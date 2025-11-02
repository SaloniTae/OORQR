import express from "express";
import { createCanvas } from "canvas";
import { JSDOM } from "jsdom";
import fs from "fs";
import path from "path";
import QRCodeStyling from "qr-code-styling";

const app = express();

app.get("/api/qr", async (req, res) => {
  const {
    data = "https://example.com",
    color = "#ffffff",
    bg = "transparent",
    size = 512,
    type = "rounded",
    eye = "extra-rounded",
    pupil = "dot",
    logo = ""
  } = req.query;

  try {
    // 1️⃣ Create a virtual DOM
    const dom = new JSDOM(`<!DOCTYPE html><body></body>`);
    global.window = dom.window;
    global.document = dom.window.document;
    global.HTMLCanvasElement = createCanvas(size, size).constructor;
    global.Image = dom.window.Image;

    // 2️⃣ Create QR Code
    const qr = new QRCodeStyling({
      width: parseInt(size),
      height: parseInt(size),
      data,
      image: logo || undefined,
      dotsOptions: { color, type },
      cornersSquareOptions: { color, type: eye },
      cornersDotOptions: { color, type: pupil },
      backgroundOptions: { color: bg }
    });

    // 3️⃣ Append to fake DOM
    const container = document.createElement("div");
    await qr.append(container);

    // 4️⃣ Extract as blob, convert to PNG buffer
    const blob = await qr.getRawData("png");
    const buffer = Buffer.from(await blob.arrayBuffer());

    // 5️⃣ Send back to client
    res.setHeader("Content-Type", "image/png");
    res.setHeader("Cache-Control", "public, max-age=300");
    res.end(buffer);

    // Cleanup
    delete global.window;
    delete global.document;
  } catch (err) {
    console.error("QR generation failed:", err);
    res.status(500).send("QR generation failed");
  }
});

app.get("/", (_, res) => res.send("✅ QR Code Styling API Online"));

const port = process.env.PORT || 3000;
app.listen(port, () => console.log(`Listening on port ${port}`));
