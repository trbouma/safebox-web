"use strict";

// Presentation preference only. Wallet and workflow state remain server-side.
(() => {
  const root = document.documentElement;
  const toggle = document.querySelector("[data-theme-toggle]");
  const savedTheme = document.cookie
    .split("; ")
    .find((entry) => entry.startsWith("safebox_theme="))
    ?.split("=")[1];
  let theme = savedTheme === "light" ? "light" : "dark";

  const applyTheme = () => {
    root.dataset.theme = theme;
    if (toggle) {
      toggle.textContent = theme === "dark" ? "Use light mode" : "Use dark mode";
      toggle.setAttribute("aria-pressed", String(theme === "light"));
    }
  };

  applyTheme();

  toggle?.addEventListener("click", () => {
    theme = theme === "dark" ? "light" : "dark";
    document.cookie = [
      `safebox_theme=${theme}`,
      "Path=/",
      "Max-Age=31536000",
      "SameSite=Strict",
      location.protocol === "https:" ? "Secure" : "",
    ]
      .filter(Boolean)
      .join("; ");
    applyTheme();
  });
})();
