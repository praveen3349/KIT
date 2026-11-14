(async () => {
  const { api } = KIT;
  const session = await KIT.loadSession();
  if (session.role !== "public") { location.replace("/"); return; }
  const form = document.getElementById("login-form");
  const err = document.getElementById("form-error");
  const id = document.getElementById("login_id");
  const pw = document.getElementById("password");
  const btn = form.querySelector("button[type=submit]");

  for (const b of document.querySelectorAll("[data-demo]")) {
    b.addEventListener("click", () => { id.value = b.dataset.demo; pw.value = "Kit@2026"; id.focus(); });
  }
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    err.textContent = "";
    btn.disabled = true; btn.textContent = "Signing in…";
    try {
      await api("/api/login", { json: { login_id: id.value.trim(), password: pw.value } });
      location.href = "/";
    } catch (ex) {
      err.textContent = ex.message;
      btn.disabled = false; btn.textContent = "Sign in";
    }
  });
})();
