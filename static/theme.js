/* Apply the saved choice before the stylesheet paints, without relaxing CSP. */
(() => {
  const key = "kit_theme";
  const valid = new Set(["light", "dark", "system"]);

  function get() {
    const saved = localStorage.getItem(key) || "system";
    return valid.has(saved) ? saved : "system";
  }

  function apply(choice = get()) {
    if (choice === "system") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = choice;
    return choice;
  }

  window.KIT_THEME = { get, apply, set: (choice) => {
    const next = valid.has(choice) ? choice : "system";
    localStorage.setItem(key, next);
    return apply(next);
  } };
  apply();
})();
