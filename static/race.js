// A ranking replayed over time, in the manner of the bar chart races people know: time runs at one pace, each company keeps
// its colour, the date sits large in the corner, a caption marks each change of leader. The values are the record's own:
// a bar only changes on the day a new figure was stated. The glide between two frames is drawn, not data.
(function () {
  var box = document.getElementById('race'), src = document.getElementById('race-data');
  if (!box || !src) return;
  var d = JSON.parse(src.textContent), dates = d.dates, rows = d.rows, top = d.top, unit = d.unit || '';
  var cur = /^(usd|eur|gbp)\b/i.exec(unit), sign = cur ? {usd: '$', eur: '€', gbp: '£'}[cur[1].toLowerCase()] : '';
  var still = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  var PAL = ['#2f6feb', '#e8590c', '#2b8a3e', '#c2255c', '#6741d9', '#0c8599', '#e67700', '#5f3dc4', '#087f5b', '#d6336c', '#1971c2', '#a61e4d', '#5c940d', '#862e9c'];
  var H = 34, i = dates.length - 1, timer = null, speed = 1, STEP = 420;
  function cut(x) { return x.toFixed(2).replace(/\.?0+$/, ''); }
  function fmt(v) {
    var a = Math.abs(v), t;
    if (a >= 1e12) t = cut(a / 1e12) + 'T'; else if (a >= 1e9) t = cut(a / 1e9) + 'B'; else if (a >= 1e6) t = cut(a / 1e6) + 'M';
    else t = a === Math.round(a) ? Math.round(a).toLocaleString('en-US') : cut(a);
    return (v < 0 ? '-' : '') + sign + t;
  }
  function nice(max) { var p = Math.pow(10, Math.floor(Math.log(max) / Math.LN10)), m = max / p; return (m >= 5 ? 2 : m >= 2 ? 1 : 0.5) * p; }
  var MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  box.innerHTML = '<div class="race-bar"><button type="button" class="btn" id="race-play">Play</button><button type="button" class="btn ghost" id="race-speed" title="Speed">1×</button>' +
    '<input id="race-at" type="range" min="0" max="' + (dates.length - 1) + '" value="' + i + '" aria-label="Date"></div>' +
    '<div id="race-stage" style="position:relative;height:' + (top * H + 22) + 'px"><div id="race-ticks"></div><div id="race-rows" style="position:absolute;left:0;right:0;top:22px;height:' + (top * H) + 'px"></div>' +
    '<div id="race-day"><b></b><span></span><em></em></div></div>' +
    '<p class="race-note">Each bar changes only on the day a new figure was stated; a company keeps its last stated value until then. Nothing is estimated in between.</p>';
  var holder = document.getElementById('race-rows'), play = document.getElementById('race-play'), at = document.getElementById('race-at'), lab = document.getElementById('race-day'),
      ticks = document.getElementById('race-ticks'), sp = document.getElementById('race-speed');
  var els = rows.map(function (r, k) {
    var e = document.createElement('a');
    e.href = r.p; e.className = 'race-row';
    e.innerHTML = '<span class="race-k"></span><span class="race-n"></span><span class="race-t"><i></i><span class="race-v"></span></span>';
    e.children[1].textContent = r.n;
    e.children[2].firstChild.style.background = PAL[k % PAL.length];
    holder.appendChild(e);
    return e;
  });
  function pace() {
    var ms = still ? 0 : Math.round(STEP / speed);
    els.forEach(function (e) {
      e.style.transition = 'transform ' + ms + 'ms linear, opacity ' + Math.min(ms, 300) + 'ms linear';
      e.children[2].firstChild.style.transition = 'width ' + ms + 'ms linear';
    });
  }
  var lead = null, said = '', saidAt = -99;
  function draw(jump) {
    var live = rows.map(function (r, k) { return {k: k, v: r.v[i]}; }).filter(function (x) { return x.v !== null; });
    live.sort(function (a, b) { return b.v - a.v; });
    var max = live.reduce(function (m, x) { return Math.max(m, Math.abs(x.v)); }, 0) || 1, place = {};
    live.forEach(function (x, n) { place[x.k] = n; });
    els.forEach(function (e, k) {
      var n = place[k], on = n !== undefined && n < top;
      e.style.opacity = on ? 1 : 0; e.style.pointerEvents = on ? '' : 'none';
      e.style.transform = 'translateY(' + ((on ? n : top) * H) + 'px)';
      if (n !== undefined) {
        e.children[0].textContent = on ? n + 1 : '';
        e.children[2].firstChild.style.width = Math.max(0.5, Math.abs(rows[k].v[i]) / max * 86) + '%';
        e.children[2].lastChild.textContent = fmt(rows[k].v[i]);
      }
    });
    var step = nice(max), html = '';
    for (var x = step; x <= max * 1.0001; x += step) html += '<u style="left:' + (x / max * 86) + '%">' + fmt(x) + '</u>';
    ticks.innerHTML = html;
    var now = live.length ? rows[live[0].k].n : null;
    if (jump) { said = ''; } else if (now && lead && now !== lead) { said = now + ' takes the lead'; saidAt = i; }
    if (i - saidAt > 8 || i < saidAt) said = '';
    lead = now;
    var p = dates[i].split('-');
    lab.children[0].textContent = p[0]; lab.children[1].textContent = MON[+p[1] - 1]; lab.children[2].textContent = said;
    at.value = i;
  }
  function label() { play.textContent = timer ? 'Pause' : i >= dates.length - 1 ? 'Replay' : 'Play'; }
  function stop() { clearInterval(timer); timer = null; label(); }
  function go() {
    if (i >= dates.length - 1) { i = 0; lead = null; draw(true); }
    timer = setInterval(function () { if (i < dates.length - 1) { i++; draw(); } if (i >= dates.length - 1) stop(); }, Math.round((still ? 900 : STEP) / speed));
    label();
  }
  play.onclick = function () { timer ? stop() : go(); };
  sp.onclick = function () { speed = speed === 1 ? 2 : speed === 2 ? 0.5 : 1; sp.textContent = (speed === 0.5 ? '½' : speed) + '×'; pace(); if (timer) { stop(); go(); } };
  at.oninput = function () { if (timer) stop(); i = +at.value; lead = null; draw(true); label(); };
  pace(); draw(true); label();
  if (/[?&]play=1/.test(location.search)) go();
})();
