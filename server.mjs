import express from "express";
import { JSDOM } from "jsdom";
import { Canvas, Image as SkImage } from "skia-canvas";
import path from "path";
import { fileURLToPath } from "url";

const app = express();
const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Resolve the browser build that ships on npm:
const QR_ESM_PATH = path.resolve(
  __dirname,
  "node_modules/qr-code-styling/lib/qr-code-styling.esm.js"
);

// Build a tiny DOM + canvas environment per request (safe for concurrency)
async function withDOM(size, fn) {
  const dom = new JSDOM(`<!DOCTYPE html><body></body>`);
  const { window } = dom;

  // Minimal globals for qr-code-styling
  global.window = window;
  global.document = window.document;
  global.Image = SkImage;

  // Make document.createElement('canvas') return a Skia Canvas
  const _createElement = document.createElement.bind(document);
  document.createElement = (tag) => {
    if (tag.toLowerCase() === "canvas") {
      // Size will be set by qr-code-styling later; start with requested px
      const c = new Canvas(size, size);
      // Provide 2D context API shim expected by the lib
      c.getContext = (type) => (type === "2d" ? c.getContext("2d") : null);
      return c;
    }
    return _createElement(tag);
  };

  try {
    const result = await fn({ window, document });
    return result;
  } finally {
    // cleanup globals
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

  const W = parseInt(size, 10) || 512;

  try {
    const buffer = await withDOM(W, async () => {
      // import the browser bundle *inside* our DOM context
      const QRCodeStyling = (await import(`file://${QR_ESM_PATH}`)).default;

      // Prepare a target Skia canvas
      const canvas = new Canvas(W, W);

      // Configure QR exactly like the browser version
      const qr = new QRCodeStyling({
        width: W,
        height: W,
        data,
        image: logo || undefined,
        dotsOptions: { color, type },                         // "rounded", "dots", etc.
        cornersSquareOptions: { color, type: eye },           // "extra-rounded"
        cornersDotOptions: { color, type: pupil },            // "dot"
        backgroundOptions: { color: bg }                      // "transparent" keeps alpha
      });

      // The library supports appending to a canvas element
      await qr.append(canvas);

      // Return PNG with alpha channel
      return canvas.toBuffer("png");
    });

    res.setHeader("Content-Type", "image/png");
    res.setHeader("Cache-Control", "public, max-age=300");
    res.end(buffer);
  } catch (err) {
    console.error("QR generation failed:", err);
    res.status(500).send("QR generation failed");
  }
});

app.get("/", (_, res) => res.send("✅ Styled QR API online (official qr-code-styling + skia-canvas)"));

const port = process.env.PORT || 3000;
app.listen(port, () => console.log(`Listening on :${port}`));
