// ─────────────────────────────────────────────
// Styled QR API (official qr-code-styling + debug)
// Works on Render free plan (no apt-get, no Docker)
// ─────────────────────────────────────────────

import express from "express";
import { JSDOM } from "jsdom";
import { createCanvas, Image as NapiImage } from "@napi-rs/canvas";
import path from "path";
import fs from "fs";
import { fileURLToPath } from "url";

const app = express();
const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ─────────────────────────────────────────────
// Resolver: find the actual bundle shipped by qr-code-styling
// ─────────────────────────────────────────────
function resolveQrStylingPath() {
  const pkgDir = path.resolve(__dirname, "node_modules/qr-code-styling");
  const pkgJsonPath = path.join(pkgDir, "package.json");

  if (!fs.existsSync(pkgJsonPath)) {
    throw new Error(`qr-code-styling not installed at ${pkgDir}`);
  }

  const pkg = JSON.parse(fs.readFileSync(pkgJsonPath, "utf8"));
  const tried = [];

  // 1) Prefer explicit fields from package.json if present
  const fromPkg = [];
  if (typeof pkg.module === "string") fromPkg.push(pkg.module);
  if (typeof pkg.browser === "string") fromPkg.push(pkg.browser);
  if (typeof pkg.main === "string") fromPkg.push(pkg.main);

  if (pkg.exports) {
    const exp = pkg.exports;
    if (typeof exp === "string") fromPkg.push(exp);
    else if (exp["."]) {
      const dot = exp["."];
      if (typeof dot === "string") fromPkg.push(dot);
      else if (dot.import) fromPkg.push(dot.import);
      else if (dot.default) fromPkg.push(dot.default);
    }
  }

  for (const rel of fromPkg) {
    const abs = path.resolve(pkgDir, rel);
    tried.push(abs);
    if (fs.existsSync(abs)) return { picked: abs, tried };
  }

  // 2) Fallback scan inside /lib
  const libDir = path.join(pkgDir, "lib");
  if (fs.existsSync(libDir)) {
    const preferred = [
      "qr-code-styling.esm.js",
      "index.esm.js",
      "index.mjs",
      "qr-code-styling.cjs.js",
      "qr-code-styling.js",
      "index.js"
    ];
    for (const name of preferred) {
      const p = path.join(libDir, name);
      tried.push(p);
      if (fs.existsSync(p)) return { picked: p, tried };
    }

    // 2b) As a last resort: any .mjs or .js in lib
    const files = (fs.readdirSync(libDir).filter(f => /\.(mjs|js)$/.test(f)) || []);
    for (const f of files) {
      const p = path.join(libDir, f);
      tried.push(p);
      if (fs.existsSync(p)) return { picked: p, tried };
    }
  }

  // 3) If still not found, show what exists to debug
  const top = fs.readdirSync(pkgDir).join(", ");
  let libList = "(no lib dir)";
  try { libList = fs.readdirSync(path.join(pkgDir, "lib")).join(", "); } catch {}

  const msg = [
    "qr-code-styling bundle not found.",
    "Tried:",
    ...tried.map(p => "  " + p),
    "",
    "Top-level files:",
    "  " + top,
    "lib/ files:",
    "  " + libList
  ].join("\n");

  throw new Error(msg);
}

// ─────────────────────────────────────────────
// JsDOM + Canvas micro-environment per request
// (concurrency-safe; no global leaks)
// ─────────────────────────────────────────────
async function withDOM(width, height, fn) {
  const dom = new JSDOM(`<!DOCTYPE html><body></body>`);
  const { window } = dom;

  // Provide minimal globals expected by qr-code-styling:
  global.window = window;
  global.document = window.document;
  global.Image = NapiImage;

  // Make document.createElement('canvas') return a napi-rs canvas
  const origCreateElement = document.createElement.bind(document);
  document.createElement = (tag) => {
    if (tag.toLowerCase() === "canvas") {
      const c = createCanvas(width, height);
      // napi-rs canvas already supports 2D contexts
      return c;
    }
    return origCreateElement(tag);
  };

  try {
    return await fn({ window, document });
  } finally {
    // Cleanup globals
    delete global.window;
    delete global.document;
    delete global.Image;
  }
}

// ─────────────────────────────────────────────
// Debug route: see installed files and picked bundle
// ─────────────────────────────────────────────
app.get("/debug/qr-files", (req, res) => {
  const base = path.resolve(__dirname, "node_modules/qr-code-styling");
  const out = { base, present: fs.existsSync(base) };
  if (out.present) {
    out.top = fs.readdirSync(base);
    const libDir = path.join(base, "lib");
    out.lib = fs.existsSync(libDir) ? fs.readdirSync(libDir) : "(no lib dir)";
    try {
      const { picked, tried } = resolveQrStylingPath();
      out.picked = picked;
      out.tried = tried;
    } catch (e) {
      out.error = String(e);
    }
  }
  res.json(out);
});

// ─────────────────────────────────────────────
// Main API: /api/qr → PNG (transparent)
// ─────────────────────────────────────────────
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
      // Dynamically import the resolved bundle (ESM or CJS entry)
      const { picked } = resolveQrStylingPath();
      const QRCodeStyling = (await import(`file://${picked}`)).default;

      // Prepare target canvas
      const canvas = createCanvas(W, W);

      // Configure like the browser usage
      const qr = new QRCodeStyling({
        width: W,
        height: W,
        data,
        image: logo || undefined,
        dotsOptions: { color, type },                 // "rounded", "dots", "classy"...
        cornersSquareOptions: { color, type: eye },   // e.g., "extra-rounded"
        cornersDotOptions: { color, type: pupil },    // e.g., "dot"
        backgroundOptions: { color: bg }              // "transparent" keeps alpha
      });

      // Render onto our canvas
      await qr.append(canvas);

      // Return PNG bytes with alpha
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
app.get("/", (_, res) => res.send("✅ Styled QR API online. See /api/qr and /debug/qr-files"));

// Start
const port = process.env.PORT || 3000;
app.listen(port, () => console.log(`🚀 Listening on :${port}`));
