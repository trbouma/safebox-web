"use strict";

import QrScanner from "/static/qr-scanner.min.js";

const root = document.querySelector("[data-lightning-scanner]");
const video = root?.querySelector("[data-scanner-video]");
const status = root?.querySelector("[data-scanner-status]");
const startButton = root?.querySelector("[data-scanner-start]");
const stopButton = root?.querySelector("[data-scanner-stop]");
const resultInput = document.querySelector("[data-scanner-result]");
const scanForm = document.querySelector("[data-scanner-form]");
const symbologyInput = document.querySelector("[data-scanner-symbology]");

if (
  root instanceof HTMLElement &&
  video instanceof HTMLVideoElement &&
  status instanceof HTMLElement &&
  startButton instanceof HTMLButtonElement &&
  stopButton instanceof HTMLButtonElement &&
  resultInput instanceof HTMLInputElement &&
  scanForm instanceof HTMLFormElement
) {
  let accepted = false;
  let cameraRunning = false;
  let detector = null;
  let detecting = false;
  const cameraAvailable =
    window.isSecureContext &&
    navigator.mediaDevices != null &&
    typeof navigator.mediaDevices.getUserMedia === "function";

  const acceptResult = (result) => {
      if (accepted) return;
      accepted = true;
      // Do not trim the payload: ASCII GS (0x1D) defines GS1 field boundaries.
      const scannedValue = String(result.data || "");
      if (!scannedValue) {
        accepted = false;
        status.textContent = "The QR code did not contain any data. Try again.";
        return;
      }
      resultInput.value = scannedValue;
      if (symbologyInput) symbologyInput.value = result.symbology || "qr_code";
      const lowerValue = scannedValue.toLowerCase();
      status.textContent = lowerValue.startsWith("acorn:record-presentation:")
        ? "Record presentation acquired. Opening the temporary view…"
        : lowerValue.startsWith("acorn:record-transfer:")
          ? "Record-sharing code acquired. Opening the transfer review…"
          : (lowerValue.startsWith("creqa") || lowerValue.startsWith("creqb1") || (lowerValue.startsWith("bitcoin:") && /[?&]creq=/.test(lowerValue)))
            ? "Clear payment request acquired. Opening the transfer review…"
          : /^(cashu:)?cashu[ab]/.test(lowerValue)
            ? "Clear token acquired. Securing it for acceptance…"
          : /^https?:\/\/[^/]+\/01(?:\/|$)/.test(lowerValue)
            ? "Product code acquired. Looking up product information…"
          : lowerValue.startsWith("https://")
            ? "Website address acquired. Opening the safety review…"
          : "Payment code acquired. Opening the payment review…";
      scanner.stop();
      cameraRunning = false;
      startButton.hidden = false;
      startButton.disabled = false;
      stopButton.hidden = true;
      scanForm.requestSubmit();
    };
  const scanner = new QrScanner(
    video,
    acceptResult,
    {
      preferredCamera: "environment",
      highlightScanRegion: true,
      highlightCodeOutline: true,
      maxScansPerSecond: 10,
      returnDetailedScanResult: true,
      onDecodeError: (error) => {
        const message = String(error?.message || error || "");
        if (message && message !== QrScanner.NO_QR_CODE_FOUND) {
          console.warn("QR decoder error", error);
          status.textContent =
            "The camera is active, but the QR decoder reported an error. Reload the page or enter the code manually.";
        }
      },
    },
  );

  resultInput.addEventListener("input", () => {
    if (symbologyInput) symbologyInput.value = "";
  });
  // QrScanner remains the cross-browser QR fallback. Native decoding adds
  // other symbologies where available, without inventing a GS1/FNC1 flag.
  if ("BarcodeDetector" in window) {
    BarcodeDetector.getSupportedFormats().then((formats) => {
      const supported = formats.filter((f) => ["data_matrix", "code_128", "ean_8", "ean_13", "upc_a", "upc_e"].includes(f));
      if (supported.length) detector = new BarcodeDetector({ formats: supported });
    }).catch(() => {});
  }
  const barcodeTimer = window.setInterval(async () => {
    if (!detector || !cameraRunning || accepted || detecting || video.readyState < 2) return;
    detecting = true;
    try {
      const results = await detector.detect(video);
      if (results.length === 1 && cameraRunning && !accepted) {
        acceptResult({ data: results[0].rawValue, symbology: results[0].format });
      } else if (results.length > 1) {
        status.textContent = "Multiple barcodes detected. Show one barcode at a time.";
      }
    } catch (_error) {
      // QR acquisition and manual input remain available.
    } finally {
      detecting = false;
    }
  }, 400);

  const stop = () => {
    cameraRunning = false;
    scanner.stop();
    startButton.hidden = false;
    startButton.disabled = false;
    stopButton.hidden = true;
    if (!accepted) status.textContent = "Camera scanning stopped.";
  };

  const start = async () => {
    if (!cameraAvailable) {
      video.hidden = true;
      startButton.hidden = true;
      stopButton.hidden = true;
      status.textContent =
        "Camera scanning requires HTTPS when Safebox is opened from another device. Enter the code manually below.";
      return;
    }
    accepted = false;
    startButton.disabled = true;
    status.textContent = "Requesting camera access…";
    try {
      await scanner.start();
      cameraRunning = true;
      startButton.hidden = true;
      startButton.disabled = false;
      stopButton.hidden = false;
      status.textContent = "Camera active. Hold the QR code inside the frame.";
    } catch (_error) {
      startButton.hidden = false;
      startButton.disabled = false;
      status.textContent =
        "Camera access was unavailable. Check browser permission or enter the address manually.";
    }
  };

  startButton.addEventListener("click", start);
  stopButton.addEventListener("click", stop);
  window.addEventListener("pagehide", () => {
    cameraRunning = false;
    window.clearInterval(barcodeTimer);
    scanner.destroy();
  }, { once: true });
  if (cameraAvailable) {
    void start();
  } else {
    video.hidden = true;
    startButton.hidden = true;
    status.textContent =
      "Camera scanning requires HTTPS when Safebox is opened from another device. Enter the code manually below.";
  }
}
