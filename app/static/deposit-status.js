"use strict";

// The server owns payment state and renders each status representation.
// This optional enhancement only refreshes HTML; the ordinary form remains.
(() => {
  const panel = document.querySelector("[data-deposit-panel]");
  if (!panel) return;
  const status = panel.querySelector("[data-deposit-status]");
  const requestedWindow = Number(panel.dataset.pollWindowMs);
  const pollWindow = Number.isFinite(requestedWindow) && requestedWindow > 0
    ? Math.min(requestedWindow, 330000)
    : 150000;
  const deadline = Date.now() + pollWindow;
  let stopped = false;
  window.addEventListener("pagehide", () => { stopped = true; });
  async function refresh() {
    if (stopped || !panel.isConnected) return;
    try {
      const response = await fetch(panel.dataset.statusUrl, {
        headers: { Accept: "text/html" },
        credentials: "same-origin",
        cache: "no-store",
        redirect: "error",
        signal: AbortSignal.timeout(10000),
      });
      if (!response.ok) throw new Error("Status unavailable");
      const fragment = document.createElement("template");
      fragment.innerHTML = await response.text();
      if (stopped) return;
      if (fragment.content.querySelector("[data-deposit-complete]")) {
        panel.replaceChildren(fragment.content);
        return;
      }
      const keepPolling = fragment.content.querySelector("[data-deposit-poll]");
      status.replaceChildren(fragment.content);
      if (!keepPolling) return;
    } catch (_error) {
      if (stopped) return;
      status.textContent = "Status temporarily unavailable. You can use Refresh Transfer Status.";
    }
    if (Date.now() < deadline) {
      window.setTimeout(refresh, 3000);
    } else {
      status.textContent = "Automatic status updates have paused. Use Refresh Transfer Status to check again.";
    }
  }
  refresh();
})();
