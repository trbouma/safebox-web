"use strict";

// Bounded hypermedia enhancement: the URL comes from the current
// server-rendered representation and the response is a complete HTML fragment.
// This script owns no wallet, record, authorization, or workflow state. The
// template retains an ordinary full-page link for browsers without JavaScript.

document.addEventListener("toggle", async (event) => {
  const panel = event.target;
  if (
    !(panel instanceof HTMLDetailsElement) ||
    !panel.matches("details[data-lazy-check]") ||
    !panel.open ||
    panel.dataset.checkLoaded === "true" ||
    panel.dataset.checkLoading === "true"
  ) {
    return;
  }

  const content = panel.querySelector("[data-check-content]");
  if (!content) {
    return;
  }

  panel.dataset.checkLoading = "true";
  panel.setAttribute("aria-busy", "true");
  content.innerHTML = '<p class="progress" role="status">Loading verification evidence…</p>';

  try {
    const response = await fetch(panel.dataset.checkUrl, {
      headers: { Accept: "text/html" },
      credentials: "same-origin",
    });
    if (!response.ok) {
      throw new Error(`check request failed with ${response.status}`);
    }
    content.innerHTML = await response.text();
    panel.dataset.checkLoaded = "true";
  } catch (_error) {
    content.innerHTML = '<p class="error">Verification evidence could not be loaded. Close and reopen this pane to try again.</p>';
  } finally {
    delete panel.dataset.checkLoading;
    panel.removeAttribute("aria-busy");
  }
}, true);
