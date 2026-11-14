/* Shared helpers: DOM builder, API calls, header. No inline scripts (strict CSP). */
const KIT = (() => {
  const themes = window.KIT_THEME;
  const el = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else if (v !== false && v != null) n.setAttribute(k, v);
    }
    for (const c of kids.flat()) if (c != null) n.append(c.nodeType ? c : document.createTextNode(c));
    return n;
  };

  async function api(path, opts = {}) {
    const init = { credentials: "same-origin", ...opts };
    if (opts.json !== undefined) {
      init.method = init.method || "POST";
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify(opts.json);
      delete init.json;
    }
    const res = await fetch(path, init);
    if (!res.ok) {
      let msg = res.statusText || "Request failed";
      try {
        const j = await res.json();
        msg = typeof j.detail === "string" ? j.detail : (j.detail && j.detail[0] && j.detail[0].msg) || msg;
      } catch (_) { /* keep default */ }
      const err = new Error(msg);
      err.status = res.status;
      throw err;
    }
    return res.json();
  }

  const label = (s) => String(s).replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

  function toolLabel(name) {
    const [verb, ...rest] = name.split("_");
    const done = { get: "Checked", post: "Submitted", put: "Updated", patch: "Updated", delete: "Removed" }[verb] || "Used";
    const words = rest.filter((w) => !["me", "v1", "id"].includes(w)).join(" ");
    return `${done} ${words || "details"}`;
  }

  const LOGO = '<svg viewBox="0 0 40 40" width="36" height="36" aria-hidden="true"><circle cx="15" cy="20" r="10" fill="none" stroke="currentColor" stroke-width="3.5"/><circle cx="25" cy="20" r="10" fill="none" stroke="#F2A900" stroke-width="3.5"/></svg>';

  function renderHeader(session) {
    const host = document.getElementById("site-header");
    if (!host) return;
    const here = location.pathname;
    const link = (href, text) => el("a", { href, text, "aria-current": here === href ? "page" : false });
    const brand = el("a", { class: "brand", href: "/", "aria-label": `${session.college} home` });
    brand.innerHTML = LOGO;
    brand.append(el("span", { class: "name" }, el("strong", { text: session.short }), el("small", { text: session.college })));

    const right = el("div", { class: "who" });
    const theme = themes.get();
    const themeIcon = (choice) => choice === "dark" ? "☀" : choice === "light" ? "☾" : "◐";
    const themeButton = el("button", {
      class: "theme-toggle", type: "button", "aria-label": `Theme: ${theme}. Change colour theme`,
      title: "Change colour theme", text: themeIcon(theme),
      onclick: () => {
        const current = themes.get();
        const next = current === "system" ? "dark" : current === "dark" ? "light" : "system";
        themes.set(next);
        themeButton.textContent = themeIcon(next);
        themeButton.title = `Theme: ${next}. Click to change.`;
        themeButton.setAttribute("aria-label", `Theme: ${next}. Change colour theme`);
      }
    });
    themeButton.title = `Theme: ${theme}. Click to change.`;
    right.append(themeButton);
    if (session.role === "public") {
      right.append(el("a", { class: "btn primary small", href: "/login", text: "Login" }));
    } else {
      right.append(
        el("span", { text: session.name || session.login_id }),
        el("span", { class: "role", text: session.role }),
        el("button", { class: "btn small", type: "button", text: "Log out", onclick: async () => {
          await api("/api/logout", { method: "POST" }).catch(() => {});
          location.href = "/";
        } }));
    }
    host.replaceChildren(el("div", { class: "container bar" }, brand,
      el("nav", { class: "main-nav", "aria-label": "Main" }, link("/", "Home"), link("/history", "History")),
      el("div", { class: "spacer" }), right));
  }

  async function loadSession() {
    let session;
    try { session = await api("/api/session"); }
    catch (_) { session = { role: "public", college: "Knotopian Institute of Technology", short: "KIT" }; }
    document.title = document.title.replace(/^Knotopian Institute of Technology/, session.college);
    renderHeader(session);
    return session;
  }

  return { el, api, label, toolLabel, loadSession };
})();
