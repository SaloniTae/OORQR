import express from "express";
import { JSDOM } from "jsdom";
import { createCanvas, Image as NapiImage } from "@napi-rs/canvas";
import path from "path";
import { fileURLToPath } from "url";

const app = express();
const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Resolve ESM build from npm
const QR_ESM_PATH = path.resolve(
  __dirname,
  "node_modules/qr-code-styling/lib/qr-code-styling.esm.js"
);

async function withDOM(size, fn) {
  const dom = new JSDOM(`<!DOCTYPE html><body></body>`);
  const { window } = dom;
  global.window = window;
  global.document = window.document;
  global.Image = NapiImage;

  // Patch createElement to return a napi-canvas when asked for 'canvas'
  const origCreateElement = document.createElement.bind(document);
  document.createElement = (tag) => {
    if (tag.toLowerCase() === "canvas") {
      const c = createCanvas(size, size);
      c.getContext = (t) => (t === "2d" ? c.getContext("2d") : null);
      return c;
    }
    return origCreateElement(tag);
  };

  try {
    const result = await fn({ window, document });
    return result;
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

  const W = parseInt(size, 10) || 512;

  try {
    const buffer = await withDOM(W, async () => {
      const QRCodeStyling = (await import(`file://${QR_ESM_PATH}`)).default;

      const canvas = createCanvas(W, W);
      const qr = new QRCodeStyling({
        width: W,
        height: W,
        data,
        image: logo || undefined,
        dotsOptions: { color, type },
        cornersSquareOptions: { color, type: eye },
        cornersDotOptions: { color, type: pupil },
        backgroundOptions: { color: bg }
      });

      await qr.append(canvas);
      return canvas.toBuffer("image/png");
    });

    res.setHeader("Content-Type", "image/png");
    res.setHeader("Cache-Control", "public, max-age=300");
    res.end(buffer);
  } catch (err) {
    console.error("❌ QR generation failed:", err);
    res.status(500).send("QR generation failed");
  }
});

app.get("/", (_, res) =>
  res.send("✅ Official qr-code-styling API running (with @napi-rs/canvas)")
);

const port = process.env.PORT || 3000;
app.listen(port, () => console.log(`Listening on port ${port}`));
