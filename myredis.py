#!/usr/bin/env python3
"""
myredis.py

FastAPI app that maintains a Redis-backed prefetched pool of cookie/token pairs
fetched from STATUS_ENDPOINT and uses them to POST to POST_ENDPOINT.

Usage:
  pip install fastapi "uvicorn[standard]" httpx redis[asyncio]
  Ensure redis-server is running and reachable via REDIS_URL
  uvicorn myredis:app --host 0.0.0.0 --port 8000 --workers 1
"""

import os
import time
import uuid
import random
import asyncio
import logging
from typing import Optional, Tuple, Dict, Any

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
import httpx
import redis.asyncio as aioredis

# ---------------- CONFIG (env override) ----------------
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
AVAILABLE_LIST_KEY = os.environ.get("REDIS_AVAILABLE_KEY", "tokens:available")
TOKEN_HASH_PREFIX = os.environ.get("REDIS_TOKEN_PREFIX", "token:")
LEASE_PREFIX = os.environ.get("REDIS_LEASE_PREFIX", "token:lease:")
PREFETCH_LOCK_KEY = os.environ.get("REDIS_PREFETCH_LOCK", "tokens:lock:prefetch")

POOL_TARGET = int(os.environ.get("POOL_TARGET", "10"))         # how many prefetched tokens to maintain
TOKEN_USES = int(os.environ.get("TOKEN_USES", "5"))            # reuse count per token
PREFETCH_CONCURRENCY = int(os.environ.get("PREFETCH_CONCURRENCY", "2"))
PREFETCH_TOKEN_TTL_SECS = int(os.environ.get("PREFETCH_TOKEN_TTL_SECS", "2700"))  # expiration for prefetched tokens
PREFETCH_INTERVAL = float(os.environ.get("PREFETCH_INTERVAL", "0.5"))
# seconds to wait AFTER a successful /status prefetch before trying again
PREFETCH_SUCCESS_WAIT = float(os.environ.get("PREFETCH_SUCCESS_WAIT", "20.0"))

STATUS_ENDPOINT = os.environ.get("STATUS_ENDPOINT", "https://oorqr.onrender.com/status")
POST_ENDPOINT = os.environ.get("POST_ENDPOINT", "https://htmlcsstoimage.com/image-demo")
HOMEPAGE = os.environ.get("HOMEPAGE", "https://htmlcsstoimage.com/")

CONNECT_TIMEOUT = float(os.environ.get("HTMLCSI_CONNECT_TIMEOUT", "60"))
READ_TIMEOUT = float(os.environ.get("HTMLCSI_READ_TIMEOUT", "120"))
STATUS_FETCH_TIMEOUT = float(os.environ.get("STATUS_FETCH_TIMEOUT", "20.0"))

INTERNAL_API_KEY = os.environ.get("HTMLCSI_API_KEY", "OTTONRENT")

# global inflight limit across all instances (optional) - set to 0 to disable
GLOBAL_POST_LIMIT = int(os.environ.get("GLOBAL_POST_LIMIT", "0"))

# local per-process semaphore to protect the upstream (holds just while issuing the POST; set HOLD_FOR_STREAM=true to hold during streaming)
POST_CONCURRENCY = int(os.environ.get("POST_CONCURRENCY", "40"))
HOLD_FOR_STREAM = os.environ.get("HOLD_FOR_STREAM", "true").lower() in ("1", "true", "yes")

# retry/backoff
MAX_429_RETRIES = int(os.environ.get("MAX_429_RETRIES", "3"))
INITIAL_BACKOFF = float(os.environ.get("INITIAL_BACKOFF", "0.5"))

# status fetch retry config
STATUS_FETCH_RETRIES = int(os.environ.get("STATUS_FETCH_RETRIES", "1"))
STATUS_FETCH_RETRY_BACKOFF = float(os.environ.get("STATUS_FETCH_RETRY_BACKOFF", "1.0"))

# owner id (used when setting leases in Redis)
OWNER_ID = os.environ.get("OWNER_ID", f"py-{uuid.uuid4().hex[:8]}")

# user agents / locales
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
]
LOCALES = ["en-US,en;q=0.9", "en-GB,en;q=0.9", "en-IN,en;q=0.9"]

HEALTH_POLL_INTERVAL = float(os.environ.get("HEALTH_POLL_INTERVAL", "30.0"))

# logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("convert-redis-pool")

# ---------------- FastAPI app & clients ----------------
app = FastAPI()

limits = httpx.Limits(max_keepalive_connections=200, max_connections=1000)
default_timeout = httpx.Timeout(connect=CONNECT_TIMEOUT, read=READ_TIMEOUT, write=READ_TIMEOUT, pool=CONNECT_TIMEOUT)
http_client = httpx.AsyncClient(timeout=default_timeout, limits=limits, http2=True)

# redis client (async)
redis = aioredis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)

# local semaphore for outbound posts
_post_semaphore = asyncio.Semaphore(POST_CONCURRENCY)

# in-process lock to ensure only one coroutine calls /status at a time
_status_call_lock = asyncio.Lock()

# Flag: when set -> upstream /status considered unavailable (5xx observed)
_status_unavailable = asyncio.Event()
_health_probe_task: Optional[asyncio.Task] = None

# Push-if-not-exists Lua (LRANGE-based check to be compatible with older Redis)
PUSH_IF_NOT_EXISTS_LUA = r'''
-- KEYS[1] = available_list_key
-- ARGV[1] = id
local listkey = KEYS[1]
local id = ARGV[1]
local vals = redis.call("LRANGE", listkey, 0, -1)
for i = 1, #vals do
  if vals[i] == id then
    return 0
  end
end
redis.call("LPUSH", listkey, id)
return 1
'''
_push_if_not_exists_sha = None


# add near existing globals (after _push_if_not_exists_sha = None)
MULTI_LEASE_LUA = r'''
-- KEYS[1] = available_list_key
-- KEYS[2] = token_hash_prefix
-- ARGV[1] = now_ts
-- ARGV[2] = max_scan
local listkey = KEYS[1]
local hprefix = KEYS[2]
local now = tonumber(ARGV[1])
local maxscan = tonumber(ARGV[2]) or 100
local ids = redis.call("LRANGE", listkey, 0, -1)
if not ids or #ids == 0 then
  return nil
end
local scanned = 0
for i = 1, #ids do
  if scanned >= maxscan then break end
  local id = ids[i]
  scanned = scanned + 1
  local hkey = hprefix .. id
  if redis.call("EXISTS", hkey) == 1 then
    local expires = redis.call("HGET", hkey, "expires_at")
    if expires then
      local ex = tonumber(expires)
      if ex and ex > now then
        local new_uses = tonumber(redis.call("HINCRBY", hkey, "uses", -1))
        if new_uses >= 0 then
          local cookie = redis.call("HGET", hkey, "cookie") or ""
          local token = redis.call("HGET", hkey, "token") or ""
          return {id, cookie, token, tostring(new_uses)}
        else
          redis.call("HINCRBY", hkey, "uses", 1)
        end
      end
    end
  end
end
return nil
'''
_multi_lease_sha = None


# ---------------- Redis Lua scripts ----------------
POP_DECR_LEASE_LUA = r'''
-- KEYS[1] = available_list_key
-- KEYS[2] = token_hash_prefix
-- KEYS[3] = lease_prefix
-- ARGV[1] = owner_id
-- ARGV[2] = lease_ms
-- ARGV[3] = now_ts (seconds)
for i=1,10 do
  local id = redis.call("RPOP", KEYS[1])
  if not id then
    return nil
  end
  local hkey = KEYS[2] .. id
  local expires = redis.call("HGET", hkey, "expires_at")
  if expires then
    local ex = tonumber(expires)
    if ex and ex <= tonumber(ARGV[3]) then
      redis.call("DEL", hkey)
    else
      local leasekey = KEYS[3] .. id
      local ok = redis.call("SET", leasekey, ARGV[1], "NX", "PX", ARGV[2])
      if not ok then
        redis.call("LPUSH", KEYS[1], id)
        return nil
      end
      if redis.call("EXISTS", hkey) == 0 then
        redis.call("DEL", leasekey)
        return nil
      end
      local new_uses = tonumber(redis.call("HINCRBY", hkey, "uses", -1))
      local cookie = redis.call("HGET", hkey, "cookie")
      local token = redis.call("HGET", hkey, "token")
      if new_uses > 0 then
        redis.call("LPUSH", KEYS[1], id)
      else
        redis.call("DEL", hkey)
      end
      return {id, cookie or "", token or "", tostring(new_uses)}
    end
  else
    local leasekey = KEYS[3] .. id
    local ok = redis.call("SET", leasekey, ARGV[1], "NX", "PX", ARGV[2])
    if not ok then
      redis.call("LPUSH", KEYS[1], id)
      return nil
    end
    if redis.call("EXISTS", hkey) == 0 then
      redis.call("DEL", leasekey)
      return nil
    end
    local new_uses = tonumber(redis.call("HINCRBY", hkey, "uses", -1))
    local cookie = redis.call("HGET", hkey, "cookie")
    local token = redis.call("HGET", hkey, "token")
    if new_uses > 0 then
      redis.call("LPUSH", KEYS[1], id)
    else
      redis.call("DEL", hkey)
    end
    return {id, cookie or "", token or "", tostring(new_uses)}
  end
end
return nil
'''

RELEASE_LUA = r'''
-- KEYS[1] = available_list_key
-- KEYS[2] = token_hash_prefix
-- KEYS[3] = lease_prefix
-- ARGV[1] = id
-- ARGV[2] = used_ok ("1" or "0")
-- ARGV[3] = owner
local id = ARGV[1]
local used_ok = ARGV[2]
local owner = ARGV[3]
local leasekey = KEYS[3] .. id
local curowner = redis.call("GET", leasekey)
if not curowner or curowner ~= owner then
  return 0
end
local hkey = KEYS[2] .. id
if used_ok == "1" then
  if redis.call("EXISTS", hkey) == 1 then
    local uses = tonumber(redis.call("HGET", hkey, "uses") or "0")
    if uses > 0 then
      redis.call("LPUSH", KEYS[1], id)
    else
      redis.call("DEL", hkey)
    end
  end
else
  redis.call("DEL", hkey)
end
redis.call("DEL", leasekey)
return 1
'''

GLOBAL_INFLIGHT_LUA = r'''
-- KEYS[1] = inflight_key
-- ARGV[1] = limit
local cur = tonumber(redis.call("INCR", KEYS[1]))
if cur > tonumber(ARGV[1]) then
  redis.call("DECR", KEYS[1])
  return 0
end
return 1
'''
GLOBAL_INFLIGHT_RELEASE_LUA = r'''
-- KEYS[1] = inflight_key
redis.call("DECR", KEYS[1])
return 1
'''


CLEAN_LIST_LUA = r'''
-- KEYS[1] = available_list_key
-- ARGV[1] = token_hash_prefix
-- ARGV[2] = lease_prefix
-- ARGV[3] = now_ts (seconds)

local listkey = KEYS[1]
local hprefix = ARGV[1]
local leaseprefix = ARGV[2]
local now = tonumber(ARGV[3])

local ids = redis.call("LRANGE", listkey, 0, -1)
if not ids or #ids == 0 then
  return 0
end

local seen = {}
local keep = {}
for i = 1, #ids do
  local id = ids[i]
  if not seen[id] then
    local hkey = hprefix .. id
    local expires = redis.call("HGET", hkey, "expires_at")
    if not expires then
      -- hash missing -> skip (drop id)
    else
      local ex = tonumber(expires)
      if ex and ex > now then
        table.insert(keep, id)
        seen[id] = true
      else
        -- expired -> delete hash and lease as cleanup
        redis.call("DEL", hkey)
        redis.call("DEL", leaseprefix .. id)
      end
    end
  else
    -- duplicate occurrence -> skip (we keep first)
  end
end

-- replace the list atomically: delete and RPUSH keep[]
redis.call("DEL", listkey)
if #keep > 0 then
  for i = 1, #keep do
    redis.call("RPUSH", listkey, keep[i])
  end
end

return #keep
'''


        
        
# ---------------- helper utils ----------------
def pick_random_user_agent() -> str:
    return random.choice(USER_AGENTS)

def generate_minimal_headers(cookie_str: Optional[str], token: Optional[str]) -> Dict[str, str]:
    ua_text = pick_random_user_agent()
    headers = {
        "Authority": HOMEPAGE,
        "User-Agent": ua_text,
        "Accept": "*/*",
        "Accept-Language": random.choice(LOCALES),
        "Content-Type": "application/json",
        "Origin": HOMEPAGE,
        "Referer": HOMEPAGE,
        "DNT": "1",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    if cookie_str:
        headers["Cookie"] = cookie_str
    if token:
        headers["requestverificationtoken"] = token
    return headers

# ---------------- robust status fetch (with health probe on 5xx) ----------------
async def _start_health_probe_once():
    global _health_probe_task
    if _health_probe_task and not _health_probe_task.done():
        return
    async def _probe_loop():
        logger.info("health_probe: started, will poll ping every %.1fs", HEALTH_POLL_INTERVAL)
        # derive ping url from STATUS_ENDPOINT ideally
        if STATUS_ENDPOINT.endswith("/status"):
            ping_url = STATUS_ENDPOINT[:-7] + "/ping"
        else:
            ping_url = STATUS_ENDPOINT.rstrip("/") + "/ping"
        while _status_unavailable.is_set():
            try:
                call_timeout = httpx.Timeout(connect=5.0, read=5.0, write=5.0, pool=5.0)
                resp = await http_client.get(ping_url, timeout=call_timeout)
                if resp.status_code == 200:
                    logger.info("health_probe: ping returned 200, clearing unavailable flag")
                    _status_unavailable.clear()
                    return
                else:
                    logger.info("health_probe: ping returned %s; still down", resp.status_code)
            except Exception as e:
                logger.info("health_probe: ping error (still down): %s", e)
            await asyncio.sleep(HEALTH_POLL_INTERVAL)
        logger.info("health_probe: exiting (status marked available)")
    _health_probe_task = asyncio.create_task(_probe_loop())

async def fetch_status_once(timeout: float = STATUS_FETCH_TIMEOUT) -> Tuple[str, Optional[str]]:
    """
    Call STATUS_ENDPOINT with a per-call timeout and small retry on transient errors.
    If upstream returns 5xx/502/503, mark it unavailable and start health probe.
    """
    attempt = 0
    last_exc = None
    while attempt <= STATUS_FETCH_RETRIES:
        attempt += 1
        call_timeout = httpx.Timeout(connect=timeout, read=timeout, write=timeout, pool=timeout)
        try:
            resp = await http_client.get(STATUS_ENDPOINT, timeout=call_timeout)
            # If server error -> set unavailable and start health probe
            if resp.status_code >= 500:
                last_exc = Exception(f"Server error {resp.status_code}")
                logger.warning("fetch_status_once attempt=%d got server error %d", attempt, resp.status_code)
                _status_unavailable.set()
                await _start_health_probe_once()
                # raise to outer handler
                raise last_exc
            resp.raise_for_status()
            data = resp.json()
            cookies = data.get("cookies", []) or []
            cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies) if cookies else ""
            token = data.get("requestVerificationToken") or data.get("__RequestVerificationToken") or data.get("RequestVerificationToken")
            return cookie_str, token
        except httpx.ReadTimeout as e:
            last_exc = e
            logger.warning("fetch_status_once attempt=%d timed out after %.1fs", attempt, timeout)
        except httpx.RequestError as e:
            last_exc = e
            logger.warning("fetch_status_once attempt=%d request error: %s", attempt, e)
        except Exception as e:
            last_exc = e
            logger.exception("fetch_status_once attempt=%d unexpected error: %s", attempt, e)

        # if upstream marked unavailable, quit earlier
        if _status_unavailable.is_set():
            raise last_exc or Exception("status currently unavailable")

        if attempt <= STATUS_FETCH_RETRIES:
            backoff = STATUS_FETCH_RETRY_BACKOFF * (2 ** (attempt - 1))
            jitter = random.random() * (backoff * 0.2)
            await asyncio.sleep(min(backoff + jitter, max(1.0, backoff + jitter)))

    logger.error("fetch_status_once failed after %d attempts: %s", attempt, last_exc)
    raise last_exc or Exception("fetch_status_once failed")

# ---------------- Redis atomic ops ----------------
async def load_lua_scripts():
    """
    Load Lua scripts into Redis, but defend against per-script failures.
    Returns tuple: pop_sha, rel_sha, push_sha, multi_sha, inflight_sha, inflight_rel_sha
    (any of these may be None if loading failed).
    """
    pop_sha = rel_sha = push_sha = multi_sha = None
    inflight_sha = inflight_rel_sha = None

    try:
        pop_sha = await redis.script_load(POP_DECR_LEASE_LUA)
    except Exception as e:
        logger.exception("Failed to load POP_DECR_LEASE_LUA: %s", e)

    try:
        rel_sha = await redis.script_load(RELEASE_LUA)
    except Exception as e:
        logger.exception("Failed to load RELEASE_LUA: %s", e)

    try:
        push_sha = await redis.script_load(PUSH_IF_NOT_EXISTS_LUA)
    except Exception as e:
        logger.exception("Failed to load PUSH_IF_NOT_EXISTS_LUA: %s", e)

    try:
        multi_sha = await redis.script_load(MULTI_LEASE_LUA)
    except Exception as e:
        logger.exception("Failed to load MULTI_LEASE_LUA: %s", e)
        multi_sha = None

    if GLOBAL_POST_LIMIT > 0:
        try:
            inflight_sha = await redis.script_load(GLOBAL_INFLIGHT_LUA)
        except Exception as e:
            logger.exception("Failed to load GLOBAL_INFLIGHT_LUA: %s", e)
        try:
            inflight_rel_sha = await redis.script_load(GLOBAL_INFLIGHT_RELEASE_LUA)
        except Exception as e:
            logger.exception("Failed to load GLOBAL_INFLIGHT_REL_LUA: %s", e)

    return pop_sha, rel_sha, push_sha, multi_sha, inflight_sha, inflight_rel_sha

async def lease_token_from_redis(pop_sha: str, lease_ms: int = 60000) -> Optional[Dict[str, Any]]:
    now_ts = int(time.time())
    try:
        res = await redis.evalsha(pop_sha, 3, AVAILABLE_LIST_KEY, TOKEN_HASH_PREFIX, LEASE_PREFIX, OWNER_ID, str(lease_ms), str(now_ts))
    except Exception:
        res = await redis.eval(POP_DECR_LEASE_LUA, 3, AVAILABLE_LIST_KEY, TOKEN_HASH_PREFIX, LEASE_PREFIX, OWNER_ID, str(lease_ms), str(now_ts))
    if not res:
        return None
    try:
        tid = str(res[0])
        cookie = str(res[1]) if res[1] is not None else ""
        token = str(res[2]) if res[2] is not None else ""
        uses_left = int(res[3])
        return {"id": tid, "cookie": cookie, "token": token, "uses_left": uses_left}
    except Exception:
        logger.exception("unexpected lua lease result: %r", res)
        return None


async def multi_lease_one_use(multi_sha: str, max_scan: int = None) -> Optional[Dict[str, Any]]:
    """
    Atomically try to take one 'use' from any available token (without exclusive lease).
    Returns dict {id, cookie, token, uses_left} or None.
    """
    now_ts = int(time.time())
    max_scan = max_scan or POOL_TARGET
    try:
        res = await redis.evalsha(multi_sha, 2, AVAILABLE_LIST_KEY, TOKEN_HASH_PREFIX, str(now_ts), str(max_scan))
    except Exception:
        try:
            res = await redis.eval(MULTI_LEASE_LUA, 2, AVAILABLE_LIST_KEY, TOKEN_HASH_PREFIX, str(now_ts), str(max_scan))
        except Exception as e:
            logger.exception("multi_lease eval failed: %s", e)
            return None
    if not res:
        return None
    try:
        tid = str(res[0])
        cookie = str(res[1]) if res[1] is not None else ""
        token = str(res[2]) if res[2] is not None else ""
        uses_left = int(res[3])
        return {"id": tid, "cookie": cookie, "token": token, "uses_left": uses_left}
    except Exception:
        logger.exception("unexpected multi_lease result: %r", res)
        return None
        

async def release_token_to_redis(rel_sha: str, tid: str, used_ok: bool):
    used_flag = "1" if used_ok else "0"
    try:
        res = await redis.evalsha(rel_sha, 3, AVAILABLE_LIST_KEY, TOKEN_HASH_PREFIX, LEASE_PREFIX, tid, used_flag, OWNER_ID)
    except Exception:
        res = await redis.eval(RELEASE_LUA, 3, AVAILABLE_LIST_KEY, TOKEN_HASH_PREFIX, LEASE_PREFIX, tid, used_flag, OWNER_ID)
    return bool(res)

async def try_acquire_global_inflight(inflight_sha: Optional[str], inflight_key: str, limit: int) -> bool:
    if limit <= 0 or inflight_sha is None:
        return True
    try:
        res = await redis.evalsha(inflight_sha, 1, inflight_key, str(limit))
    except Exception:
        res = await redis.eval(GLOBAL_INFLIGHT_LUA, 1, inflight_key, str(limit))
    return int(res) == 1

async def release_global_inflight(inflight_rel_sha: Optional[str], inflight_key: str):
    if inflight_rel_sha is None:
        return
    try:
        await redis.evalsha(inflight_rel_sha, 1, inflight_key)
    except Exception:
        await redis.eval(GLOBAL_INFLIGHT_RELEASE_LUA, 1, inflight_key)

# ---------------- Prefetcher (single fetch per lock acquisition) ----------------
async def prefetch_worker(worker_id: int):
    global _push_if_not_exists_sha
    logger.info("prefetch_worker %d started (target=%d)", worker_id, POOL_TARGET)
    while True:
        got_lock = False
        acquired = False
        try:
            # if upstream marked unavailable, sleep until probe clears it
            if _status_unavailable.is_set():
                logger.info("prefetch_worker %d: upstream status unavailable, sleeping %.1fs", worker_id, HEALTH_POLL_INTERVAL)
                await asyncio.sleep(HEALTH_POLL_INTERVAL)
                continue

            llen = await redis.llen(AVAILABLE_LIST_KEY)
            if llen >= POOL_TARGET:
                await asyncio.sleep(PREFETCH_INTERVAL)
                continue

            # try to acquire redis prefetch lock (only one process should fill)
            try:
                got_lock = await redis.set(PREFETCH_LOCK_KEY, OWNER_ID, nx=True, px=15000)
            except Exception as e:
                logger.warning("prefetch_worker %d: redis.set prefetch lock error: %s", worker_id, e)
                got_lock = False

            if not got_lock:
                # someone else is filling; yield and retry later
                await asyncio.sleep(0.3)
                continue

            # We own the redis lock now.
            try:
                # re-check under lock
                llen = await redis.llen(AVAILABLE_LIST_KEY)
                if llen >= POOL_TARGET:
                    await asyncio.sleep(0.1)
                    continue

                try:
                    acquired = await asyncio.wait_for(_status_call_lock.acquire(), timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning("prefetch_worker %d: could not acquire in-process status lock; skipping", worker_id)
                    continue

                try:
                    # perform a single /status fetch
                    cookie, token = await fetch_status_once(timeout=STATUS_FETCH_TIMEOUT)
                except Exception as e:
                    logger.warning("prefetch_worker %d: status fetch failed: %s", worker_id, e)
                    # on failure, just continue (we will release redis lock in finally below)
                    continue

                # on success: store token and push id (avoid duplicates)
                tid = uuid.uuid4().hex
                hkey = TOKEN_HASH_PREFIX + tid
                expires_at = int(time.time()) + PREFETCH_TOKEN_TTL_SECS
                await redis.hset(hkey, mapping={
                    "cookie": cookie or "",
                    "token": token or "",
                    "uses": str(TOKEN_USES),
                    "created_at": str(int(time.time())),
                    "expires_at": str(expires_at),
                })
                try:
                    await redis.expire(hkey, PREFETCH_TOKEN_TTL_SECS + 5)
                except Exception:
                    pass

                # push id only if not already present (atomic)
                if not _push_if_not_exists_sha:
                    try:
                        _push_if_not_exists_sha = await redis.script_load(PUSH_IF_NOT_EXISTS_LUA)
                    except Exception:
                        _push_if_not_exists_sha = None
                try:
                    if _push_if_not_exists_sha:
                        await redis.evalsha(_push_if_not_exists_sha, 1, AVAILABLE_LIST_KEY, tid)
                    else:
                        await redis.eval(PUSH_IF_NOT_EXISTS_LUA, 1, AVAILABLE_LIST_KEY, tid)
                except Exception:
                    # fallback: naive LPUSH
                    await redis.lpush(AVAILABLE_LIST_KEY, tid)

                pool_now = await redis.llen(AVAILABLE_LIST_KEY)
                logger.info("prefetch_worker %d: prefetched id=%s uses=%d expires_at=%d pool=%d",
                            worker_id, tid[:8], TOKEN_USES, expires_at, pool_now)

                # wait after success to avoid hammering upstream
                await asyncio.sleep(PREFETCH_SUCCESS_WAIT)
                # small pause to avoid tight loop
                await asyncio.sleep(0.05)
                continue

            finally:
                # always release the in-process status lock if we acquired it
                if acquired and _status_call_lock.locked():
                    try:
                        _status_call_lock.release()
                    except RuntimeError:
                        pass

        except Exception as e:
            logger.exception("prefetch_worker %d exception: %s", worker_id, e)
            await asyncio.sleep(1.0)
        finally:
            # IMPORTANT: only delete the redis prefetch lock if *we* set it
            if got_lock:
                try:
                    val = await redis.get(PREFETCH_LOCK_KEY)
                    # safe-delete only when it is still ours
                    if val == OWNER_ID:
                        await redis.delete(PREFETCH_LOCK_KEY)
                except Exception:
                    # swallow delete error (someone else might already removed it)
                    pass
            await asyncio.sleep(0.2)

# ---------------- maintenance tasks ----------------
async def scrub_expired_and_duplicates_loop():
    """
    Safe periodic scrub:
      - Removes expired token hashes and leases
      - Deduplicates tokens:available keeping the *first* occurrence (original order)
      - Operates atomically using Lua
    """
    while True:
        try:
            now = int(time.time())
            try:
                res = await redis.eval(CLEAN_LIST_LUA, 1, AVAILABLE_LIST_KEY, TOKEN_HASH_PREFIX, LEASE_PREFIX, str(now))
                logger.info("scrub: cleaned pool -> kept=%s entries", res)
            except Exception as e:
                logger.exception("scrub loop lua exec failed: %s", e)
        except Exception as e:
            logger.exception("scrub loop error: %s", e)
        await asyncio.sleep(30)

# ---------------- Routes ----------------
HOP_BY_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-encoding",
}

@app.get("/ping")
async def ping():
    return {"status": "ok", "owner": OWNER_ID}

@app.get("/health")
async def health():
    pool_len = await redis.llen(AVAILABLE_LIST_KEY)
    return JSONResponse({"status": "ok", "pool": pool_len, "pool_target": POOL_TARGET, "owner": OWNER_ID, "upstream_unavailable": _status_unavailable.is_set()})

# ---------------- Startup / shutdown ----------------
_startup_done = False
@app.on_event("startup")
async def on_startup():
    global _startup_done, _pop_sha, _rel_sha, _push_if_not_exists_sha, _inflight_sha, _inflight_rel_sha
    if _startup_done:
        return
    try:
        await redis.ping()
    except Exception as e:
        logger.exception("Failed to connect to Redis at %s: %s", REDIS_URL, e)
        raise
    _pop_sha, _rel_sha, _push_if_not_exists_sha, _multi_lease_sha, _inflight_sha, _inflight_rel_sha = await load_lua_scripts()
    logger.info("Loaded Lua scripts. pop_sha=%s rel_sha=%s push_sha=%s multi_sha=%s inflight_sha=%s", _pop_sha, _rel_sha, _push_if_not_exists_sha, _multi_lease_sha, _inflight_sha)
    for i in range(max(1, PREFETCH_CONCURRENCY)):
        asyncio.create_task(prefetch_worker(i + 1))
    # background maintenance scrub
    asyncio.create_task(scrub_expired_and_duplicates_loop())
    _startup_done = True
    logger.info("Startup complete. Owner=%s", OWNER_ID)

@app.on_event("shutdown")
async def on_shutdown():
    try:
        await http_client.aclose()
    except Exception:
        pass
    try:
        await redis.close()
    except Exception:
        pass

# ---------------- Main convert endpoint ----------------
@app.post("/convert")
async def convert(request: Request):
    global _multi_lease_sha  # <- fix: use the module-level variable

    # API key
    client_key = request.headers.get("X-API-KEY", "")
    if not client_key or client_key != INTERNAL_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-KEY")

    body = await request.json()
    html = body.get("html")
    if not html:
        raise HTTPException(status_code=400, detail="Missing 'html' field")

    forward_payload = {"html": html}
    for key in ("selector", "full_screen", "render_when_ready", "color_scheme", "timezone",
                "block_consent_banners", "viewport_width", "viewport_height", "device_scale", "css", "url"):
        if key in body:
            forward_payload[key] = body[key]

    # --- Acquire token (exclusive pop -> multi-lease -> on-demand) ---
    token_info = None
    try:
        token_info = await lease_token_from_redis(_pop_sha, lease_ms=int(os.environ.get("LEASE_MS", "60000")))
        if token_info:
            logger.info("lease_token_from_redis: got id=%s uses_left=%d", (token_info.get("id") or "")[:8], token_info.get("uses_left"))
    except Exception as e:
        logger.warning("lease_token_from_redis error: %s", e)
        token_info = None

    from_pool = True
    _multi_lease_used = False

    # if exclusive lease missed, try multi-lease fallback
    if not token_info:
        try:
            pool_size = await redis.llen(AVAILABLE_LIST_KEY)
        except Exception:
            pool_size = -1

        logger.info("exclusive lease miss -> attempting multi-lease fallback (pool_size=%d) multi_sha=%s",
                    pool_size, (_multi_lease_sha or "<none>"))

        # lazy-load the multi-lease script if it wasn't loaded at startup
        if _multi_lease_sha is None:
            try:
                _multi_lease_sha = await redis.script_load(MULTI_LEASE_LUA)
                logger.info("lazy-loaded MULTI_LEASE_LUA sha=%s", _multi_lease_sha)
            except Exception as e:
                logger.warning("lazy load of MULTI_LEASE_LUA failed: %s", e)
                _multi_lease_sha = None

        # attempt multi-lease only if we have a SHA
        try:
            if _multi_lease_sha is not None:
                token_info = await multi_lease_one_use(_multi_lease_sha, max_scan=POOL_TARGET)
                if token_info:
                    _multi_lease_used = True
                    from_pool = True
                    logger.info("multi-lease: acquired id=%s uses_left=%d", (token_info.get("id") or "")[:8], token_info.get("uses_left"))
                else:
                    logger.info("multi-lease: returned None (no candidate found or expired)")
            else:
                logger.info("multi-lease SHA not available; skipping multi-lease attempt")
        except Exception as e:
            logger.exception("multi-lease attempt error: %s", e)
            token_info = None

    # If still no token, fallback to on-demand fetch
    if not token_info:
        from_pool = False
        logger.info("No pool token available; performing on-demand fetch_status")
        try:
            cookie, token = await fetch_status_once(timeout=STATUS_FETCH_TIMEOUT)
            token_info = {"id": None, "cookie": cookie or "", "token": token or "", "uses_left": 1}
            logger.info("on-demand /status: got token (uses_left=1)")
        except Exception as e:
            logger.exception("On-demand /status failed:")
            raise HTTPException(status_code=502, detail="Failed to obtain auth token")

    # Build headers and do upstream POST + stream back
    headers = generate_minimal_headers(token_info["cookie"], token_info["token"])

    inflight_key = "tokens:inflight"
    got_global = False
    try:
        await asyncio.wait_for(_post_semaphore.acquire(), timeout=30.0)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="Server busy (could not acquire post slot)")

    try:
        if GLOBAL_POST_LIMIT > 0:
            got_global = await try_acquire_global_inflight(_inflight_sha, inflight_key, GLOBAL_POST_LIMIT)
            if not got_global:
                _post_semaphore.release()
                raise HTTPException(status_code=429, detail="Too many concurrent upstream requests (global limit)")

        # POST with retry logic
        attempt = 0
        last_exc = None
        resp = None
        upstream_cm = None

        while attempt <= MAX_429_RETRIES:
            attempt += 1
            try:
                call_timeout = httpx.Timeout(connect=CONNECT_TIMEOUT, read=READ_TIMEOUT, write=READ_TIMEOUT, pool=CONNECT_TIMEOUT)
                upstream_cm = http_client.stream("POST", POST_ENDPOINT, headers=headers, json=forward_payload, timeout=call_timeout)
                resp = await upstream_cm.__aenter__()  # enter async context
            except httpx.RequestError as e:
                last_exc = e
                logger.exception("Upstream request error attempt=%d: %s", attempt, e)
                try:
                    if upstream_cm is not None:
                        await upstream_cm.__aexit__(None, None, None)
                except Exception:
                    pass
                await asyncio.sleep(min(INITIAL_BACKOFF * (2 ** (attempt - 1)), 10.0))
                continue

            # handle 429 with Retry-After respect
            if resp.status_code == 429 and attempt <= MAX_429_RETRIES:
                ra = resp.headers.get("Retry-After")
                wait = None
                if ra:
                    try:
                        wait = int(float(ra))
                    except Exception:
                        pass
                if wait is None:
                    wait = min(INITIAL_BACKOFF * (2 ** (attempt - 1)), 10.0) + random.random() * 0.2
                logger.warning("Upstream returned 429 attempt=%d; waiting %.2fs", attempt, wait)
                try:
                    await resp.aread()
                except Exception:
                    pass
                try:
                    await upstream_cm.__aexit__(None, None, None)
                except Exception:
                    pass
                resp = None
                upstream_cm = None
                await asyncio.sleep(wait)
                continue

            break  # got non-429 response -> proceed

        if resp is None:
            raise HTTPException(status_code=502, detail=f"Failed to contact upstream: {last_exc}")

        status_code = resp.status_code
        content_type = resp.headers.get("Content-Type", "application/octet-stream")
        forwarded_headers = {k: v for k, v in resp.headers.items() if k.lower() not in HOP_BY_HOP_HEADERS}

        async def stream_gen():
            used_ok = (status_code == 200)
            try:
                try:
                    async for chunk in resp.aiter_bytes(chunk_size=8192):
                        if chunk:
                            yield chunk
                except httpx.StreamClosed:
                    logger.warning("Upstream StreamClosed while streaming")
                    return
                except Exception as e:
                    logger.exception("Exception streaming from upstream: %s", e)
                    raise
            finally:
                # ensure closing context
                try:
                    if upstream_cm is not None:
                        await upstream_cm.__aexit__(None, None, None)
                    else:
                        try:
                            await resp.aclose()
                        except Exception:
                            pass
                except Exception:
                    pass

                if got_global:
                    try:
                        await release_global_inflight(_inflight_rel_sha, inflight_key)
                    except Exception:
                        logger.exception("release_global_inflight failed")

                if HOLD_FOR_STREAM:
                    try:
                        _post_semaphore.release()
                    except Exception:
                        pass

                # Only release exclusive-pop leases; multi-lease already decremented uses
                if from_pool and token_info.get("id") and (not _multi_lease_used):
                    try:
                        ok = await release_token_to_redis(_rel_sha, token_info["id"], used_ok)
                        if not ok:
                            logger.warning("Release script returned false for id=%s", token_info["id"])
                    except Exception:
                        logger.exception("Failed to release token id=%s", token_info.get("id"))

        if not HOLD_FOR_STREAM:
            try:
                _post_semaphore.release()
            except Exception:
                pass

        return StreamingResponse(stream_gen(), status_code=status_code, media_type=content_type, headers=forwarded_headers)

    except HTTPException:
        if got_global:
            try:
                await release_global_inflight(_inflight_rel_sha, inflight_key)
            except Exception:
                pass
        try:
            _post_semaphore.release()
        except Exception:
            pass
        raise
    except Exception as e:
        logger.exception("Unhandled exception in /convert: %s", e)
        if got_global:
            try:
                await release_global_inflight(_inflight_rel_sha, inflight_key)
            except Exception:
                pass
        try:
            _post_semaphore.release()
        except Exception:
            pass

        # If this was a multi-lease (we decremented uses directly) and the request failed,
        # try to increment uses back (best-effort).
        try:
            if _multi_lease_used and token_info and token_info.get("id"):
                try:
                    await redis.hincrby(TOKEN_HASH_PREFIX + token_info["id"], "uses", 1)
                    logger.info("restored one 'use' for id=%s after failure", (token_info["id"] or "")[:8])
                except Exception:
                    logger.exception("failed to restore use for id=%s", (token_info["id"] or "")[:8])
        except Exception:
            pass

        # If it was an exclusive lease and we didn't use it, try to mark invalid via release script
        if token_info and token_info.get("id") and (not _multi_lease_used):
            try:
                await release_token_to_redis(_rel_sha, token_info["id"], False)
            except Exception:
                pass
        raise HTTPException(status_code=500, detail="Internal server error")
