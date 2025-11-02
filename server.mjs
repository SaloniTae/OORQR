import express from "express";
import { JSDOM } from "jsdom";
import { createCanvas, Image as NapiImage } from "@napi-rs/canvas";
import path from "path";
import { fileURLToPath } from "url";
import fs from "fs";

const app = express();
const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** Try multiple likely dist paths shipped by different qr-code-styling builds */
function resolveQrStylingPath() {
  const base = path.resolve(__dirname, "node_modules/qr-code-styling");
  const candidates = [
    "lib/qr-code-styling.esm.js",
    "lib/qr-code-styling.cjs.js",
    "lib/index.esm.js",
    "lib/index.js",
    "dist/qr-code-styling.js",
    "dist/index.js",
    "index.js"
  ].map(p => path.join(base, p));

  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  throw new Error(
    `qr-code-styling bundle not found. Looked for:\n${candidates.join("\n")}\n` +
    `Installed files:\n${fs.readdirSync(base).join(", ")}`
  );
}

/** Build a tiny DOM per request (concurrency-safe) and run fn inside it */
async function withDOM(width, height, fn) {
  const dom = new JSDOM(`<!DOCTYPE html><body></body>`);
  const { window } = dom;

  // Minimal globals the lib expects
  global.window = window;
  global.document = window.document;
  global.Image = NapiImage;

  // Make document.createElement('canvas') return a @napi-rs/canvas
  const origCreateElement = document.createElement.bind(document);
  document.createElement = tag => {
    if (tag.toLowerCase() === "canvas") {
      const c = createCanvas(width, height);
      // napi-rs canvas already has getContext("2d")
      return c;
    }
    return origCreateElement(tag);
  };

  try {
    return await fn({ window, document });
  } finally {
    delete global.window;
    delete global.document;
    delete global.Image;
  }
}

app.get("/api/qr", async (req, res) => {
  const {
    data = "https://example.com",
    color = "#ffffff",
    bg = "transparent",
    size = "512",
    type = "rounded",
    eye = "extra-rounded",
    pupil = "dot",
    logo = ""
  } = req.query;

  const W = Math.max(64, parseInt(size, 10) || 512);

  try {
    const buf = await withDOM(W, W, async () => {
      const qrPath = resolveQrStylingPath();
      const QRCodeStyling = (await import(`file://${qrPath}`)).default;

      // Target canvas
      const canvas = createCanvas(W, W);

      const qr = new QRCodeStyling({
        width: W,
        height: W,
        data,
        image: logo || undefined,
        dotsOptions: { color, type },                 // "rounded", "dots", etc.
        cornersSquareOptions: { color, type: eye },   // "extra-rounded"
        cornersDotOptions: { color, type: pupil },    // "dot"
        backgroundOptions: { color: bg }              // "transparent" keeps alpha
      });

      // The lib supports appending to a canvas element
      await qr.append(canvas);

      // Return PNG with alpha
      return canvas.toBuffer("image/png");
    });

    res.setHeader("Content-Type", "image/png");
    res.setHeader("Cache-Control", "public, max-age=300");
    res.end(buf);
  } catch (err) {
    console.error("❌ QR generation failed:", err);
    res.status(500).send("QR generation failed");
  }
});

app.get("/", (_, res) =>
  res.send("✅ Styled QR API online (qr-code-styling + @napi-rs/canvas)")
);

const port = process.env.PORT || 3000;
app.listen(port, () => console.log(`Listening on :${port}`));
