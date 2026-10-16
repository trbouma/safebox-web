"use strict";

// Hardware-only prototype: no credentials, network requests or persistent state.
const root = document.querySelector("[data-nfc-test]");
if (root) {
  const confirm = root.querySelector("[data-nfc-test-confirm]");
  const write = root.querySelector("[data-nfc-test-write]");
  const read = root.querySelector("[data-nfc-test-read]");
  const cancel = root.querySelector("[data-nfc-test-cancel]");
  const status = root.querySelector("[data-nfc-test-status]");
  const payload = root.querySelector("[data-nfc-test-payload]").textContent;
  const supported = window.isSecureContext && typeof window.NDEFReader === "function";
  let active = null;

  function controls() {
    write.disabled = !supported || !!active || !confirm.checked;
    read.disabled = !supported || !!active;
    confirm.disabled = !!active;
    cancel.hidden = !active;
  }

  function finish(operation, message) {
    if (active !== operation) return;
    window.clearTimeout(operation.timer);
    active = null;
    operation.controller.abort();
    status.textContent = message;
    controls();
  }

  function begin(kind) {
    const operation = { kind, controller: new AbortController(), timer: null };
    active = operation;
    operation.timer = window.setTimeout(() => finish(operation,
      kind === "write"
        ? "Write timed out. Card state is uncertain; read it back before trying again."
        : "Read timed out. No test record was verified."), 30000);
    controls();
    return operation;
  }

  function failure(operation, error) {
    const explanation = {
      NotAllowedError: "NFC permission was denied. Check browser permission.",
      NotSupportedError: "This device or card does not support this NFC operation.",
      NetworkError: "The card could not be accessed. Check NFC is enabled and hold the card steady.",
      NotReadableError: "The NFC card could not be read.",
      InvalidStateError: "NFC is busy or unavailable.",
    }[error?.name] || "The NFC operation did not complete. The card may be non-empty, locked or too small.";
    finish(operation, explanation + (operation.kind === "write"
      ? " No wallet credential was issued. Read back before retrying."
      : " No test record was verified."));
  }

  confirm.addEventListener("change", controls);
  write.addEventListener("click", async () => {
    if (!supported || active || !confirm.checked) return;
    const operation = begin("write");
    status.textContent = "Tap a blank NFC card to write the non-secret test record…";
    try {
      const reader = new window.NDEFReader();
      await reader.write({ records: [{ recordType: "text", data: payload, lang: "en" }] },
        { overwrite: false, signal: operation.controller.signal });
      finish(operation, "Test record written; not yet read-back verified. Remove the card, then choose Read Back Test Card. This is not a wallet card.");
    } catch (error) {
      failure(operation, error);
    }
  });

  read.addEventListener("click", async () => {
    if (!supported || active) return;
    const operation = begin("read");
    status.textContent = "Tap the test card to verify its public test record…";
    try {
      const reader = new window.NDEFReader();
      reader.onreading = (event) => {
        if (active !== operation) return;
        try {
          const records = event.message.records;
          const record = records[0];
          const matches = records.length === 1 && record.recordType === "text" &&
            record.data.byteLength <= 256 &&
            new TextDecoder(record.encoding || "utf-8", { fatal: true }).decode(record.data) === payload;
          finish(operation, matches
            ? "Test record verified. NFC write/read works for this test; no wallet card has been issued."
            : "This card does not contain exactly the expected test record. Its contents were not displayed or changed.");
        } catch (_error) {
          finish(operation, "The card could not be interpreted as the expected test record. Its contents were not displayed or changed.");
        }
      };
      reader.onreadingerror = () => finish(operation, "The card could not be read. No test record was verified.");
      await reader.scan({ signal: operation.controller.signal });
    } catch (error) {
      failure(operation, error);
    }
  });

  function stop() {
    if (active) finish(active, active.kind === "write"
      ? "Write cancelled. Card state may be uncertain; read back before retrying."
      : "Read cancelled. No test record was verified.");
  }
  cancel.addEventListener("click", stop);
  window.addEventListener("pagehide", stop);
  document.addEventListener("visibilitychange", () => { if (document.hidden) stop(); });
  status.textContent = !window.isSecureContext
    ? "NFC requires a secure context. Open Safebox over HTTPS on a compatible device."
    : !supported
      ? "Web NFC is unavailable in this browser. Use a compatible NFC-enabled device/browser; no card has been written."
      : "Ready for a non-secret hardware test. No wallet card will be issued.";
  controls();
}
