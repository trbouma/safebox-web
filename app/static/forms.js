"use strict";

document.addEventListener("submit", (event) => {
  const form = event.target;
  if (
    event.defaultPrevented ||
    !(form instanceof HTMLFormElement) ||
    !form.dataset.progressMessage
  ) {
    return;
  }

  form.setAttribute("aria-busy", "true");

  let status = form.querySelector("[data-progress-status]");
  if (!status) {
    status = document.createElement("p");
    status.className = "progress";
    status.dataset.progressStatus = "true";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    form.appendChild(status);
  }
  status.textContent = form.dataset.progressMessage;

  form.querySelectorAll('button[type="submit"]').forEach((button) => {
    button.dataset.originalText = button.textContent;
    if (form.dataset.progressButton) {
      button.textContent = form.dataset.progressButton;
    }
    button.disabled = true;
    button.setAttribute("aria-disabled", "true");
  });
});

window.addEventListener("pageshow", () => {
  document.querySelectorAll("form[data-progress-message]").forEach((form) => {
    form.removeAttribute("aria-busy");
    form.querySelector("[data-progress-status]")?.remove();
    form.querySelectorAll('button[type="submit"]').forEach((button) => {
      button.disabled = false;
      button.removeAttribute("aria-disabled");
      if (button.dataset.originalText) {
        button.textContent = button.dataset.originalText;
      }
    });
  });
});
