import QrScanner from "/static/qr-scanner.min.js";

// Acquisition only. The server validates the credential, challenge and PIN.
const root = document.querySelector('[data-login-qr]');
if (root) {
  const find = (name) => root.querySelector(`[data-login-qr-${name}]`);
  const video = find('video'), start = find('start'), stop = find('stop');
  const status = find('status'), input = find('payload'), form = find('form');
  const supported = window.isSecureContext && typeof navigator.mediaDevices?.getUserMedia === 'function';
  let active = null, submitted = false;
  function finish(message) {
    const previous = active;
    active = null;
    if (previous) {
      clearTimeout(previous.timer);
      previous.scanner?.destroy();
    }
    video.hidden = true;
    start.disabled = !supported || submitted;
    stop.hidden = true;
    if (message) status.textContent = message;
  }
  start.addEventListener('click', async () => {
    if (!supported || active || submitted) return;
    const op = {};
    active = op;
    start.disabled = true;
    stop.hidden = false;
    video.hidden = false;
    status.textContent = 'Show the login QR. Its contents will not be opened as a link.';
    try {
      op.scanner = new QrScanner(video, (result) => {
        if (active !== op || submitted) return;
        const text = result?.data;
        if (typeof text !== 'string' || text.length > 550 || !/^safebox-card:v1:[A-Za-z0-9_-]+$/.test(text)) {
          status.textContent = 'Not a Safebox login QR. No contents were submitted or opened.';
          return;
        }
        submitted = true;
        input.value = text;
        finish('Login QR acquired. Continue by entering its PIN.');
        form.requestSubmit();
      }, {preferredCamera: 'environment', returnDetailedScanResult: true,
          maxScansPerSecond: 5, onDecodeError: () => { /* Never log scanned credentials. */ }});
      op.timer = setTimeout(() => { if (active === op) finish('Camera timed out. Start again or paste the QR text.'); }, 60000);
      await op.scanner.start();
      if (active !== op) op.scanner.destroy();
    } catch {
      if (active === op) finish('Camera could not start. Check camera permission, or paste the login QR text.');
    }
  });
  stop.addEventListener('click', () => finish('Camera stopped.'));
  window.addEventListener('pagehide', () => finish());
  document.addEventListener('visibilitychange', () => { if (document.hidden) finish('Camera stopped.'); });
  document.addEventListener('submit', () => { submitted = true; finish(); });
  start.disabled = !supported;
  if (!supported) status.textContent = 'Camera scanning is unavailable. Paste your login QR text instead.';
}
