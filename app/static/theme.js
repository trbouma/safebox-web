"use strict";

// Presentation preference only. Wallet and workflow state remain server-side.
(() => {
  const root = document.documentElement;
  const toggle = document.querySelector("[data-theme-toggle]");
  const sunIcon = toggle?.querySelector('[data-theme-icon="sun"]');
  const moonIcon = toggle?.querySelector('[data-theme-icon="moon"]');
  const label = toggle?.querySelector("[data-theme-label]");
  const savedTheme = document.cookie
    .split("; ")
    .find((entry) => entry.startsWith("safebox_theme="))
    ?.split("=")[1];
  let theme = savedTheme === "light" ? "light" : "dark";

  const applyTheme = () => {
    root.dataset.theme = theme;
    if (toggle) {
      const action = theme === "dark" ? "Use light mode" : "Use dark mode";
      toggle.setAttribute("aria-label", action);
      toggle.setAttribute("title", action);
      toggle.setAttribute("aria-pressed", String(theme === "light"));
      if (label) label.textContent = action;
      if (sunIcon) sunIcon.hidden = theme !== "dark";
      if (moonIcon) moonIcon.hidden = theme === "dark";
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
