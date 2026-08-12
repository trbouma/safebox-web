"use strict";

// Progressive enhancement only: never intercept submission or own workflow
// state. Every form must remain fully functional through normal HTTP behavior.

document.addEventListener("submit", (event) => {
  const form = event.target;
  if (
    event.defaultPrevented ||
    !(form instanceof HTMLFormElement) ||
    !(form.dataset.progressMessage || form.hasAttribute("data-progress-form"))
  ) {
    return;
  }

  form.setAttribute("aria-busy", "true");

  const inlineStatus = form.querySelector("[data-progress-message]");
  const message =
    form.dataset.progressMessage ||
    inlineStatus?.textContent.trim() ||
    "Working. Please wait…";
  let status = inlineStatus || form.querySelector("[data-progress-status]");
  if (!status) {
    status = document.createElement("p");
    status.className = "progress";
    status.dataset.progressStatus = "true";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    form.appendChild(status);
  } else {
    status.hidden = false;
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
  }
  status.textContent = message;

  form.querySelectorAll('button[type="submit"]').forEach((button) => {
    button.dataset.originalText = button.textContent;
    const progressLabel =
      form.dataset.progressButton || button.dataset.progressLabel;
    if (progressLabel) {
      button.textContent = progressLabel;
    }
    button.disabled = true;
    button.setAttribute("aria-disabled", "true");
  });
});

window.addEventListener("pageshow", () => {
  const pageProgress = document.getElementById("page-progress");
  if (pageProgress) pageProgress.hidden = true;

  document
    .querySelectorAll("form[data-progress-message], form[data-progress-form]")
    .forEach((form) => {
      form.removeAttribute("aria-busy");
      form.querySelector("[data-progress-status]")?.remove();
      const inlineStatus = form.querySelector("[data-progress-message]");
      if (inlineStatus) inlineStatus.hidden = true;
      form.querySelectorAll('button[type="submit"]').forEach((button) => {
        button.disabled = false;
        button.removeAttribute("aria-disabled");
        if (button.dataset.originalText) {
          button.textContent = button.dataset.originalText;
        }
      });
    });
});

document.addEventListener("click", (event) => {
  if (
    event.defaultPrevented ||
    event.button !== 0 ||
    event.metaKey ||
    event.ctrlKey ||
    event.shiftKey ||
    event.altKey
  ) {
    return;
  }

  const link = event.target.closest("a[href]");
  if (
    !link ||
    link.target ||
    link.hasAttribute("download") ||
    link.getAttribute("href").startsWith("#")
  ) {
    return;
  }

  const destination = new URL(link.href, window.location.href);
  if (
    destination.origin !== window.location.origin ||
    destination.href === window.location.href
  ) {
    return;
  }

  const pageProgress = document.getElementById("page-progress");
  if (pageProgress) {
    pageProgress.hidden = false;
    pageProgress.textContent = "Opening…";
  }
  link.setAttribute("aria-busy", "true");
});

document.addEventListener("click", async (event) => {
  const qr = event.target.closest("button[data-address-copy]");
  if (!qr) {
    return;
  }

  const address = qr.dataset.addressCopy;
  const status = document.getElementById(qr.dataset.addressCopyStatus);

  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(address);
    } else {
      const temporary = document.createElement("textarea");
      temporary.value = address;
      temporary.setAttribute("readonly", "");
      temporary.style.position = "fixed";
      temporary.style.opacity = "0";
      document.body.appendChild(temporary);
      temporary.select();
      const copied = document.execCommand("copy");
      temporary.remove();
      if (!copied) {
        throw new Error("copy was rejected");
      }
    }

    if (status) {
      status.hidden = false;
      status.textContent = `${address} copied to the clipboard.`;
    }
  } catch (_error) {
    if (status) {
      status.hidden = false;
      status.textContent = "The address could not be copied automatically.";
    }
  }
});

document.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-copy-target]");
  if (!button) {
    return;
  }

  const target = document.getElementById(button.dataset.copyTarget);
  const status = document.getElementById(button.dataset.copyStatus);
  if (!(target instanceof HTMLTextAreaElement)) {
    return;
  }

  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(target.value);
    } else {
      target.focus();
      target.select();
      target.setSelectionRange(0, target.value.length);
      if (!document.execCommand("copy")) {
        throw new Error("copy was rejected");
      }
    }
    if (status) {
      status.textContent = "Safekeeping message copied. Clear the clipboard after storing it safely.";
    }
  } catch (_error) {
    target.focus();
    target.select();
    target.setSelectionRange(0, target.value.length);
    if (status) {
      status.textContent = "Automatic copy was unavailable. The message is selected for manual copying.";
    }
  }
});
