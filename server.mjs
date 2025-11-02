// ─────────────────────────────────────────────
// QR Code Styling API (official repo version)
// ─────────────────────────────────────────────

import express from "express";
import { createCanvas } from "canvas";
import { JSDOM } from "jsdom";
import path from "path";
import { fileURLToPath } from "url";
import fs from "fs";

const app = express();

// ─────────────────────────────────────────────
// Load the official browser bundle from /dist
// ─────────────────────────────────────────────

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const QR_LIB_PATH = path.resolve(
  __dirname,
  "node_modules/qr-code-styling/dist/qr-code-styling.js"
);

if (!fs.existsSync(QR_LIB_PATH)) {
  console.error(
    `❌ Cannot find qr-code-styling bundle at ${QR_LIB_PATH}\nMake sure you ran: npm install "github:kozakdenys/qr-code-styling"`
  );
  process.exit(1);
}

// Initialize a fake DOM
const { window } = new JSDOM(`<!DOCTYPE html><body></body>`);
global.window = window;
global.document = window.document;
global.Image = window.Image;
global.HTMLCanvasElement = window.HTMLCanvasElement;

// Dynamically import the browser bundle into our fake DOM
const QRCodeStyling = (await import(`file://${QR_LIB_PATH}`)).default;

console.log("✅ Loaded official qr-code-styling successfully.");

// ─────────────────────────────────────────────
// Express API route
// ─────────────────────────────────────────────

app.get("/api/qr", async (req, res) => {
  const {
    data = "https://example.com",
    color = "#ffffff",
    bg = "transparent",
    size = 512,
    type = "rounded",
    eye = "extra-rounded",
    pupil = "dot",
    logo = "",
  } = req.query;

  try {
    // Create fake DOM per-request (safe for concurrency)
    const dom = new JSDOM(`<!DOCTYPE html><body></body>`);
    global.window = dom.window;
    global.document = dom.window.document;
    global.Image = dom.window.Image;
    global.HTMLCanvasElement = createCanvas(size, size).constructor;

    // Create QR Code instance
    const qr = new QRCodeStyling({
      width: parseInt(size),
      height: parseInt(size),
      data,
      image: logo || undefined,
      dotsOptions: { color, type },
      cornersSquareOptions: { color, type: eye },
      cornersDotOptions: { color, type: pupil },
      backgroundOptions: { color: bg },
    });

    // Render inside a fake container
    const container = document.createElement("div");
    await qr.append(container);

    // Export as PNG buffer
    const blob = await qr.getRawData("png");
    const buffer = Buffer.from(await blob.arrayBuffer());

    res.setHeader("Content-Type", "image/png");
    res.setHeader("Cache-Control", "public, max-age=300");
    res.end(buffer);

    // Cleanup
    delete global.window;
    delete global.document;
  } catch (err) {
    console.error("❌ QR generation failed:", err);
    res.status(500).send("QR generation failed");
  }
});

app.get("/", (_, res) =>
  res.send("✅ Official QR Code Styling API is online.")
);

// ─────────────────────────────────────────────
// Server startup
// ─────────────────────────────────────────────

const port = process.env.PORT || 3000;
app.listen(port, () =>
  console.log(`🚀 QR Code API running at http://localhost:${port}`)
);
