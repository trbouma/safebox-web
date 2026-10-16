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
        : "Read timed out. No NDEF card was read."), 30000);
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
      : " No NDEF card was read."));
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
      finish(operation, "Test record written; not yet read-back verified. Remove the card, then choose Read Any NFC Card. This is not a wallet card.");
    } catch (error) {
      failure(operation, error);
    }
  });

  read.addEventListener("click", async () => {
    if (!supported || active) return;
    const operation = begin("read");
    status.textContent = "Tap any NDEF card, including a safebox-2 card. Reading will not change it…";
    try {
      const reader = new window.NDEFReader();
      reader.onreading = (event) => {
        if (active !== operation) return;
        try {
          const records = event.message.records;
          if (!records || !Number.isSafeInteger(records.length) || records.length < 0) throw new Error();
          const record = records[0];
          let matches = false;
          if (records.length === 1 && record.recordType === "text" &&
              record.data?.byteLength <= 256 &&
              [undefined, "utf-8", "utf-16"].includes(record.encoding)) {
            try {
              matches = new TextDecoder(record.encoding || "utf-8", { fatal: true }).decode(record.data) === payload;
            } catch { /* An undecodable payload still confirms hardware reading. */ }
          }
          const types = new Set(['empty', 'text', 'url', 'smart-poster', 'absolute-url', 'mime', 'unknown']);
          const summary = Array.prototype.slice.call(records, 0, 16).map((item, index) => {
            const type = types.has(item.recordType) ? item.recordType : 'custom';
            const size = item.data?.byteLength;
            return `Record ${index + 1}: ${type}, ${Number.isSafeInteger(size) && size >= 0 ? size + ' bytes' : 'size unavailable'}.`;
          }).join(' ');
          finish(operation, matches
            ? "Test record verified. NFC write/read works for this test; no wallet card has been issued."
            : `NFC card read successfully: ${records.length} NDEF record(s). ${summary} ${records.length > 16 ? 'Only the first 16 records are listed. ' : ''}Payloads and card identifiers are hidden. Nothing was changed, submitted or opened. This confirms reading, not login or write permission.`);
        } catch (_error) {
          finish(operation, "An NFC reading event was received, but its record details could not be interpreted. Nothing was changed, submitted or opened.");
        }
      };
      reader.onreadingerror = () => finish(operation, "The card could not be read as NDEF. Nothing was changed.");
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
