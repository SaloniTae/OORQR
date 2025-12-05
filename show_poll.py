# show_pool.py
import redis
import time

r = redis.Redis.from_url("redis://localhost:6379/0", decode_responses=True)

ids = r.lrange("tokens:available", 0, -1)
print("Pool count:", len(ids))
now = int(time.time())
for tid in ids:
    h = r.hgetall(f"token:{tid}")
    if not h:
        print(tid, "-> metadata missing")
        continue
    cookie = h.get("cookie","")
    token = h.get("token","")
    uses = h.get("uses","?")
    expires_at = int(h.get("expires_at","0") or 0)
    remain = expires_at - now
    print(f"ID: {tid}  uses={uses}  expires_in={remain}s")
    print(f"  cookie=[{cookie[:60]}{'...' if len(cookie)>60 else ''}]")
    print(f"  token=[{token[:8]}...]")
    print()
