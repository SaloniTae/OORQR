import asyncio
from pyppeteer import launch

CHROME_PATH = "/usr/bin/chromium-browser"

async def main():
    try:
        print("🚀 Launching Chromium...")

        browser = await launch(
            executablePath=CHROME_PATH,
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--single-process",
                "--disable-software-rasterizer",
                "--disable-extensions",
                "--disable-translate",
                "--disable-background-networking",
                "--disable-sync",
            ],
        )

        page = await browser.newPage()
        print("🌐 Navigating to Google...")

        await page.goto("https://www.google.com", {"waitUntil": "domcontentloaded"})
        title = await page.title()
        print(f"📄 Page title: {title}")

        await browser.close()
        print("🎉 SUCCESS: Chromium navigated successfully!")
    except Exception as e:
        print("❌ ERROR: Chromium failed to launch or navigate.")
        print(e)

asyncio.get_event_loop().run_until_complete(main())
