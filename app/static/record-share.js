"use strict";

// Best-effort lifecycle warning only. Deletion remains a confirmed server-side
// form action, and the recipient independently deletes after successful import.
(() => {
  const stopForm = document.querySelector("[data-stop-record-sharing]");
  if (!stopForm) return;

  let sharingActive = true;
  const warnBeforeLeaving = (event) => {
    if (!sharingActive) return;
    event.preventDefault();
    // Browsers intentionally display their own warning text.
    event.returnValue = "";
  };

  window.addEventListener("beforeunload", warnBeforeLeaving);
  stopForm.addEventListener("submit", () => {
    sharingActive = false;
    window.removeEventListener("beforeunload", warnBeforeLeaving);
  });
})();
