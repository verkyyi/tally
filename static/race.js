// A ranking replayed over time. The data is the page's own: dated points already on record, held until the next disclosure.
(function () {
  var box = document.getElementById('race'), src = document.getElementById('race-data');
  if (!box || !src) return;
  var d = JSON.parse(src.textContent), dates = d.dates, rows = d.rows, top = d.top, unit = d.unit || '';
  var cur = /^(usd|eur|gbp)\b/i.exec(unit), sign = cur ? {usd: '$', eur: '€', gbp: '£'}[cur[1].toLowerCase()] : '';
  var still = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  var H = 30, i = dates.length - 1, timer = null;
  function fmt(v) {
    var a = Math.abs(v), t;
    function cut(x) { return x.toFixed(2).replace(/\.?0+$/, ''); }
    if (a >= 1e12) t = cut(a / 1e12) + 'T'; else if (a >= 1e9) t = cut(a / 1e9) + 'B'; else if (a >= 1e6) t = cut(a / 1e6) + 'M';
    else t = a === Math.round(a) ? Math.round(a).toLocaleString('en-US') : cut(a);
    return (v < 0 ? '-' : '') + sign + t;
  }
  function day(s) { return new Date(s + 'T00:00:00Z').toLocaleDateString('en-US', {year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC'}); }
  box.innerHTML = '<div class="race-bar"><button type="button" class="btn" id="race-play">Play</button><input id="race-at" type="range" min="0" max="' + (dates.length - 1) +
    '" value="' + i + '" aria-label="Date"><b id="race-day"></b></div><div id="race-rows" style="position:relative;height:' + (top * H) + 'px"></div>' +
    '<p class="race-note">Each company keeps its last stated value until its next dated disclosure. Nothing is estimated in between.</p>';
  var holder = document.getElementById('race-rows'), play = document.getElementById('race-play'), at = document.getElementById('race-at'), lab = document.getElementById('race-day');
  var els = rows.map(function (r) {
    var e = document.createElement('a');
    e.href = r.p; e.className = 'race-row';
    e.innerHTML = '<span class="race-n"></span><span class="race-t"><i></i></span><span class="race-v"></span>';
    e.firstChild.textContent = r.n;
    if (still) e.style.transition = 'none';
    holder.appendChild(e);
    return e;
  });
  function draw() {
    var live = rows.map(function (r, k) { return {k: k, v: r.v[i]}; }).filter(function (x) { return x.v !== null; });
    live.sort(function (a, b) { return b.v - a.v; });
    var max = live.reduce(function (m, x) { return Math.max(m, Math.abs(x.v)); }, 0) || 1, place = {};
    live.forEach(function (x, n) { place[x.k] = n; });
    els.forEach(function (e, k) {
      var n = place[k], on = n !== undefined && n < top;
      e.style.opacity = on ? 1 : 0; e.style.pointerEvents = on ? '' : 'none';
      e.style.transform = 'translateY(' + ((on ? n : top) * H) + 'px)';
      if (n !== undefined) {
        var bar = e.children[1].firstChild;
        bar.style.width = Math.max(1, Math.abs(rows[k].v[i]) / max * 100) + '%';
        bar.style.background = rows[k].v[i] < 0 ? '#c0392b' : '';
        e.children[2].textContent = fmt(rows[k].v[i]);
      }
    });
    lab.textContent = day(dates[i]); at.value = i;
  }
  function stop() { clearInterval(timer); timer = null; play.textContent = i >= dates.length - 1 ? 'Replay' : 'Play'; }
  function go() {
    if (i >= dates.length - 1) i = 0;
    play.textContent = 'Pause'; draw();
    timer = setInterval(function () { if (i >= dates.length - 1) return stop(); i++; draw(); if (i >= dates.length - 1) stop(); }, still ? 1200 : 800);
  }
  play.onclick = function () { timer ? stop() : go(); };
  at.oninput = function () { if (timer) stop(); i = +at.value; draw(); play.textContent = i >= dates.length - 1 ? 'Replay' : 'Play'; };
  draw(); play.textContent = 'Replay';
  if (/[?&]play=1/.test(location.search)) go();
})();
