import express from "express";
import { JSDOM } from "jsdom";
import { createCanvas, Image as NapiImage } from "@napi-rs/canvas";
import path from "path";
import fs from "fs";
import { fileURLToPath } from "url";
import { createRequire } from "module";

const require = createRequire(import.meta.url);
const app = express();
const __dirname = path.dirname(fileURLToPath(import.meta.url));

function resolveQrStylingPath() {
  const pkgDir = path.resolve(__dirname, "node_modules/qr-code-styling");
  const libDir = path.join(pkgDir, "lib");
  if (!fs.existsSync(pkgDir)) throw new Error("qr-code-styling not installed");

  // Prefer the known CJS/UMD bundles first
  const candidates = [
    path.join(libDir, "qr-code-styling.js"),
    path.join(libDir, "qr-code-styling.common.js"),
    path.join(libDir, "index.js")
  ];
  for (const p of candidates) if (fs.existsSync(p)) return p;

  // Fallback: any .js in lib
  if (fs.existsSync(libDir)) {
    const any = fs.readdirSync(libDir).filter(f => f.endsWith(".js"));
    if (any.length) return path.join(libDir, any[0]);
  }
  throw new Error("qr-code-styling bundle not found in lib/");
}

async function withDOM(width, height, fn) {
  const dom = new JSDOM(`<!DOCTYPE html><body></body>`);
  const { window } = dom;

  global.window = window;
  global.document = window.document;
  global.Image = NapiImage;

  const origCreateElement = document.createElement.bind(document);
  document.createElement = (tag) => {
    if (tag.toLowerCase() === "canvas") {
      const c = createCanvas(width, height);
      return c; // @napi-rs/canvas has getContext('2d')
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
      const picked = resolveQrStylingPath();
      const mod = require(picked);               // ← load CJS/UMD
      const QRCodeStyling = mod.default || mod;  // ← get the class

      const canvas = createCanvas(W, W);

      const qr = new QRCodeStyling({
        width: W,
        height: W,
        data,
        image: logo || undefined,
        dotsOptions: { color, type },
        cornersSquareOptions: { color, type: eye },
        cornersDotOptions: { color, type: pupil },
        backgroundOptions: { color: bg } // "transparent" => PNG alpha preserved
      });

      await qr.append(canvas);
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

app.get("/debug/qr-files", (req, res) => {
  const base = path.resolve(__dirname, "node_modules/qr-code-styling");
  const libDir = path.join(base, "lib");
  const out = {
    base,
    top: fs.existsSync(base) ? fs.readdirSync(base) : [],
    lib: fs.existsSync(libDir) ? fs.readdirSync(libDir) : []
  };
  try { out.picked = resolveQrStylingPath(); } catch (e) { out.picked_error = String(e); }
  res.json(out);
});

app.get("/", (_, res) => res.send("✅ Styled QR API online"));

const port = process.env.PORT || 3000;
app.listen(port, () => console.log(`Listening on :${port}`));
