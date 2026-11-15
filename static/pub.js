// Shared by the public pages: the number search box, Follow on a number's page, and asking for a new number.
(function () {
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const post = async (url, body) => {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(new Error(j.detail || "That did not work. Try again in a minute."), { status: r.status });
    return j;
  };
  let mePromise;
  const me = fresh => (mePromise = (!fresh && mePromise) || fetch("/api/me").then(r => r.json()));

  // ---- search: numbers, each with its current value
  document.querySelectorAll("input[data-suggest]").forEach(input => {
    const box = document.createElement("div");
    box.className = "sug"; box.hidden = true;
    input.parentNode.appendChild(box);
    let timer, seq = 0, sel = -1;
    const mark = () => [...box.children].forEach((a, i) => a.classList.toggle("sel", i === sel));
    const show = async () => {
      const q = input.value.trim();
      if (!q) { box.hidden = true; return; }
      const mine = ++seq;
      const j = await fetch("/api/suggest?q=" + encodeURIComponent(q)).then(r => r.json()).catch(() => ({ numbers: [] }));
      if (mine !== seq) return;  // a later keystroke already answered
      box.innerHTML = (j.boards || []).map(b => `<a class="board" href="${esc(b.path)}"><span><b>${esc(b.name)}</b> · all ${b.count} numbers</span><em>board</em></a>`).join("") + j.numbers.map(n => `<a href="${esc(n.path)}"><span>${esc(n.title)}</span><em>${esc(n.value)}</em></a>`).join("")
        + `<a class="new" href="/track?q=${encodeURIComponent(q)}"><span>Track “${esc(q)}” as a new number</span><em>Pro</em></a>`;
      sel = -1; box.hidden = false;
    };
    input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(show, 140); });
    input.addEventListener("focus", show);
    input.addEventListener("keydown", e => {
      const n = box.children.length;
      if (e.key === "ArrowDown" && n) { e.preventDefault(); sel = (sel + 1) % n; mark(); }
      else if (e.key === "ArrowUp" && n) { e.preventDefault(); sel = (sel - 1 + n) % n; mark(); }
      else if (e.key === "Enter") { e.preventDefault(); const a = box.children[Math.max(sel, 0)]; if (a && !box.hidden) location.href = a.href; else if (input.value.trim()) show(); }
      else if (e.key === "Escape") box.hidden = true;
    });
    document.addEventListener("click", e => { if (!input.parentNode.contains(e.target)) box.hidden = true; });
    document.querySelectorAll("[data-try]").forEach(b => b.addEventListener("click", () => { input.value = b.dataset.try; input.focus(); show(); }));
  });

  // ---- copy a page's link
  document.querySelectorAll("[data-copy]").forEach(b => b.addEventListener("click", async () => {
    const label = b.dataset.label || (b.dataset.label = b.textContent);
    try { await navigator.clipboard.writeText(b.dataset.copy); b.textContent = "Copied"; }
    catch (e) { window.prompt("Copy this", b.dataset.copy); }
    setTimeout(() => { b.textContent = label; }, 2000);
  }));

  // ---- sign in, in place: the visitor stays on the page they were reading
  function signIn(holder, why) {
    return new Promise(async resolve => {
      const m = await me();
      holder.hidden = false;
      holder.innerHTML = `<p>${esc(why)}</p><div class="gbtn"></div>` + (m.google_client_id ? "" : "<p>Sign-in is not set up on this server.</p>");
      if (!m.google_client_id) return;
      const start = () => {
        google.accounts.id.initialize({ client_id: m.google_client_id, callback: async r => {
          try { await post("/api/auth/google", { credential: r.credential, bare: true }); await me(true); holder.hidden = true; resolve(true); }
          catch (e) { holder.querySelector("p").textContent = e.message; }
        } });
        google.accounts.id.renderButton(holder.querySelector(".gbtn"), { theme: "filled_blue", size: "large", shape: "rectangular", text: "continue_with", width: 260 });
      };
      if (window.google && google.accounts) return start();
      const s = document.createElement("script");
      s.src = "https://accounts.google.com/gsi/client"; s.onload = start;
      document.head.appendChild(s);
    });
  }

  // ---- Follow, on a number's page
  const fb = document.querySelector("[data-follow]");
  if (fb) {
    const mid = fb.dataset.follow, note = document.getElementById("follownote"), holder = document.getElementById("signin");
    const set = on => {
      fb.textContent = on ? "Following" : "Follow";
      fb.classList.toggle("on", on); fb.setAttribute("aria-pressed", on);
      note.innerHTML = on ? 'This number is on <a href="/app">your board</a>, where you can have changes sent to Slack or Discord.' : "";
    };
    me().then(async m => { if (m.user) { const j = await fetch("/api/metrics/" + mid).then(r => r.json()); set(!!(j.metric && j.metric.on_board)); } });
    fb.addEventListener("click", async () => {
      const m = await me();
      if (!m.user && !(await signIn(holder, "Sign in to follow this number. It goes on your board, and you stay on this page."))) return;
      try {
        if (fb.classList.contains("on")) { await post(`/api/metrics/${mid}/unfollow`); set(false); }
        else { await post(`/api/metrics/${mid}/follow`); set(true); }
      } catch (e) { note.textContent = e.message; }
    });
  }

  // ---- ask for a number the record does not have
  const tb = document.querySelector("[data-track]");
  if (tb) {
    const holder = document.getElementById("signin"), note = document.getElementById("tracknote");
    tb.addEventListener("click", async () => {
      const q = document.getElementById("trackq").value.trim();
      if (!q) { note.textContent = "Type the number you want, for example “Stripe latest valuation”."; return; }
      const m = await me();
      if (!m.user && !(await signIn(holder, "Sign in first. Adding a number to the record is part of Pro."))) return;
      tb.disabled = true; note.textContent = "Searching for it and reading the source. This takes about half a minute.";
      try { const j = await post("/api/metrics", { question: q }); location.href = j.path || "/n/" + j.metric.id; }
      catch (e) { tb.disabled = false; note.innerHTML = esc(e.message) + (e.status === 402 ? ' <a href="/app?plans=1">See Pro</a>' : ""); }
    });
  }
})();
