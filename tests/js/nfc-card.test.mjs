import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import { readFileSync } from "node:fs";

const source = readFileSync(new URL("../../app/static/nfc-card.js", import.meta.url), "utf8");
const payload = "SAFEBOX-WEB-NFC-TEST:v1";
function setup({ secure = true, supported = true, writeError = null, pendingWrite = false } = {}) {
  const elements = {};
  for (const name of ["confirm", "write", "read", "cancel", "status", "payload"]) {
    elements[name] = { checked: false, disabled: false, hidden: false,
      textContent: name === "payload" ? payload : "", handlers: {},
      addEventListener(event, handler) { this.handlers[event] = handler; } };
  }
  const listeners = {};
  const timers = new Map();
  const readers = [];
  const writes = [];
  let completeWrite;
  class Reader {
    constructor() { readers.push(this); }
    async write(message, options) {
      writes.push({ message, options });
      if (writeError) throw writeError;
      if (pendingWrite) await new Promise((resolve) => { completeWrite = resolve; });
    }
    async scan(options) { this.options = options; }
  }
  const window = { isSecureContext: secure,
    setTimeout(fn) { const id = Symbol(); timers.set(id, fn); return id; },
    clearTimeout(id) { timers.delete(id); },
    addEventListener(event, handler) { listeners[event] = handler; } };
  if (supported) window.NDEFReader = Reader;
  const document = { hidden: false,
    querySelector() { return { querySelector(selector) {
      return elements[selector.slice("[data-nfc-test-".length, -1)];
    } }; },
    addEventListener(event, handler) { listeners[event] = handler; } };
  vm.runInNewContext(source, { window, document, AbortController, TextDecoder });
  return { elements, readers, writes, timers, listeners, document,
    click: (name) => elements[name].handlers.click(),
    confirm() { elements.confirm.checked = true; elements.confirm.handlers.change(); },
    complete: () => completeWrite() };
}

test("opening page never activates NFC; insecure/unsupported browsers stay disabled", async () => {
  for (const options of [{}, { secure: false }, { supported: false }]) {
    const ui = setup(options);
    assert.equal(ui.readers.length, 0);
    assert.equal(ui.elements.write.disabled, true);
    if (options.secure === false || options.supported === false) {
      ui.confirm();
      await ui.click("write");
      assert.equal(ui.writes.length, 0);
      assert.equal(ui.elements.read.disabled, true);
    }
  }
});

test("write requires confirmation, protects existing data and waits for readback", async () => {
  const ui = setup();
  await ui.click("write");
  assert.equal(ui.writes.length, 0);
  ui.confirm();
  await ui.click("write");
  assert.equal(ui.writes[0].message.records[0].data, payload);
  assert.equal(ui.writes[0].options.overwrite, false);
  assert.match(ui.elements.status.textContent, /not yet read-back verified/);
  await ui.click("read");
  ui.readers.at(-1).onreading({ message: { records: [{ recordType: "text", encoding: "utf-8", data: new TextEncoder().encode(payload) }] } });
  assert.match(ui.elements.status.textContent, /Test record verified/);
  assert.equal(ui.readers.at(-1).options.signal.aborted, true);
  assert.equal(ui.timers.size, 0);
});

test("any NDEF payload confirms reading without displaying secrets or claiming verification", async () => {
  for (const records of [[], [{ recordType: "text", data: new TextEncoder().encode("SECRET") }],
    [{ recordType: "url", data: new TextEncoder().encode(payload) }],
    [{ recordType: "text", data: new Uint8Array([255]) }],
    [{ recordType: "text", data: new Uint8Array(257) }],
    [{ recordType: "text" }, { recordType: "text" }]]) {
    const ui = setup();
    await ui.click("read");
    ui.readers.at(-1).onreading({ message: { records } });
    assert.doesNotMatch(ui.elements.status.textContent, /Test record verified|SECRET/);
    assert.match(ui.elements.status.textContent, /NFC card read successfully/);
    assert.equal(ui.timers.size, 0);
  }
});

test("legacy text, URLs and binary cards show bounded record metadata only", async () => {
  const ui = setup();
  await ui.click('read');
  const records = [
    {recordType:'text', data:new TextEncoder().encode('nembedSECRET')},
    {recordType:'url', data:new TextEncoder().encode('https://example.com/SECRET')},
    {recordType:'mime', mediaType:'SECRET', data:new DataView(new ArrayBuffer(400))},
    ...Array.from({length:20}, () => ({recordType:'SECRET', data:new Uint8Array(1)})),
  ];
  ui.readers.at(-1).onreading({serialNumber:'SECRET', message:{records}});
  const message = ui.elements.status.textContent;
  assert.match(message, /23 NDEF record/);
  assert.match(message, /Record 1: text, 12 bytes/);
  assert.match(message, /Record 3: mime, 400 bytes/);
  assert.match(message, /Only the first 16/);
  assert.doesNotMatch(message, /SECRET|Record 17/);
  assert.equal(ui.writes.length, 0);
});

test("permission/error text never exposes payloads or claims issuance", async () => {
  const ui = setup({ writeError: { name: "NotAllowedError", message: "SECRET" } });
  ui.confirm();
  await ui.click("write");
  assert.match(ui.elements.status.textContent, /permission was denied/);
  assert.doesNotMatch(ui.elements.status.textContent, /SECRET/);
  assert.equal(ui.elements.write.disabled, false);
});

test("cancel, timeout and navigation abort writes; late completion is ignored", async () => {
  for (const stop of [ui => ui.click("cancel"), ui => [...ui.timers.values()][0](),
    ui => ui.listeners.pagehide(), ui => { ui.document.hidden = true; ui.listeners.visibilitychange(); }]) {
    const ui = setup({ pendingWrite: true });
    ui.confirm();
    const writing = ui.click("write");
    await ui.click("write");
    assert.equal(ui.writes.length, 1);
    stop(ui);
    const message = ui.elements.status.textContent;
    assert.match(message, /uncertain/);
    assert.equal(ui.writes[0].options.signal.aborted, true);
    ui.complete();
    await writing;
    assert.equal(ui.elements.status.textContent, message);
  }
});

test("late reads after cancellation cannot report verification", async () => {
  const ui = setup();
  await ui.click("read");
  ui.click("cancel");
  const message = ui.elements.status.textContent;
  ui.readers.at(-1).onreading({ message: { records: [{ recordType: "text", data: new TextEncoder().encode(payload) }] } });
  assert.equal(ui.elements.status.textContent, message);
});
