(async () => {
  const { el, api, toolLabel } = KIT;
  await KIT.loadSession();
  const list = document.getElementById("conv-list");
  const thread = document.getElementById("thread");
  const fmt = (ts) => new Date(ts * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });

  async function open(id, button) {
    document.querySelectorAll(".conv-item").forEach((b) => b.removeAttribute("aria-current"));
    if (button) button.setAttribute("aria-current", "true");
    const c = await api(`/api/conversations/${id}`);
    const log = el("div", { class: "log" });
    for (const m of c.messages) {
      const box = el("div", { class: `msg ${m.role === "user" ? "user" : "bot"}` }, el("p", { text: m.content }));
      if (m.meta && m.meta.tools) box.append(el("div", { class: "tools" }, m.meta.tools.map((t) => el("span", { class: "tool-chip", text: toolLabel(t) }))));
      log.append(box);
    }
    thread.replaceChildren(
      el("div", { class: "thread-head" },
        el("div", {}, el("strong", { text: c.title }), el("br"), el("small", { class: "empty-note", text: `${fmt(c.created)} · ${c.mode === "public" ? "Visitor" : c.mode}` })),
        el("button", { class: "btn small danger", type: "button", text: "Delete", onclick: async () => {
          if (!confirm("Delete this conversation?")) return;
          await api(`/api/conversations/${id}`, { method: "DELETE" });
          load();
        } })),
      log);
  }

  async function load() {
    const items = await api("/api/conversations");
    list.replaceChildren();
    thread.replaceChildren(el("p", { class: "empty-note", text: items.length ? "Select a conversation to read it." : "No conversations yet. Talk to the assistant on the home page and it will appear here." }));
    for (const c of items) {
      const b = el("button", { class: "conv-item", type: "button", onclick: () => open(c.id, b) },
        el("strong", { text: c.title }), el("small", { text: `${fmt(c.updated)} · ${c.messages} messages · ${c.mode === "public" ? "Visitor" : c.mode}` }));
      list.append(el("li", {}, b));
    }
    if (items.length) open(items[0].id, list.querySelector(".conv-item"));
  }
  load().catch(() => thread.replaceChildren(el("p", { class: "empty-note", text: "Couldn't load history. Please refresh." })));
})();
