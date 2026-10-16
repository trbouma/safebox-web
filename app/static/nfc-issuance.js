// Hardware adapter only: credential encryption and interpretation stay on the issuer.
const root = document.querySelector('[data-nfc-issuance]');
if (root) {
  const find = (name) => root.querySelector(`[data-card-${name}]`);
  const write = find('write'), read = find('read'), cancel = find('cancel');
  const status = find('status'), consent = find('confirm');
  const supported = window.isSecureContext && typeof window.NDEFReader === 'function';
  const mime = 'application/vnd.safebox.card+cbor';
  let active = null;
  function controls() {
    read.disabled = !supported || !!active;
    if (write) write.disabled = !supported || !!active || !consent.checked;
    cancel.hidden = !active;
  }
  function finish(op, message) {
    if (active !== op) return false;
    active = null;
    clearTimeout(op.timer);
    op.controller.abort();
    status.textContent = message;
    controls();
    return true;
  }
  function begin() {
    if (!supported || active) return null;
    const op = {controller: new AbortController()};
    active = op;
    op.timer = setTimeout(() => finish(op, 'NFC operation timed out. Read the card before retrying a write.'), 30000);
    controls();
    return op;
  }
  consent?.addEventListener('change', controls);
  write?.addEventListener('click', async () => {
    if (!consent.checked) return;
    const op = begin();
    if (!op) return;
    try {
      const bytes = Uint8Array.from(atob(find('write-payload').value), (c) => c.charCodeAt(0));
      if (!bytes.length || bytes.length > 400) throw new Error();
      status.textContent = 'Hold the intended card near your device. Its existing NDEF contents will be replaced.';
      await new window.NDEFReader().write({records: [{recordType: 'mime', mediaType: mime, data: bytes}]},
        {overwrite: true, signal: op.controller.signal});
      finish(op, 'Write completed. Remove the card, then choose Read Card to verify it before activation.');
    } catch {
      finish(op, 'Write was not confirmed. Check permissions and card capacity; read the card before retrying.');
    }
  });
  read.addEventListener('click', async () => {
    const op = begin();
    if (!op) return;
    try {
      const reader = new window.NDEFReader();
      reader.onreading = ({message}) => {
        if (active !== op) return;
        const records = message?.records;
        const record = records?.[0];
        if (records?.length !== 1 || record.recordType !== 'mime' || record.mediaType !== mime ||
            !record.data || record.data.byteLength < 1 || record.data.byteLength > 400) {
          finish(op, 'Not a supported Safebox credential card. No card contents were submitted.');
          return;
        }
        const bytes = new Uint8Array(record.data.buffer, record.data.byteOffset, record.data.byteLength);
        const encoded = btoa(String.fromCharCode(...bytes));
        if (finish(op, 'Card read. Verifying with this issuer…')) {
          find('read-payload').value = encoded;
          find('read-form').requestSubmit();
        }
      };
      reader.onreadingerror = () => finish(op, 'Card could not be read. Remove it and try again.');
      status.textContent = 'Tap the card to read its encrypted credential.';
      await reader.scan({signal: op.controller.signal});
    } catch {
      finish(op, 'NFC reading could not start. Check browser permissions and device support.');
    }
  });
  const stop = () => { if (active) finish(active, 'NFC operation cancelled. Read the card to check whether a write completed.'); };
  cancel.addEventListener('click', stop);
  window.addEventListener('pagehide', stop);
  document.addEventListener('visibilitychange', () => { if (document.hidden) stop(); });
  status.textContent = supported ? 'Ready. NFC operations require a button press and a nearby card.' :
    'Web NFC is unavailable. Use a compatible NFC-enabled browser over HTTPS.';
  controls();
}
