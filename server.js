// ─────────────────────────────────────────────
// Styled QR API (CommonJS version, no ESM)
// Works on Render free plan (no apt-get)
// ─────────────────────────────────────────────

const express = require("express");
const { JSDOM } = require("jsdom");
const { createCanvas, Image: NapiImage } = require("@napi-rs/canvas");
const path = require("path");
const fs = require("fs");

// __dirname is available in CJS
const app = express();
const pkgBase = path.resolve(__dirname, "node_modules/qr-code-styling");

// Use the UMD bundle shipped by the npm package (per your /debug output)
function resolveQrStylingUMD() {
  const p = path.join(pkgBase, "lib", "qr-code-styling.js");
  if (!fs.existsSync(p)) {
    const top = fs.existsSync(pkgBase) ? fs.readdirSync(pkgBase) : [];
    const lib = fs.existsSync(path.join(pkgBase, "lib"))
      ? fs.readdirSync(path.join(pkgBase, "lib"))
      : [];
    throw new Error(
      "qr-code-styling UMD not found at " + p +
      "\nTop: " + top.join(", ") + "\nlib: " + lib.join(", ")
    );
  }
  return p;
}

// Run a function inside a fresh JSDOM + napi-canvas micro-env (concurrency-safe)
async function withDOM(width, height, fn) {
  const dom = new JSDOM(`<!DOCTYPE html><body></body>`);
  const { window } = dom;

  // Provide globals the UMD expects
  global.window = window;
  global.document = window.document;
  global.Image = NapiImage;

  // Intercept <canvas> creation to return napi canvas
  let lastCanvas = null;
  const origCreateElement = document.createElement.bind(document);
  document.createElement = (tag) => {
    if (String(tag).toLowerCase() === "canvas") {
      const c = createCanvas(width, height);
      lastCanvas = c;
      return c; // napi canvas already supports getContext('2d')
    }
    return origCreateElement(tag);
  };

  try {
    return await fn({ window, document, getCanvas: () => lastCanvas });
  } finally {
    // cleanup globals
    delete global.window;
    delete global.document;
    delete global.Image;
  }
}

// Debug route: shows files under qr-code-styling
app.get("/debug/qr-files", (req, res) => {
  const base = pkgBase;
  const libDir = path.join(base, "lib");
  const out = {
    base,
    top: fs.existsSync(base) ? fs.readdirSync(base) : [],
    lib: fs.existsSync(libDir) ? fs.readdirSync(libDir) : []
  };
  try { out.picked = resolveQrStylingUMD(); } catch (e) { out.error = String(e); }
  res.json(out);
});

// Main API: returns PNG with transparency
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
    const buf = await withDOM(W, W, async ({ window, document, getCanvas }) => {
      // Execute UMD; it attaches constructor to window.QRCodeStyling
      const umdPath = resolveQrStylingUMD();
      require(umdPath);
      const QRCodeStyling = window.QRCodeStyling;
      if (!QRCodeStyling) {
        throw new Error("window.QRCodeStyling not exposed by UMD bundle");
      }

      // Create container; let library append a canvas to it
      const container = document.createElement("div");
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

      await qr.append(container);

      const canvas = getCanvas();
      if (!canvas) throw new Error("Canvas not created by qr-code-styling");

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

// Health/root
app.get("/", (_, res) =>
  res.send("✅ Styled QR API online (CommonJS). Try /api/qr and /debug/qr-files")
);

// Start
const port = process.env.PORT || 3000;
app.listen(port, () => console.log(`🚀 Listening on :${port}`));
