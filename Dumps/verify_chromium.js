// verify_chromium.js
const puppeteer = require('puppeteer-core');
const fs = require('fs');

const CHROME_PATH = '/usr/bin/google-chrome'; // adjust if needed

(async () => {
  try {
    console.log("🔍 Checking if Chromium exists at:", CHROME_PATH);

    if (!fs.existsSync(CHROME_PATH)) {
      console.error(`❌ ERROR: Chromium not found at ${CHROME_PATH}`);
      process.exit(1);
    }

    console.log("✅ Chromium binary found!");

    console.log("🚀 Launching Chromium...");
    const browser = await puppeteer.launch({
      executablePath: CHROME_PATH,
      headless: true,
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-gpu',
      ],
      timeout: 60000,
    });

    console.log("✅ Chromium launched successfully!");

    const page = await browser.newPage();
    console.log("🌐 Navigating to https://example.com ...");

    await page.goto('https://google.com', { waitUntil: 'domcontentloaded' });

    const title = await page.title();
    console.log(`📄 Page title is: "${title}"`);

    if (title.toLowerCase().includes('example')) {
      console.log("🎉 SUCCESS: Chromium navigated successfully!");
    } else {
      console.log("⚠️ WARNING: Chromium opened, but navigation failed.");
    }

    await browser.close();
    console.log("🔒 Browser closed.");
  } catch (err) {
    console.error("❌ ERROR: Chromium failed to launch or navigate.");
    console.error(err);
    process.exit(1);
  }
})();
