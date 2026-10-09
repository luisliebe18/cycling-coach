/* Canvas-Charts ohne externe Abhängigkeiten:
   - lineChart: Zeitreihen mit Zoom (Drag-Selektion), Doppelklick = Reset,
     gemeinsamer Cursor + Tooltip über mehrere Charts (syncGroup).
   - barChart / hbarChart: Balken.
   - trackMap: einfache GPS-Darstellung (equirektangular, Maßstab cos(Mittelbreite)). */

const CHARTS = { registry: {}, uid: 0 };

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function fmtDur(s) {
  s = Math.round(s || 0);
  if (s < 60) return s + ' s';
  if (s < 3600) return (s / 60).toFixed(0) + ' min';
  const h = Math.floor(s / 3600);
  return h + ' h ' + Math.round((s % 3600) / 60) + ' min';
}

function niceTicks(min, max, count = 5) {
  const span = (max - min) || 1;
  const step0 = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const norm = step0 / mag;
  const step = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag;
  const ticks = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) ticks.push(v);
  return ticks;
}

class LineChart {
  constructor(canvas, opts) {
    this.canvas = canvas;
    this.opts = opts;                 // {series:[{name,color,pts:[[x,y],...]}], xLabel, yLabel, sync, fmtX, fmtY, markers}
    this.id = ++CHARTS.uid;
    this.view = null;                 // [x0,x1] nach Zoom
    this.selStart = null;
    this._bind();
  }
  allPts() { return this.opts.series.flatMap(s => s.pts.filter(p => p[1] != null)); }
  dataRange() {
    const pts = this.allPts();
    if (!pts.length) return [0, 1];
    let x0 = Infinity, x1 = -Infinity;
    for (const [x] of pts) { if (x < x0) x0 = x; if (x > x1) x1 = x; }
    if (x1 === x0) x1 = x0 + 1;
    return [x0, x1];
  }
  range() { return this.view || this.dataRange(); }
  yRange() {
    const [x0, x1] = this.range();
    let ymin = Infinity, ymax = -Infinity;
    for (const s of this.opts.series)
      for (const [x, y] of s.pts) {
        if (y == null || x < x0 || x > x1) continue;
        if (y < ymin) ymin = y; if (y > ymax) ymax = y;
      }
    if (!isFinite(ymin)) return [0, 1];
    if (ymax === ymin) { ymax += 1; ymin -= 1; }
    const pad = (ymax - ymin) * 0.07;
    return [Math.max(0, ymin - pad), ymax + pad];
  }
  _bind() {
    const c = this.canvas;
    c.addEventListener('mousedown', e => { this.selStart = this._x(e); });
    c.addEventListener('mousemove', e => {
      if (this.selStart != null && this._x(e) !== null) this.selEnd = this._x(e);
      else this.selEnd = null;
      const x = this._x(e);
      this.cursor = x;
      CHARTS.registry[this.opts.sync]?.forEach(ch => ch.cursor = x);
      this.draw();
      CHARTS.registry[this.opts.sync]?.forEach(ch => { if (ch !== this) ch.draw(); });
      this._tip(e, x);
    });
    c.addEventListener('mouseleave', () => {
      this.cursor = null; this._hideTip();
      CHARTS.registry[this.opts.sync]?.forEach(ch => { ch.cursor = null; ch.draw(); });
    });
    c.addEventListener('mouseup', () => {
      if (this.selStart != null && this.selEnd != null && Math.abs(this.selEnd - this.selStart) > (this.range()[1] - this.range()[0]) * 0.02) {
        this.view = [Math.min(this.selStart, this.selEnd), Math.max(this.selStart, this.selEnd)];
        this.draw();
      }
      this.selStart = this.selEnd = null;
    });
    c.addEventListener('dblclick', () => { this.view = null; this.draw(); });
  }
  _px(e) {
    const r = this.canvas.getBoundingClientRect();
    return { px: (e.clientX - r.left) * (this.canvas.width / r.width),
             py: (e.clientY - r.top) * (this.canvas.height / r.height),
             cx: e.clientX - r.left, cy: e.clientY - r.top };
  }
  _x(e) {
    const p = this._px(e);
    const g = this.geom();
    if (!g || p.px < g.l || p.px > g.l + g.w) return null;
    const [x0, x1] = this.range();
    return x0 + (p.px - g.l) / g.w * (x1 - x0);
  }
  geom() {
    const W = this.canvas.width, H = this.canvas.height;
    if (!W || !H) return null;
    return { l: 46, t: 10, w: W - 60, h: H - 38 };
  }
  _tip(e, x) {
    const tip = this.canvas.parentElement.querySelector('.chart-tip');
    if (!tip || x == null) return;
    const rows = [];
    let shown = false;
    for (const s of this.opts.series) {
      if (!s.pts.some(p => p[1] != null)) continue;
      let best = null, bd = Infinity;
      for (const [px, py] of s.pts) {
        if (py == null) continue;
        const d = Math.abs(px - x);
        if (d < bd) { bd = d; best = [px, py]; }
      }
      if (best) {
        shown = true;
        const f = s.fmt || (v => Math.round(v) + (s.unit ? ' ' + s.unit : ''));
        rows.push(`<span style="color:${s.color}">●</span> ${s.name}: <b>${f(best[1])}</b>`);
      }
    }
    if (!shown) { tip.style.display = 'none'; return; }
    const fx = this.opts.fmtX || (v => String(Math.round(v)));
    tip.innerHTML = `<b>${fx(x)}</b>` + rows.join('<br>');
    tip.style.display = 'block';
    const box = this.canvas.parentElement.getBoundingClientRect();
    let lx = e.clientX - box.left + 14, ly = e.clientY - box.top + 10;
    if (lx + 170 > box.width) lx = e.clientX - box.left - 180;
    tip.style.left = lx + 'px'; tip.style.top = ly + 'px';
  }
  _hideTip() {
    const tip = this.canvas.parentElement.querySelector('.chart-tip');
    if (tip) tip.style.display = 'none';
  }
  draw() {
    const c = this.canvas, ctx = c.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const cw = c.clientWidth || 600;
    if (c.width !== Math.round(cw * dpr)) { c.width = Math.round(cw * dpr); c.height = Math.round((this.opts.height || 220) * dpr); }
    ctx.clearRect(0, 0, c.width, c.height);
    const g = this.geom(); if (!g) return;
    const [x0, x1] = this.range(), [y0, y1] = this.yRange();
    const X = v => g.l + (v - x0) / (x1 - x0) * g.w;
    const Y = v => g.t + g.h - (v - y0) / (y1 - y0) * g.h;
    const fg = cssVar('--fg') || '#ddd', mut = cssVar('--muted') || '#888', grid = cssVar('--grid') || '#222';
    ctx.font = `${11 * dpr}px system-ui`; ctx.textBaseline = 'middle';
    // Gitter + Achsen
    ctx.strokeStyle = grid; ctx.fillStyle = mut; ctx.lineWidth = 1;
    for (const ty of niceTicks(y0, y1, 4)) {
      const py = Y(ty);
      ctx.beginPath(); ctx.moveTo(g.l, py); ctx.lineTo(g.l + g.w, py); ctx.stroke();
      ctx.textAlign = 'right';
      ctx.fillText(String(Math.round(ty * 10) / 10), g.l - 5, py);
    }
    const fx = this.opts.fmtX || (v => String(Math.round(v)));
    ctx.textAlign = 'center';
    for (const tx of niceTicks(x0, x1, 6)) {
      if (tx < x0 || tx > x1) continue;
      ctx.fillText(fx(tx), X(tx), g.t + g.h + 14 * dpr);
    }
    if (this.opts.yLabel) { ctx.save(); ctx.translate(12 * dpr, g.t + g.h / 2); ctx.rotate(-Math.PI / 2); ctx.textAlign = 'center'; ctx.fillText(this.opts.yLabel, 0, 0); ctx.restore(); }
    // Referenzlinien (z.B. FTP)
    for (const m of (this.opts.markers || [])) {
      if (m.value < y0 || m.value > y1) continue;
      ctx.strokeStyle = m.color || cssVar('--warn'); ctx.setLineDash([5 * dpr, 4 * dpr]);
      ctx.beginPath(); ctx.moveTo(g.l, Y(m.value)); ctx.lineTo(g.l + g.w, Y(m.value)); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = ctx.strokeStyle; ctx.textAlign = 'left';
      ctx.fillText(m.label || '', g.l + 4, Y(m.value) - 7 * dpr);
    }
    // Serien
    for (const s of this.opts.series) {
      if (!s.pts.length) continue;
      ctx.strokeStyle = s.color || cssVar('--accent'); ctx.lineWidth = (s.width || 1.6) * dpr;
      ctx.beginPath();
      let started = false;
      for (const [px, py] of s.pts) {
        if (py == null) { started = false; continue; }
        if (px < x0 || px > x1) { started = false; continue; }
        const cx = X(px), cy = Y(py);
        if (!started) { ctx.moveTo(cx, cy); started = true; } else ctx.lineTo(cx, cy);
      }
      ctx.stroke();
      if (s.fill) {
        ctx.globalAlpha = 0.12; ctx.fillStyle = s.color;
        ctx.lineTo(X(Math.min(x1, s.pts[s.pts.length - 1][0])), Y(y0));
        ctx.lineTo(X(Math.max(x0, s.pts[0][0])), Y(y0));
        ctx.closePath(); ctx.fill(); ctx.globalAlpha = 1;
      }
    }
    // Cursor
    if (this.cursor != null && this.cursor >= x0 && this.cursor <= x1) {
      ctx.strokeStyle = mut; ctx.setLineDash([3 * dpr, 3 * dpr]);
      ctx.beginPath(); ctx.moveTo(X(this.cursor), g.t); ctx.lineTo(X(this.cursor), g.t + g.h); ctx.stroke();
      ctx.setLineDash([]);
    }
    // Auswahlintervall beim Ziehen
    if (this.selStart != null && this.selEnd != null) {
      ctx.fillStyle = 'rgba(88,166,255,.15)';
      ctx.fillRect(X(Math.min(this.selStart, this.selEnd)), g.t,
                   Math.abs(X(this.selEnd) - X(this.selStart)), g.h);
    }
  }
}

function registerSync(group) { CHARTS.registry[group] = []; }
function makeLine(canvas, opts) {
  const ch = new LineChart(canvas, opts);
  canvas._chart = ch;                 // für Resize-Neuzeichnung
  if (opts.sync) (CHARTS.registry[opts.sync] ||= []).push(ch);
  requestAnimationFrame(() => ch.draw());
  return ch;
}

function barChart(canvas, labels, values, colors, fmt) {
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const H = opts_h(canvas);
  if (canvas.width !== Math.round(canvas.clientWidth * dpr)) { canvas.width = Math.round(canvas.clientWidth * dpr); canvas.height = Math.round(H * dpr); }
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const n = values.length; if (!n) return;
  const max = Math.max(...values, 1);
  const bw = canvas.width / n;
  const mut = cssVar('--muted'), fg = cssVar('--fg');
  ctx.font = `${10 * dpr}px system-ui`;
  for (let i = 0; i < n; i++) {
    const h = (values[i] / max) * (canvas.height - 34 * dpr);
    ctx.fillStyle = colors?.[i] || cssVar('--accent');
    ctx.fillRect(i * bw + bw * 0.15, canvas.height - 22 * dpr - h, bw * 0.7, h);
    ctx.fillStyle = mut; ctx.textAlign = 'center';
    ctx.fillText(labels[i], i * bw + bw / 2, canvas.height - 8 * dpr);
    if (values[i] > 0) { ctx.fillStyle = fg; ctx.fillText(fmt ? fmt(values[i]) : String(Math.round(values[i])), i * bw + bw / 2, canvas.height - 28 * dpr - h); }
  }
}
function opts_h(c) { return parseInt(c.getAttribute('data-h') || '180'); }

/* Minimale GPS-Karte: Punkte als Polyline, keine externen Tiles (datenschutzfreundlich) */
function trackMap(canvas, lats, lons) {
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const H = opts_h(canvas);
  canvas.width = Math.round(canvas.clientWidth * dpr); canvas.height = Math.round(H * dpr);
  const pts = [];
  for (let i = 0; i < lats.length; i++)
    if (lats[i] != null && lons[i] != null) pts.push([lons[i], lats[i]]);
  ctx.fillStyle = cssVar('--bg3'); ctx.fillRect(0, 0, canvas.width, canvas.height);
  if (pts.length < 2) {
    ctx.fillStyle = cssVar('--muted'); ctx.font = `${12 * dpr}px system-ui`; ctx.textAlign = 'center';
    ctx.fillText('Keine GPS-Daten', canvas.width / 2, canvas.height / 2);
    return;
  }
  const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
  const minx = Math.min(...xs), maxx = Math.max(...xs), miny = Math.min(...ys), maxy = Math.max(...ys);
  const midLat = (miny + maxy) / 2;
  const scaleX = Math.cos(midLat * Math.PI / 180);
  const wSpan = ((maxx - minx) * scaleX) || 1e-6, hSpan = (maxy - miny) || 1e-6;
  const pad = 14 * dpr;
  const sc = Math.min((canvas.width - 2 * pad) / wSpan, (canvas.height - 2 * pad) / hSpan);
  const ox = (canvas.width - wSpan * sc) / 2, oy = (canvas.height - hSpan * sc) / 2;
  ctx.strokeStyle = cssVar('--accent'); ctx.lineWidth = 2 * dpr; ctx.lineJoin = 'round';
  ctx.beginPath();
  pts.forEach(([lon, lat], i) => {
    const x = ox + (lon - minx) * scaleX * sc, y = oy + (maxy - lat) * sc;
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  });
  ctx.stroke();
  // Start/Ende
  const P = ([lon, lat]) => [ox + (lon - minx) * scaleX * sc, oy + (maxy - lat) * sc];
  const [sx, sy] = P(pts[0]), [ex, ey] = P(pts[pts.length - 1]);
  ctx.fillStyle = cssVar('--accent2'); ctx.beginPath(); ctx.arc(sx, sy, 5 * dpr, 0, 7); ctx.fill();
  ctx.fillStyle = cssVar('--danger'); ctx.beginPath(); ctx.arc(ex, ey, 5 * dpr, 0, 7); ctx.fill();
  ctx.fillStyle = cssVar('--muted'); ctx.font = `${10 * dpr}px system-ui`; ctx.textAlign = 'left';
  ctx.fillText(`${pts.length} GPS-Punkte · grün=Start, rot=Ende`, pad, canvas.height - pad);
}

window.addEventListener('resize', () => {
  // Charts des aktuellen Views neu zeichnen (Registry wird beim View-Wechsel geleert)
  CHARTS.registry = {};
  document.querySelectorAll('#view canvas.chart').forEach(c => { if (c._chart) c._chart.draw(); });
});
