/* Apply the saved choice before the stylesheet paints, without relaxing CSP. */
(() => {
  // A versioned preference gives every existing visitor the new light-first
  // experience while still preserving any choice they make from now on.
  const key = "kit_theme_v2";
  const valid = new Set(["light", "dark", "system"]);

  function get() {
    const saved = localStorage.getItem(key) || "light";
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
