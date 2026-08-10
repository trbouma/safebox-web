"use strict";

import QrScanner from "/static/qr-scanner.min.js";

const root = document.querySelector("[data-lightning-scanner]");
const video = root?.querySelector("[data-scanner-video]");
const status = root?.querySelector("[data-scanner-status]");
const startButton = root?.querySelector("[data-scanner-start]");
const stopButton = root?.querySelector("[data-scanner-stop]");
const resultInput = document.querySelector("[data-scanner-result]");
const scanForm = document.querySelector("[data-scanner-form]");

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

  const scanner = new QrScanner(
    video,
    (result) => {
      if (accepted) return;
      accepted = true;
      const scannedValue = String(result.data || "").trim();
      if (!scannedValue) {
        accepted = false;
        status.textContent = "The QR code did not contain any data. Try again.";
        return;
      }
      resultInput.value = scannedValue;
      status.textContent = scannedValue.toLowerCase().startsWith("acorn:record-transfer:")
        ? "Record-sharing code acquired. Opening the transfer review…"
        : "Payment code acquired. Opening the payment review…";
      scanner.stop();
      startButton.hidden = false;
      startButton.disabled = false;
      stopButton.hidden = true;
      scanForm.requestSubmit();
    },
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

  const stop = () => {
    scanner.stop();
    startButton.hidden = false;
    startButton.disabled = false;
    stopButton.hidden = true;
    if (!accepted) status.textContent = "Camera scanning stopped.";
  };

  startButton.addEventListener("click", async () => {
    accepted = false;
    startButton.disabled = true;
    status.textContent = "Requesting camera access…";
    try {
      await scanner.start();
      startButton.hidden = true;
      startButton.disabled = false;
      stopButton.hidden = false;
      status.textContent = "Camera active. Hold the QR code inside the frame.";
    } catch (_error) {
      startButton.disabled = false;
      status.textContent =
        "Camera access was unavailable. Check browser permission or enter the address manually.";
    }
  });

  stopButton.addEventListener("click", stop);
  window.addEventListener("pagehide", () => scanner.destroy(), { once: true });
}
