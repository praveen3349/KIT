(async () => {
  const { el, api, label } = KIT;
  const session = await KIT.loadSession();
  new KitAssistant(document.getElementById("assistant"), session);

  if (session.role !== "public") {
    document.getElementById("login-cta").hidden = true;
    document.getElementById("welcome").hidden = false;
    document.getElementById("welcome-text").textContent =
      `Signed in as ${session.name || session.login_id} (${session.role}). Your conversations are saved in History.`;
  }

  const SKIP = new Set(["id", "name", "title", "course_name", "description", "about", "summary", "intro", "tagline", "motto"]);
  const pick = (o, keys) => keys.map((k) => o && o[k]).find((v) => typeof v === "string" && v.trim());
  const primitives = (o) => Object.entries(o || {}).filter(([k, v]) => !SKIP.has(k) && ["string", "number", "boolean"].includes(typeof v) && String(v).length < 60);

  try {
    const c = await api("/api/public/college");
    const data = c.college || c.data || c;
    const name = pick(data, ["name", "college_name", "title"]);
    if (name) document.getElementById("college-name").textContent = name;
    const intro = pick(data, ["description", "about", "intro", "summary", "tagline", "motto"]);
    if (intro) document.getElementById("college-intro").textContent = intro;
    const facts = document.getElementById("college-facts");
    for (const [k, v] of primitives(data).slice(0, 4)) facts.append(el("li", {}, el("b", { text: label(k) + ":" }), String(v)));
  } catch (_) { /* the page still works with its default intro */ }

  const grid = document.getElementById("course-grid");
  try {
    const raw = await api("/api/public/courses");
    const list = Array.isArray(raw) ? raw : raw.courses || raw.items || raw.data || [];
    grid.replaceChildren();
    if (!list.length) grid.append(el("p", { class: "empty-note", text: "Course details will appear here soon." }));
    for (const course of list) {
      const title = pick(course, ["name", "title", "course_name", "program", "code"]) || "Course";
      const desc = pick(course, ["description", "summary", "about"]);
      const dl = el("dl");
      for (const [k, v] of primitives(course).slice(0, 6)) dl.append(el("dt", { text: label(k) }), el("dd", { text: String(v) }));
      grid.append(el("article", { class: "card course" }, el("h3", { text: title }), desc ? el("p", { text: desc }) : null, dl));
    }
  } catch (_) {
    grid.replaceChildren(el("p", { class: "empty-note", text: "We couldn't load the course list right now. Ask the assistant about our courses instead." }));
  }
})();
