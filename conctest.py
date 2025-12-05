# concurrent_test.py
import asyncio
import httpx
import random
import time

URL = "http://127.0.0.1:8000/convert"
API_KEY = "OTTONRENT"
CONCURRENT_REQUESTS = 10  # change as needed
HTML_SNIPPETS = [
    "<h1>Hello Ayush!</h1>",
    "<p>FastAPI concurrency test</p>",
    "<div style='color:red'>Red text</div>",
]

async def make_request(client: httpx.AsyncClient, idx: int):
    html = random.choice(HTML_SNIPPETS)
    try:
        resp = await client.post(URL,
                                 json={"html": html},
                                 headers={"X-API-KEY": API_KEY, "Content-Type": "application/json"})
        print(f"[{idx}] Status code: {resp.status_code}")
    except Exception as e:
        print(f"[{idx}] Exception: {e}")

async def main():
    async with httpx.AsyncClient(timeout=120.0) as client:
        tasks = [make_request(client, i) for i in range(CONCURRENT_REQUESTS)]
        start = time.time()
        await asyncio.gather(*tasks)
        print("All done in %.2f seconds" % (time.time() - start))

if __name__ == "__main__":
    asyncio.run(main())
