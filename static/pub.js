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

  // ---- chart: point at any moment and read what the number was then. The value shown is the one on record at that moment
  // (the last reading on or before it); nothing is filled in between readings. A click opens the page at that day.
  document.querySelectorAll("svg[data-probe]").forEach(svg => {
    let pts; try { pts = JSON.parse(svg.dataset.probe); } catch (e) { return; }
    if (!pts || pts.length < 2) return;
    const d = svg.dataset, n = k => parseFloat(d[k]), x0 = n("x0"), x1 = n("x1"), y0 = n("y0"), y1 = n("y1"), t0 = n("t0"), t1 = n("t1"), lo = n("lo"), hi = n("hi"), W = n("w"), step = d.step === "1";
    const NS = "http://www.w3.org/2000/svg", el = (tag, attrs) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); svg.appendChild(e); return e; };
    const line = el("line", { y1: y0, y2: y1, stroke: "#0e1320", "stroke-opacity": ".35", "stroke-dasharray": "3 3", visibility: "hidden" });
    const dot = el("circle", { r: 5, fill: "#2f5bff", stroke: "#fff", "stroke-width": 2, visibility: "hidden" });
    const wrap = svg.parentNode; if (getComputedStyle(wrap).position === "static") wrap.style.position = "relative";
    const tip = document.createElement("div");
    tip.style.cssText = "position:absolute;top:6px;pointer-events:none;background:#0e1320;color:#fff;font:13px/1.35 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;padding:7px 10px;border-radius:8px;white-space:nowrap;visibility:hidden;z-index:2;box-shadow:0 4px 14px rgba(14,19,32,.18)";
    wrap.appendChild(tip);
    const day = t => new Date(t * 1000).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" });
    const iso = t => new Date(t * 1000).toISOString().slice(0, 10);
    const X = t => x0 + (t - t0) / (t1 - t0) * (x1 - x0), Y = v => y0 + (1 - (v - lo) / (hi - lo)) * (y1 - y0);
    let at = null;
    const move = ev => {
      const r = svg.getBoundingClientRect(), cx = (ev.touches ? ev.touches[0].clientX : ev.clientX) - r.left;
      const t = Math.min(t1, Math.max(t0, t0 + (cx / r.width * W - x0) / (x1 - x0) * (t1 - t0)));
      let a = 0, b = pts.length - 1;
      while (a < b) { const m = (a + b + 1) >> 1; if (pts[m][0] <= t + 43200) a = m; else b = m - 1; }  // the last reading on or before this moment
      const p = pts[a], px = step ? X(t) : X(p[0]);
      at = step ? t : p[0];
      line.setAttribute("x1", px); line.setAttribute("x2", px); dot.setAttribute("cx", px); dot.setAttribute("cy", Y(p[1]));
      line.setAttribute("visibility", "visible"); dot.setAttribute("visibility", "visible");
      const since = step && iso(p[0]) !== iso(t) ? `<br><span style="opacity:.7">since ${esc(day(p[0]))}${p[3] ? " · " + esc(p[3]) : ""}</span>` : (p[3] ? `<br><span style="opacity:.7">${esc(p[3])}</span>` : "");
      tip.innerHTML = `<b>${esc(p[2])}</b> · ${esc(day(at))}${since}`;
      tip.style.visibility = "visible";
      const left = px / W * r.width + (svg.offsetLeft || 0);
      tip.style.left = Math.max(4, Math.min(wrap.clientWidth - tip.offsetWidth - 4, left - tip.offsetWidth / 2)) + "px";
    };
    const hide = () => { [line, dot].forEach(e => e.setAttribute("visibility", "hidden")); tip.style.visibility = "hidden"; };
    svg.style.cursor = "crosshair"; svg.style.touchAction = "pan-y";
    svg.addEventListener("mousemove", move); svg.addEventListener("mouseleave", hide);
    svg.addEventListener("touchstart", move, { passive: true }); svg.addEventListener("touchmove", move, { passive: true });
    svg.addEventListener("click", () => { if (at !== null) { const u = new URL(location.href); u.searchParams.set("on", iso(at)); u.hash = "on"; location.href = u.toString(); } });
  });

  // ---- search: numbers, each with its current value
  document.querySelectorAll("input[data-suggest]").forEach(input => {
    const box = document.createElement("div");
    box.className = "sug"; box.hidden = true;
    input.parentNode.appendChild(box);
    let timer, seq = 0, sel = -1, told = "", settle;
    // Tell the site what was searched for once the visitor has stopped typing (or picks a row): the words and how many numbers matched.
    const tell = (q, found) => { if (q.length < 3 || q === told) return; told = q;
      try { navigator.sendBeacon("/api/asked", new Blob([JSON.stringify({ q, found })], { type: "application/json" })); } catch (e) {} };
    const mark = () => [...box.children].forEach((a, i) => a.classList.toggle("sel", i === sel));
    const show = async () => {
      const q = input.value.trim();
      if (!q) { box.hidden = true; return; }
      const mine = ++seq;
      const j = await fetch("/api/suggest?q=" + encodeURIComponent(q)).then(r => r.json()).catch(() => ({ numbers: [] }));
      if (mine !== seq) return;  // a later keystroke already answered
      box.innerHTML = (j.boards || []).map(b => `<a class="board" href="${esc(b.path)}"><span><b>${esc(b.name)}</b> · ${b.vs ? esc(b.note || "side by side") : `all ${b.count} numbers`}</span><em>${b.vs ? "compare" : "board"}</em></a>`).join("") + j.numbers.map(n => `<a href="${esc(n.path)}"><span>${esc(n.title)}</span><em>${esc(n.value)}</em></a>`).join("")
        + `<a class="new" href="/request?q=${encodeURIComponent(q)}"><span>Research “${esc(q)}” now, with sources</span><em>${j.numbers.length ? "not listed?" : "not on record yet"}</em></a>`;
      sel = -1; box.hidden = false;
      clearTimeout(settle); settle = setTimeout(() => { if (input.value.trim() === q) tell(q, j.numbers.length + (j.boards || []).length); }, 1500);
      box.onclick = () => tell(q, j.numbers.length + (j.boards || []).length);
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
      note.innerHTML = on ? 'You will get an email when it moves. It is on <a href="/app">your board</a>.' : "";
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

  // ---- create a number: the agent's research plays out live. Phases across the top, a feed of what it is doing right now,
  // the sites it has read so far, and a placeholder for the result that fills in when the checks are done.
  const hostOf = u => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch (e) { return ""; } };
  const shortUrl = u => { try { const x = new URL(u); return x.hostname.replace(/^www\./, "") + (x.pathname.length > 1 ? x.pathname.slice(0, 44) : ""); } catch (e) { return u; } };
  const hue = h => { let n = 0; for (const c of h) n = (n * 31 + c.charCodeAt(0)) % 360; return `hsl(${n} 55% 42%)`; };
  const av = u => { const h = hostOf(u); return h ? `<span class="av" style="background:${hue(h)}">${esc(h[0].toUpperCase())}</span>` : ""; };
  const stepHtml = s => {
    if (s.tool === "search") return `Searched the web for “${esc(s.arg)}”`;
    if (s.tool === "fetch") return `${av(s.arg)}Read <a href="${esc(s.arg)}" rel="nofollow noopener" target="_blank">${esc(shortUrl(s.arg))}</a>`;
    if (s.tool === "get_json") return `${av(s.arg)}Read data from <a href="${esc(s.arg)}" rel="nofollow noopener" target="_blank">${esc(shortUrl(s.arg))}</a>`;
    if (s.tool === "grep") { const i = s.arg.lastIndexOf(" in "); const u = s.arg.slice(i + 4), pat = s.arg.slice(0, i);
      return `${av(u)}Searched inside <a href="${esc(u)}" rel="nofollow noopener" target="_blank">${esc(hostOf(u))}</a> for “${esc(pat)}”`; }
    if (s.tool === "check") return `Checked: ${esc(s.arg)}`;
    return esc(s.arg);
  };
  const PHASES = ["Check the record", "Research", "Rebuild its history", "Verify", "Ready"];
  const phaseOf = (steps, status) => {
    if (status === "ready" || status === "matched") return 5;
    if (steps.some(s => s.tool === "check")) return 3;
    const looks = steps.filter(s => s.tool === "look").length;
    if (looks >= 2) return 2;
    return steps.length > 1 ? 1 : 0;
  };
  const tb = document.querySelector("[data-track]");
  if (tb) {
    const holder = document.getElementById("signin"), note = document.getElementById("tracknote"), pv = document.getElementById("preview");
    let shown = 0, open = false, t0 = 0, clock;
    const frame = () => {
      pv.innerHTML = `<div class="run"><ol class="phases">${PHASES.map(p => `<li><i></i>${p}</li>`).join("")}</ol>`
        + `<div class="runhead"><span id="runline">Starting</span><span id="runtime">0:00</span></div><ul class="feed" id="feed"></ul><div class="srcs" id="srcs"></div>`
        + `<div class="skel" id="skel" aria-hidden="true"><b></b><u></u><u></u></div><div id="result"></div></div>`;
      shown = 0; open = false; t0 = Date.now();
      clearInterval(clock);
      clock = setInterval(() => { const s = Math.floor((Date.now() - t0) / 1000), el = document.getElementById("runtime"); if (el) el.textContent = Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); }, 1000);
    };
    const paint = (j, final) => {
      const steps = j.steps || [], ph = phaseOf(steps, j.status), failed = j.status === "failed";
      document.querySelectorAll(".phases li").forEach((li, i) => { li.className = failed && i === Math.min(ph, 3) ? "bad" : i < ph ? "done" : i === ph && !final ? "now" : ""; });
      const feed = document.getElementById("feed");
      for (; shown < steps.length; shown++) {  // only new steps are added, so each one animates in once
        const s = steps[shown], li = document.createElement("li");
        li.innerHTML = stepHtml(s);
        if (s.tool === "check") li.className = s.ok ? "ok" : "no";
        feed.appendChild(li);
      }
      const items = [...feed.querySelectorAll("li")];
      items.forEach((li, i) => { li.classList.toggle("live", !final && i === items.length - 1); if (i < items.length - 1 || final) { if (!li.classList.contains("no")) li.classList.add("ok"); const t = li.querySelector(".think"); if (t) t.remove(); } });
      const last = items[items.length - 1];
      if (last && !final && !last.querySelector(".think")) last.insertAdjacentHTML("beforeend", '<span class="think">working</span>');
      const keep = 6;  // a long run keeps its latest steps in view and folds the rest away
      if (!open) items.forEach((li, i) => { li.hidden = i < items.length - keep; });
      let more = document.getElementById("morebtn");
      if (items.length > keep && !open) {
        if (!more) { more = document.createElement("button"); more.id = "morebtn"; more.className = "more"; more.type = "button"; feed.before(more); more.onclick = () => { open = true; more.remove(); feed.querySelectorAll("li").forEach(li => { li.hidden = false; }); }; }
        more.textContent = `Show ${items.length - keep} earlier steps`;
      }
      const hosts = {};
      steps.forEach(s => { const u = s.tool === "grep" ? s.arg.slice(s.arg.lastIndexOf(" in ") + 4) : s.arg; if (["fetch", "get_json", "grep"].includes(s.tool)) { const h = hostOf(u); if (h) hosts[h] = (hosts[h] || 0) + 1; } });
      const srcs = document.getElementById("srcs"), have = new Set([...srcs.children].map(c => c.dataset.h));
      Object.keys(hosts).forEach(h => { if (!have.has(h)) { const sp = document.createElement("span"); sp.dataset.h = h; srcs.appendChild(sp); } });
      [...srcs.children].forEach(sp => { sp.textContent = sp.dataset.h + (hosts[sp.dataset.h] > 1 ? " ×" + hosts[sp.dataset.h] : ""); });
      const n = Object.keys(hosts).length, searches = steps.filter(s => s.tool === "search").length;
      document.getElementById("runline").textContent = final ? `${steps.length} steps` : `${PHASES[Math.min(ph, 4)]} · ${searches} search${searches === 1 ? "" : "es"}, ${n} site${n === 1 ? "" : "s"} read`;
    };
    const countUp = (el, text) => {  // the figure rolls up to its value instead of just appearing
      const m = text.match(/^([^\d-]*)(-?[\d.,]+)(.*)$/);
      if (!m || matchMedia("(prefers-reduced-motion: reduce)").matches) { el.textContent = text; return; }
      const target = parseFloat(m[2].replace(/,/g, "")), dec = (m[2].split(".")[1] || "").length, start = performance.now();
      const tick = now => { const k = Math.min(1, (now - start) / 700), e = 1 - Math.pow(1 - k, 3);
        el.textContent = m[1] + (target * e).toLocaleString("en-US", { minimumFractionDigits: dec, maximumFractionDigits: dec }) + m[3];
        if (k < 1) requestAnimationFrame(tick); else el.textContent = text; };
      requestAnimationFrame(tick);
    };
    const spark = (pts, holds) => {  // its history, drawn as the line it will have on its page
      if (!pts || pts.length < 2) return "";
      const w = 560, h = 96, p = 6, ts = pts.map(x => Date.parse(x[0])), vs = pts.map(x => x[1]);
      const tA = ts[0], tB = ts[ts.length - 1] > tA ? ts[ts.length - 1] : tA + 1, lo = Math.min(0, ...vs), hi = Math.max(...vs) > lo ? Math.max(...vs) : lo + 1;
      const X = t => p + (t - tA) / (tB - tA) * (w - 2 * p), Y = v => p + (1 - (v - lo) / (hi - lo)) * (h - 2 * p);
      let d = "", len = 0, px, py;
      ts.forEach((t, i) => { const x = X(t), y = Y(vs[i]);
        if (!i) d += `M${x.toFixed(1)} ${y.toFixed(1)}`; else if (holds) { d += ` H${x.toFixed(1)} V${y.toFixed(1)}`; len += Math.abs(x - px) + Math.abs(y - py); } else { d += ` L${x.toFixed(1)} ${y.toFixed(1)}`; len += Math.hypot(x - px, y - py); }
        px = x; py = y; });
      return `<svg viewBox="0 0 ${w} ${h}" style="width:100%;height:auto;display:block;margin-top:10px" role="img" aria-label="Its history"><line x1="${p}" x2="${w - p}" y1="${h - p}" y2="${h - p}" stroke="#e1e4ea"/>`
        + `<path class="drawline" style="--len:${Math.ceil(len) + 4}" d="${d}" fill="none" stroke="#2f5bff" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/><circle cx="${px.toFixed(1)}" cy="${py.toFixed(1)}" r="4.5" fill="#2f5bff"/></svg>`;
    };
    const done = j => {
      clearInterval(clock);
      paint(j, true);
      document.getElementById("skel").remove();
      const out = document.getElementById("result");
      if (j.status === "matched") { note.innerHTML = `The record already has this: <a href="${esc(j.path)}">${esc(j.title)}</a>.`; return; }
      if (j.status === "failed") {
        note.textContent = "No page could be quoted for this number, so nothing was created.";
        out.innerHTML = `<div class="card reveal" style="margin-top:14px"><b>Nothing was created</b><ul class="rows" style="margin-top:8px">${(j.reasons || []).map(r => `<li><span style="font:inherit;color:inherit;white-space:normal">✗ ${esc(r)}</span></li>`).join("")}</ul>`
          + (j.notes ? `<p class="meta">Agent's note: ${esc(j.notes)}</p>` : "") + '<p class="meta">It is on the <a href="/wanted">wanted list</a>. Try other words, or name the page that states it.</p></div>';
        return;
      }
      note.textContent = "Found. Check it, then publish.";
      const hist = (j.history || []).map((h, i) => `<tr style="animation-delay:${Math.min(i, 14) * 45}ms"><td>${esc(h.date)}</td><td>${esc(h.value)}</td><td>${h.url ? `<a href="${esc(h.url)}" rel="nofollow noopener" target="_blank">${esc(hostOf(h.url))}</a>` : ""}</td></tr>`).join("");
      const plan = j.plan || {}, every = { day: "every day", week: "every week", month: "every month" }[plan.every] || "every day";
      const how = plan.method === "json" ? "straight from a public API, with no search and no model" : plan.method === "page" ? "by reading that one page again" : "by searching again, since the value moves between pages";
      out.innerHTML = `<div class="card reveal" style="margin-top:14px;background:var(--paper)"><b style="font-size:18px">${esc(j.title)}</b><p class="q" style="margin:2px 0 10px">${esc(j.question)}</p>`
        + `<div class="val" style="font-size:44px" id="bigval"></div>` + spark(j.points, j.holds) + (j.quote ? `<blockquote style="margin-top:12px">${esc(j.quote)}</blockquote>` : "")
        + `<p class="meta">Read from <a href="${esc(j.url)}" rel="nofollow noopener" target="_blank">${esc(j.url)}</a></p>`
        + (hist ? `<p class="meta" style="margin-top:12px">${j.history.length} past values, each from a dated source</p><table class="pts"><tr><th>When</th><th>Value</th><th>Source</th></tr>${hist}</table>`
                : `<p class="meta"><b style="color:#d92d20">No past values could be proven.</b> ${esc(j.no_past || "")} Its history would start today.</p>`)
        + `<p class="meta" style="margin-top:12px">Kept current ${every}, ${how}.</p>` + (j.notes ? `<p class="meta">Agent's note: ${esc(j.notes)}</p>` : "")
        + `<details style="margin-top:10px"><summary class="meta" style="cursor:pointer">The recipe the agent wrote for this number</summary><pre style="white-space:pre-wrap;font:13px/1.5 var(--mono);margin:8px 0 0">${esc(j.recipe || "")}</pre></details>`
        + `<div class="cta" style="margin-top:14px"><button class="follow" type="button" id="pub">Publish this number</button></div><p class="meta" id="pubnote">Publishing makes the page public and puts it on your board.</p></div>`;
      countUp(document.getElementById("bigval"), j.value);
      out.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "nearest" });
      document.getElementById("pub").addEventListener("click", async ev => {
        ev.target.disabled = true; ev.target.textContent = "Publishing…";
        try { const r = await post("/api/numbers/publish", { draft: j.draft }); location.href = r.path; }
        catch (e) { ev.target.disabled = false; ev.target.textContent = "Publish this number"; document.getElementById("pubnote").textContent = e.message; }
      });
    };
    tb.addEventListener("click", async () => {
      const q = document.getElementById("trackq").value.trim();
      if (!q) { note.textContent = "Type the number you want, for example “Stripe latest valuation”."; return; }
      const m = await me();
      if (!m.user && !(await signIn(holder, "Sign in to create a number. It is yours to follow, and a free account can create one a day."))) return;
      tb.disabled = true; note.textContent = "An agent is researching it. You can watch every step; this takes a minute or two.";
      try {
        const d = await post("/api/numbers/draft", { question: q });
        frame();
        for (;;) {
          await new Promise(r => setTimeout(r, 1200));
          const j = await fetch("/api/numbers/draft/" + d.draft).then(r => r.json());
          if (j.status === "working") { paint(j, false); continue; }
          done(j); break;
        }
      } catch (e) { clearInterval(clock); note.innerHTML = esc(e.message) + (e.status === 402 ? ' <a href="/app?plans=1">See the plans</a>' : ""); }
      tb.disabled = false;
    });
  }
})();
