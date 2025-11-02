import express from "express";
import { createCanvas } from "canvas";
import { StyledQRCode } from "qrcode_styled";

const app = express();

app.get("/api/qr", async (req, res) => {
  try {
    const data    = req.query.data  || "https://example.com";
    const color   = req.query.color || "#ffffff";
    const bg      = req.query.bg    || "transparent";
    const size    = parseInt(req.query.size || "512", 10);
    const type    = req.query.type  || "rounded";
    const eye     = req.query.eye   || "extra-rounded";
    const pupil   = req.query.pupil || "dot";
    const logo    = req.query.logo  || "";

    // Offscreen canvas
    const canvas = createCanvas(size, size);
    const qr = new StyledQRCode({
      width: size,
      height: size,
      data,
      image: logo || undefined,
      dotsOptions: { color, type },
      cornersSquareOptions: { color, type: eye },
      cornersDotOptions: { color, type: pupil },
      backgroundOptions: { color: bg } // "transparent" keeps alpha in PNG
    });

    // Render onto our node-canvas
    await qr.append(canvas);

    // Output PNG with alpha channel
    res.setHeader("Content-Type", "image/png");
    res.setHeader("Cache-Control", "public, max-age=3600");
    canvas.createPNGStream().pipe(res);
  } catch (e) {
    console.error(e);
    res.status(500).send("QR generation error");
  }
});

app.get("/", (_, res) => res.send("Styled QR API OK"));
const port = process.env.PORT || 3000;
app.listen(port, () => console.log("Listening on " + port));
