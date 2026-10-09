/* Views: render functions. Alle Daten kommen aus der API — nichts erfunden. */

const V = {};
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const nf = (v, d = 1) => v == null ? '—' : Number(v).toLocaleString('de-DE', { maximumFractionDigits: d });
const fmtKm = m => m == null ? '—' : nf(m / 1000, 1) + ' km';
const fmtH = s => s == null ? '—' : fmtDur(s);
const fmtDate = iso => iso ? String(iso).slice(0, 16).replace('T', ' ') : '—';
const chartBox = (id, h = 220) => `<div class="chart-box"><canvas id="${id}" class="chart" data-h="${h}"></canvas><div class="chart-tip"></div></div>`;

function emptyState(text, sub = '') {
  return `<div class="empty-state"><div class="big">📊</div><h3>${esc(text)}</h3><p class="muted">${esc(sub)}</p></div>`;
}

async function importData() { return { settings: await API.get('/settings') }; }

/* ------------------------------ Dashboard ------------------------------- */
V.dashboard = async (el) => {
  const d = await API.get('/dashboard');
  if (d.empty) { el.innerHTML = emptyState('Noch keine Aktivitäten im Bestand.',
    'Importiere zuerst FIT/TCX/GPX-Dateien oder einen Strava-Datenexport unter „Import“.'); return; }
  const r = d.ranges, f = d.fitness, rd = d.readiness, rec = d.today_recommendation;
  const rdBadge = !rd ? '' :
    `<span class="badge ${rd.category === 'gut_erholt' ? 'good' : rd.category === 'normal' ? 'info' : rd.category === 'erhoehte_ermuedung' ? 'warn' : 'bad'}">${esc(rd.label)} (${rd.score}/100)</span>`;
  el.innerHTML = `
  <div class="grid cols-4">
    ${kpi('Letzte 7 Tage', r['7d']?.rides + ' Fahrten', `${nf(r['7d']?.km,1)} km · ${nf(r['7d']?.hours,1)} h`)}
    ${kpi('Letzte 28 Tage', r['28d']?.rides + ' Fahrten', `${nf(r['28d']?.km,1)} km · ${nf(r['28d']?.tss,0)} TSS`)}
    ${kpi('FTP', d.ftp?.w ? d.ftp.w + ' W' : 'nicht gesetzt', d.ftp?.wkg ? d.ftp.wkg + ' W/kg · Quelle: ' + d.ftp.source : 'in Einstellungen setzen')}
    ${kpi('Form (TSB)', f ? nf(f.tsb, 0) : '—', f ? `CTL ${nf(f.ctl,0)} · ATL ${nf(f.atl,0)}` : 'keine Belastungsdaten')}
  </div>
  <div class="grid cols-2" style="margin-top:14px">
    <div class="card">
      <h3>Tageszustand & Coach-Empfehlung</h3>
      <div style="margin-bottom:8px">${rdBadge} ${rd ? `<span class="muted small">Sicherheit: ${esc(rd.confidence)}</span>` : ''}</div>
      ${rec ? `<p><b>Heute:</b> ${esc(rec.name)} <span class="muted">(${rec.duration_min || 0} min${rec.zone ? ', ' + esc(rec.zone) : ''})</span></p>` : '<p class="muted">Noch keine heutige Empfehlung – im Coach-Bereich generieren.</p>'}
      ${(d.readiness_factors || []).length ? `<ul class="muted small">${d.readiness_factors.map(x => `<li>${esc(x.text)}</li>`).join('')}</ul>` : ''}
      ${d.coach_error ? `<div class="error">Coach: ${esc(d.coach_error)}</div>` : ''}
    </div>
    <div class="card">
      <h3>Nächste geplante Einheiten</h3>
      ${(d.planned || []).length ? d.planned.map(p => `<div class="plan-day"><b>${esc(p.day)}</b> — ${esc(p.title)}</div>`).join('')
        : '<p class="muted">Keine geplanten Workouts. Siehe Trainingsplan.</p>'}
    </div>
  </div>
  <div class="card">
    <h3>Belastungsverlauf (CTL / ATL / TSB)</h3>
    ${f ? chartBox('dashFit', 240) : '<p class="muted">Keine TSS-Daten — FTP setzen und Aktivitäten neu verarbeiten (Einstellungen → Änderungen berechnen alles neu).</p>'}
  </div>
  <div class="grid cols-2">
    <div class="card"><h3>Persönliche Bestleistungen (Auszug)</h3>${pbTableSmall(d.best_efforts)}</div>
    <div class="card"><h3>Volumen</h3>
      <table><tr><th>Zeitraum</th><th>Fahrten</th><th>KM</th><th>Zeit</th><th>Höhenmeter</th><th>TSS</th></tr>
      ${['7d','28d','month','year','all'].map(k => r[k] ? `<tr><td>${{'7d':'7 Tage','28d':'28 Tage','month':'Monat','year':'Jahr','all':'Gesamt'}[k]}</td>
        <td>${r[k].rides}</td><td>${nf(r[k].km,1)}</td><td>${nf(r[k].hours,1)} h</td><td>${nf(r[k].elev_m,0)} m</td><td>${nf(r[k].tss,0)}</td></tr>` : '').join('')}
      </table>
    </div>
  </div>`;
  if (f && d.fitness_series?.length) {
    const pts = d.fitness_series.map((x, i) => [i, x]);
    makeLine(document.getElementById('dashFit'), { height: 240, yLabel: '', fmtX: i => {
      const x = d.fitness_series[Math.round(i)]; return x ? x.date.slice(5) : '';
    }, series: [
      { name: 'CTL', color: cssVar('--accent2'), pts: pts.map(p => [p[0], p[1].ctl]) },
      { name: 'ATL', color: cssVar('--warn'), pts: pts.map(p => [p[0], p[1].atl]) },
      { name: 'TSB', color: cssVar('--accent'), pts: pts.map(p => [p[0], p[1].tsb]) },
    ]});
  }
};
function kpi(l, v, s) { return `<div class="kpi"><div class="l">${esc(l)}</div><div class="v">${v ?? '—'}</div><div class="s">${esc(s || '')}</div></div>`; }
function pbTableSmall(rows) {
  const key = [5, 60, 300, 1200, 3600];
  const sel = (rows || []).filter(x => key.includes(x.duration_s));
  if (!sel.length) return '<p class="muted">Noch keine Bestleistungen — Watt-Daten werden beim Import berechnet.</p>';
  return `<div class="tablewrap"><table><tr><th>Dauer</th><th>Watt</th><th>W/kg</th><th>Datum</th><th>Aktivität</th></tr>
    ${sel.map(x => `<tr><td>${fmtDur(x.duration_s)}</td><td>${nf(x.watts,0)} W</td><td>${nf(x.wkg,2)}</td>
      <td>${esc(x.achieved_on || '')}</td><td>${x.activity_id ? `<a href="#/activity/${x.activity_id}">${esc(x.name || '#' + x.activity_id)}</a>` : '—'}</td></tr>`).join('')}</table></div>`;
}

/* ---------------------------- Aktivitäten ------------------------------- */
V.activities = async (el) => {
  el.innerHTML = `
  <div class="card">
    <form class="inline" id="actFilters">
      <label>Von <input type="date" name="from"></label>
      <label>Bis <input type="date" name="to"></label>
      <label>Typ <select name="type"><option value="">alle</option></select></label>
      <label>Leistung <select name="power"><option value="">alle</option><option value="yes">mit Watt</option><option value="no">ohne Watt</option></select></label>
      <label>Ort <select name="indoor"><option value="">alle</option><option value="no">draußen</option><option value="yes">indoor</option></select></label>
      <label>min km <input type="number" name="min_km" step="0.1" min="0" style="width:90px"></label>
      <button class="btn" type="submit">Filtern</button>
      <button class="btn ghost" type="reset">Zurücksetzen</button>
    </form>
  </div>
  <div class="card tablewrap" id="actTable"><div class="loading">Lädt…</div></div>`;
  const st = { sort: 'date', dirn: 'desc', offset: 0, limit: 50, filters: {} };
  const cols = [['date','Datum'],['name','Name'],['distance','Distanz'],['duration','Dauer'],['elev','HM'],
    ['avg_hr','HF Ø'],['power','Ø W'],['np','NP'],['if','IF'],['tss','TSS'],['work','kJ'],['cadence','Kadenz'],['speed','km/h']];
  async function load() {
    const q = new URLSearchParams({ sort: st.sort, dirn: st.dirn, limit: st.limit, offset: st.offset, ...st.filters });
    const res = await API.get('/activities?' + q);
    const t = document.getElementById('actTable');
    if (!res.items.length && !res.total) { t.innerHTML = emptyState('Keine Aktivitäten gefunden.', 'Zum Start Dateien unter „Import“ hochladen.'); return; }
    t.innerHTML = `<table><tr>
      ${cols.map(([k, l]) => `<th class="sortable" data-k="${k}">${l}${st.sort === k ? (st.dirn === 'asc' ? ' ▲' : ' ▼') : ''}</th>`).join('')}
      </tr>${res.items.map(a => `<tr class="clickable" data-id="${a.id}">
        <td>${fmtDate(a.start_time)}</td>
        <td>${esc(a.name)} ${a.power_data ? '' : '<span class="badge warn" title="Keine Wattdaten in dieser Aktivität">kein Power</span>'}</td>
        <td>${fmtKm(a.distance_m)}</td><td>${fmtH(a.moving_time_s || a.duration_s)}</td>
        <td>${nf(a.elevation_gain_m, 0)}</td><td>${nf(a.avg_hr, 0)}</td>
        <td>${a.power_data ? nf(a.avg_power, 0) : '—'}</td><td>${a.np_power ? nf(a.np_power, 0) : '—'}</td>
        <td>${a.if_value != null ? nf(a.if_value, 2) : '—'}</td><td>${a.tss != null ? nf(a.tss, 0) : '—'}</td>
        <td>${a.work_kj ? nf(a.work_kj, 0) : '—'}</td><td>${nf(a.avg_cadence, 0)}</td>
        <td>${a.avg_speed ? nf(a.avg_speed * 3.6, 1) : '—'}</td></tr>`).join('')}
      </table>
      <div style="display:flex;gap:8px;align-items:center;margin-top:10px">
        <button class="btn small" id="pgPrev" ${st.offset ? '' : 'disabled'}>◀ Zurück</button>
        <span class="muted small">Seite ab ${st.offset + 1} von ${res.total} · ${res.items.length} gezeigt</span>
        <button class="btn small" id="pgNext" ${st.offset + st.limit >= res.total ? 'disabled' : ''}>Weiter ▶</button>
      </div>`;
    t.querySelectorAll('th.sortable').forEach(th => th.onclick = () => {
      const k = th.dataset.k;
      if (st.sort === k) st.dirn = st.dirn === 'asc' ? 'desc' : 'asc'; else { st.sort = k; st.dirn = 'desc'; }
      st.offset = 0; load();
    });
    t.querySelectorAll('tr.clickable').forEach(tr => tr.onclick = () => location.hash = '#/activity/' + tr.dataset.id);
    document.getElementById('pgPrev').onclick = () => { st.offset = Math.max(0, st.offset - st.limit); load(); };
    document.getElementById('pgNext').onclick = () => { st.offset += st.limit; load(); };
    const sel = el.querySelector('select[name=type]');
    sel.innerHTML = '<option value="">alle</option>' + (res.types || []).map(x => `<option ${st.filters.type === x ? 'selected' : ''}>${esc(x)}</option>`).join('');
  }
  el.querySelector('#actFilters').addEventListener('submit', e => {
    e.preventDefault();
    const fd = new FormData(e.target);
    st.filters = {};
    for (const [k, v] of fd.entries()) if (v !== '') st.filters[k] = v;
    st.offset = 0; load();
  });
  el.querySelector('#actFilters').addEventListener('reset', () => { st.filters = {}; st.offset = 0; setTimeout(load, 0); });
  await load();
};

/* ------------------------- Aktivitätsdetail ----------------------------- */
V.activityDetail = async (el, actId) => {
  let detail;
  try { detail = await API.get('/activities/' + actId); }
  catch (e) { el.innerHTML = `<div class="error">${esc(e.message)}</div>`; return; }
  const a = detail.activity;
  const hasPower = !!a.power_data;
  el.innerHTML = `
  <div class="card">
    <h2>${esc(a.name)} <span class="muted" style="font-weight:400;font-size:14px">${fmtDate(a.start_time)}</span></h2>
    <div class="grid cols-4" style="margin-top:8px">
      ${kpi('Distanz', fmtKm(a.distance_m), '')}
      ${kpi('Zeit', fmtH(a.moving_time_s || a.duration_s), a.moving_time_s ? 'bewegt' : 'gesamt')}
      ${kpi('Höhendifferenz', nf(a.elevation_gain_m, 0) + ' m', '')}
      ${kpi('NP', a.np_power ? nf(a.np_power, 0) + ' W' : '—', a.fit_np_note || '')}
      ${kpi('IF', a.if_value != null ? nf(a.if_value, 2) : '—', detail.ftp_used ? `FTP ${nf(detail.ftp_used,0)} W` : 'FTP fehlt → IF nicht berechenbar')}
      ${kpi('TSS', a.tss != null ? nf(a.tss, 0) : '—', detail.ftp_used ? '' : 'FTP fehlt')}
      ${kpi('Ø Leistung', hasPower ? nf(a.avg_power, 0) + ' W' : 'keine Wattdaten', hasPower && detail.weight_used ? nf(a.avg_power / detail.weight_used, 2) + ' W/kg' : '')}
      ${kpi('Ø HF / Kadenz', nf(a.avg_hr, 0) + ' / ' + nf(a.avg_cadence, 0), '')}
    </div>
    ${!hasPower ? '<p class="badge warn" style="margin-top:8px">Diese Aktivität enthält keine Leistungsdaten — Leistungskennzahlen sind bewusst leer statt geschätzt.</p>' : ''}
    <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn" id="reprocessBtn" title="Originaldatei erneut mit dem aktuellen Parser einlesen">↻ Neu verarbeiten</button>
      <button class="btn" id="journalBtn">📝 Journal</button>
      <a class="btn ghost" href="#/activities">← Zurück zur Liste</a>
    </div>
    <div id="reprocMsg" class="small muted" style="margin-top:6px"></div>
  </div>
  <div class="card">
    <h3>GPS-Track</h3>
    ${a.has_gps ? `<canvas id="actMap" class="chart" data-h="260"></canvas>` : '<p class="muted">Keine GPS-Daten (Indoor-Fahrt oder Gerät ohne Track).</p>'}
  </div>
  <div class="card">
    <h3>Messreihe <span class="muted small">Einheitliches Zeitfenster für alle Diagramme · Ziehen = Zoom · Doppelklick = Reset</span></h3>
    ${chartBox('sPow', 200)}${chartBox('sHr', 150)}${chartBox('sCad', 130)}${chartBox('sAlt', 130)}
    <div class="legend" id="sLegend"></div>
    <div id="sNote" class="small muted"></div>
  </div>
  ${detail.laps.length ? `<div class="card"><h3>Runden</h3><div class="tablewrap"><table>
    <tr><th>#</th><th>Dauer</th><th>Distanz</th><th>Ø W</th><th>Max W</th><th>Ø HF</th><th>Ø Kad.</th><th>HM</th></tr>
    ${detail.laps.map(l => `<tr><td>${l.lap_no}</td><td>${fmtH(l.time_s)}</td><td>${fmtKm(l.distance_m)}</td>
      <td>${nf(l.avg_power,0)}</td><td>${nf(l.max_power,0)}</td><td>${nf(l.avg_hr,0)}</td><td>${nf(l.avg_cadence,0)}</td><td>${nf(l.elevation_gain_m,0)}</td></tr>`).join('')}
    </table></div></div>` : ''}
  ${detail.segments.length ? `<div class="card"><h3>Erkannte Belastungsabschnitte</h3><div class="tablewrap"><table>
    <tr><th>Typ</th><th>Start</th><th>Dauer</th><th>Ø W</th><th>Max W</th></tr>
    ${detail.segments.map(s => `<tr><td>${s.kind === 'work' ? '▶ Arbeit' : '⏸ Pause'}</td><td>${fmtDur(s.start_epoch)}</td>
      <td>${fmtDur(s.length_s)}</td><td>${nf(s.avg_power,0)}</td><td>${nf(s.max_power,0)}</td></tr>`).join('')}
    </table></div></div>` : ''}
  <div class="card"><h3>Journal</h3><div id="journalArea"></div></div>`;

  // Karte
  if (a.has_gps) {
    const s = await API.get(`/activities/${actId}/series?max_points=1500`).catch(() => null);
    if (s && s.lat) trackMap(document.getElementById('actMap'), s.lat, s.lon);
  }
  // Messreihen-Charts synchron
  const sync = 'act' + actId; registerSync(sync);
  const s = await API.get(`/activities/${actId}/series?max_points=2000`).catch(e => null);
  if (!s) {
    document.getElementById('sNote').textContent = 'Keine Messreihe gespeichert — evtl. „Neu verarbeiten“ nutzen.';
  } else {
    const mk = (id, name, color, arr, unit, extra = {}) => {
      const pts = arr.map((y, i) => [s.t[i], y]);
      makeLine(document.getElementById(id), { height: extra.h || 150, sync, fmtX: fmtDur, series: [{ name, color, pts, unit, ...extra }], ...extra.opts });
    };
    const powArr = s.power || [];
    const hasP = powArr.some(v => v != null);
    const hrArr = s.hr || [], cadArr = s.cadence || [], altArr = s.alt || [];
    const legend = [];
    if (hasP) {
      legend.push(`<i style="background:${cssVar('--accent')}"></i>Watt`);
      if (detail.ftp_used) legend.push(`<i style="background:${cssVar('--warn')}"></i>FTP ${detail.ftp_used} W (gestrichelt)`);
      makeLine(document.getElementById('sPow'), { height: 200, sync, fmtX: fmtDur, yLabel: 'W',
        markers: detail.ftp_used ? [{ value: detail.ftp_used, label: 'FTP ' + detail.ftp_used + ' W' }] : [],
        series: [{ name: 'Watt', color: cssVar('--accent'), pts: powArr.map((y, i) => [s.t[i], y]), unit: ' W' }] });
    } else {
      document.getElementById('sPow').parentElement.innerHTML = '<p class="muted">Keine Watt-Messreihe in dieser Datei.</p>';
    }
    const mkSimple = (id, arr, color, name, unit, h) => {
      if (!arr.some(v => v != null)) { document.getElementById(id)?.parentElement.replaceWith(Object.assign(document.createElement('p'), { className: 'muted', textContent: `Keine ${name}-Daten.` })); return; }
      legend.push(`<i style="background:${color}"></i>${name}`);
      makeLine(document.getElementById(id), { height: h, sync, fmtX: fmtDur, series: [{ name, color, pts: arr.map((y, i) => [s.t[i], y]), unit }] });
    };
    mkSimple('sHr', hrArr, cssVar('--danger'), 'Herzfrequenz', ' bpm', 150);
    mkSimple('sCad', cadArr, cssVar('--accent2'), 'Kadenz', ' rpm', 130);
    mkSimple('sAlt', altArr, cssVar('--muted'), 'Höhe', ' m', 130);
    document.getElementById('sLegend').innerHTML = legend.join(' ');
    document.getElementById('sNote').textContent =
      `${s.n_original} Original-Messpunkte · für die Anzeige auf ${s.t.length} reduziert (Zoom zeigt reduzierte Kurve; gespeicherte Rohdaten bleiben unverändert).`;
  }
  // Re-process
  document.getElementById('reprocessBtn').onclick = async () => {
    const m = document.getElementById('reprocMsg'); m.textContent = 'Verarbeite…';
    try { const r = await API.post(`/activities/${actId}/reprocess`); m.textContent = `Fertig: ${r.status} ${r.message || ''}`; }
    catch (e) { m.textContent = 'Fehler: ' + e.message; }
  };
  // Journal
  const jArea = document.getElementById('journalArea');
  const j = detail.journal || {};
  jArea.innerHTML = `<form id="jForm" class="inline">
    <label style="flex:2">Notiz <input name="notes" maxlength="4000" value="${esc(j.notes || '')}"></label>
    <label>RPE 1-10 <input type="number" name="rpe" min="1" max="10" value="${j.rpe ?? ''}" style="width:90px"></label>
    <label>Bedingungen <input name="conditions" maxlength="500" value="${esc(j.conditions || '')}"></label>
    <button class="btn primary">Speichern</button><span id="jMsg" class="small muted"></span></form>`;
  document.getElementById('jForm').onsubmit = async e => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const body = { notes: fd.get('notes') || null, conditions: fd.get('conditions') || null };
    if (fd.get('rpe')) body.rpe = Number(fd.get('rpe'));
    try { await API.post(`/activities/${actId}/journal`, body); document.getElementById('jMsg').textContent = 'Gespeichert ✓'; }
    catch (err) { document.getElementById('jMsg').textContent = 'Fehler: ' + err.message; }
  };
};

/* --------------------------- Leistungskurve ------------------------------ */
V.powercurve = async (el) => {
  el.innerHTML = `<div class="card"><h3>Power-Dauer-Kurve (Bestleistungen über den gesamten Bestand)</h3>
    <div class="tabs" id="pcScale"><button class="active" data-m="w">Watt</button><button data-m="wkg">W/kg</button></div>
    ${chartBox('pcChart', 300)}
    <div class="legend" id="pcLegend"></div>
    <p class="small muted">Berechnet aus gespeicherten Messreihen (maximale mittlere Leistung je Dauer). Lücken in den Rohdaten werden übersprungen, nie überbrückt.</p>
  </div>
  <div class="card tablewrap"><h3>Kurve nach Dauer</h3><div id="pcTable"></div></div>`;
  const data = await API.get('/power-curve');
  if (!data.curve.length) { el.querySelector('#pcTable').innerHTML = emptyState('Noch keine Leistungsdaten.', 'Watt-Messreihen entstehen beim Import von Dateien mit Power-Kanälen.'); return; }
  let mode = 'w';
  function draw() {
    const canvas = document.getElementById('pcChart');
    const pts = data.curve.map(c => [Math.log10(Math.max(c.dur, 1)), mode === 'w' ? c.watts : (c.wkg ?? null)]);
    const ftp = data.ftp;
    makeLine(canvas, {
      height: 300, fmtX: x => fmtDur(Math.pow(10, x)), yLabel: mode === 'w' ? 'Watt' : 'W/kg',
      markers: ftp ? [{ value: mode === 'w' ? ftp : (mode === 'w' ? null : null), label: `FTP ${ftp} W` }]
                    .concat(mode === 'wkg' && false ? [] : []) : [],
      series: [{ name: mode === 'w' ? 'Watt' : 'W/kg', color: cssVar('--accent'), pts, fill: true }],
    });
    document.getElementById('pcLegend').innerHTML =
      `<i style="background:${cssVar('--accent')}"></i>${mode === 'w' ? 'Watt' : 'W/kg'}` +
      (ftp ? ` · gestrichelt: FTP ${ftp} W${mode === 'wkg' && data.curve[0]?.wkg ? '' : ''}` : ' · FTP nicht gesetzt');
  }
  draw();
  document.querySelectorAll('#pcScale button').forEach(b => b.onclick = () => {
    document.querySelectorAll('#pcScale button').forEach(x => x.classList.remove('active'));
    b.classList.add('active'); mode = b.dataset.m; draw();
  });
  document.getElementById('pcTable').innerHTML = `<table>
    <tr><th>Dauer</th><th>Watt</th><th>W/kg</th><th>Datum</th><th>Aktivität</th></tr>
    ${data.curve.filter(c => [5,10,30,60,300,600,1200,1800,3600,7200].includes(c.dur)).map(c =>
      `<tr><td>${fmtDur(c.dur)}</td><td>${nf(c.watts,1)}</td><td>${nf(c.wkg,2)}</td><td>${esc(c.date || '')}</td><td>${esc(c.activity || '')}</td></tr>`).join('')}
    </table>`;
};

/* ---------------------------- Bestleistungen ----------------------------- */
V.pbs = async (el) => {
  const rows = await API.get('/best-efforts');
  if (!rows.length) { el.innerHTML = emptyState('Noch keine Bestleistungen erfasst.'); return; }
  el.innerHTML = `<div class="card tablewrap"><h3>Persönliche Bestleistungen (alle Dauern)</h3>
    <table><tr><th>Dauer</th><th>Watt</th><th>W/kg</th><th>Datum</th><th>Aktivität</th></tr>
    ${rows.map(r => `<tr><td>${fmtDur(r.duration_s)}</td><td>${nf(r.watts,1)}</td><td>${nf(r.wkg,2)}</td>
      <td>${esc(r.achieved_on || '')}</td>
      <td>${r.activity_id ? `<a href="#/activity/${r.activity_id}">${esc(r.name || '')}</a>` : '—'}</td></tr>`).join('')}
    </table></div>`;
};

/* -------------------------- Trainingsbelastung --------------------------- */
V.loadview = async (el) => {
  el.innerHTML = `<div class="card"><h3>Trainingsbelastung</h3>
    <form class="inline" id="loadDays"><label>Tage <select name="days"><option>90</option><option selected>180</option><option>365</option><option>730</option></select></label>
    <button class="btn">Anzeigen</button></form>
    <div id="loadBody"><div class="loading">Lädt…</div></div></div>`;
  async function draw(days) {
    const d = await API.get('/load?days=' + days);
    const body = document.getElementById('loadBody');
    if (!d.series.length) { body.innerHTML = '<p class="muted">Noch keine Daten.</p>'; return; }
    body.innerHTML = `
      <div class="grid cols-3">
        ${kpi('CTL (Fitness)', nf(d.current.ctl, 1), 'τ = 42 Tage')}
        ${kpi('ATL (Frische)', nf(d.current.atl, 1), 'τ = 7 Tage')}
        ${kpi('TSB (Form)', nf(d.current.tsb, 1), 'CTL(gestern) − ATL(gestern)')}
      </div>
      ${chartBox('ldTss', 140)}${chartBox('ldFit', 240)}
      <h3 style="margin-top:14px">Wochenübersicht</h3>
      <div class="tablewrap"><table><tr><th>Woche</th><th>TSS</th><th>KM</th><th>Fahrten</th></tr>
      ${d.weekly.map(w => `<tr><td>${esc(w.w)}</td><td>${nf(w.tss,0)}</td><td>${nf(w.km,1)}</td><td>${w.n}</td></tr>`).join('')}
      </table></div>
      <details style="margin-top:10px"><summary class="muted small">Verwendete Formeln</summary>
      <pre class="small">${esc(JSON.stringify(d.formulas, null, 2))}</pre></details>`;
    const sync = 'load'; registerSync(sync);
    makeLine(document.getElementById('ldTss'), { height: 140, sync, fmtX: i => d.series[Math.round(i)]?.date.slice(5) || '',
      series: [{ name: 'TSS/Tag', color: cssVar('--z4'), pts: d.series.map((x, i) => [i, x.tss]), unit: ' TSS' }] });
    makeLine(document.getElementById('ldFit'), { height: 240, sync, fmtX: i => d.series[Math.round(i)]?.date.slice(5) || '',
      series: [
        { name: 'CTL', color: cssVar('--accent2'), pts: d.series.map((x, i) => [i, x.ctl]) },
        { name: 'ATL', color: cssVar('--warn'), pts: d.series.map((x, i) => [i, x.atl]) },
        { name: 'TSB', color: cssVar('--accent'), pts: d.series.map((x, i) => [i, x.tsb]) }] });
  }
  draw(el.querySelector('[name=days]').value);
  el.querySelector('#loadDays').onsubmit = e => { e.preventDefault(); draw(new FormData(e.target).get('days')); };
};

/* ------------------------------- Coach ---------------------------------- */
V.coach = async (el) => {
  const [rd, today] = await Promise.all([API.get('/coach/readiness'), API.get('/coach/today')]);
  el.innerHTML = `
  <div class="grid cols-2">
    <div class="card">
      <h3>Tages-Check-in</h3>
      <form id="ciForm">
        <div class="grid cols-2">
          <label>Schlaf (h) <input type="number" name="sleep_hours" min="0" max="24" step="0.1"></label>
          <label>Schlafqualität 1–5 <input type="number" name="sleep_quality" min="1" max="5"></label>
          <label>Ermüdung 1–5 <input type="number" name="fatigue" min="1" max="5"></label>
          <label>Muskelschmerz 1–5 <input type="number" name="soreness" min="1" max="5"></label>
          <label>Motivation 1–5 <input type="number" name="motivation" min="1" max="5"></label>
          <label>Wohlbefinden 1–5 <input type="number" name="wellness" min="1" max="5"></label>
        </div>
        <label>Symptome <input name="symptoms" maxlength="500" placeholder="z.B. Halsschmerzen"></label>
        <label>Notiz <input name="notes" maxlength="2000"></label>
        <button class="btn primary">Check-in speichern</button>
        <span id="ciMsg" class="small muted"></span>
      </form>
    </div>
    <div class="card">
      <h3>Readiness-Schätzung <span class="badge info">Modell, kein Messwert</span></h3>
      <div style="font-size:34px;font-weight:700">${rd.score}<span class="muted" style="font-size:16px">/100</span>
        <span class="badge ${rd.category === 'gut_erholt' ? 'good' : rd.category === 'normal' ? 'info' : rd.category === 'erhoehte_ermuedung' ? 'warn' : 'bad'}">${esc(rd.label)}</span></div>
      <p class="small muted">Sicherheit: ${esc(rd.confidence)}</p>
      <ul class="small">${rd.factors.map(f => `<li><b>${esc(f.name)}</b> (${esc(f.effect)}): ${esc(f.text)}</li>`).join('')}</ul>
      ${(rd.missing || []).length ? `<p class="badge warn">Fehlende Eingaben</p><ul class="small muted">${rd.missing.map(m => `<li>${esc(m)}</li>`).join('')}</ul>` : ''}
    </div>
  </div>
  <div class="card">
    <h3>Heutige Empfehlung</h3>
    ${recBlock(today)}
    <button class="btn" id="refreshRec">↻ Neu bewerten</button>
  </div>
  <div class="card">
    <h3>Coach fragen <span class="muted small">(regelbasiert, antwortet nur mit gespeicherten Daten)</span></h3>
    <div class="chat-log" id="chatLog"></div>
    <form class="inline" id="chatForm"><input id="chatQ" maxlength="500" minlength="2" placeholder="z.B. Was soll ich heute fahren?" style="flex:1">
      <button class="btn primary">Frage stellen</button></form>
    <p class="small muted">Beispiele: Wochenbelastung · beste 5-Min-Leistung · was soll ich heute fahren · wie entwickelt sich meine Leistung · warum diese Einschätzung</p>
  </div>`;
  document.getElementById('ciForm').onsubmit = async e => {
    e.preventDefault();
    const fd = new FormData(e.target); const body = {};
    for (const [k, v] of fd.entries()) if (v !== '') body[k] = k.endsWith('_hours') ? parseFloat(v) : parseInt(v) || v;
    try { await API.post('/coach/checkin', body); document.getElementById('ciMsg').textContent = 'Gespeichert ✓ Lade Readiness…'; V.coach(el); }
    catch (err) { document.getElementById('ciMsg').textContent = 'Fehler: ' + err.message; }
  };
  document.getElementById('refreshRec').onclick = async () => {
    const t = await API.get('/coach/today?refresh=true');
    el.querySelector('.card:nth-of-type(3)').innerHTML = '<h3>Heutige Empfehlung</h3>' + recBlock(t) + '<button class="btn" id="refreshRec">↻ Neu bewerten</button>';
    document.getElementById('refreshRec').onclick = arguments.callee;
  };
  document.getElementById('chatForm').onsubmit = async e => {
    e.preventDefault();
    const q = document.getElementById('chatQ').value.trim(); if (q.length < 2) return;
    const log = document.getElementById('chatLog');
    log.insertAdjacentHTML('beforeend', `<div class="chat-msg user">${esc(q)}</div>`);
    document.getElementById('chatQ').value = '';
    try { const r = await API.post('/coach/chat', { question: q });
      log.insertAdjacentHTML('beforeend', `<div class="chat-msg coach">${esc(r.answer)}</div>`); }
    catch (err) { log.insertAdjacentHTML('beforeend', `<div class="chat-msg coach">Fehler: ${esc(err.message)}</div>`); }
    log.scrollTop = log.scrollHeight;
  };
};
function recBlock(t) {
  const w = t.recommendation || {};
  return `
  <p><b>${esc(w.name)}</b> <span class="muted">· Ziel: ${esc(w.goal)} · Zone ${esc(w.zone || '—')} · ${w.duration_min || 0} min${t.ftp ? '' : ' · <span class="badge warn">FTP fehlt – keine Wattziele</span>'}</p>
  ${t.note ? `<p class="badge warn">${esc(t.note)}</p>` : ''}
  ${w.blocks?.length ? `<table><tr><th>Block</th><th>Dauer</th><th>Ziel</th><th>Watt</th></tr>
    ${w.blocks.map(b => `<tr><td>${esc(b.type)}</td><td>${b.minutes != null ? b.minutes + ' min' : (b.seconds || 0) + ' s'}</td>
      <td>${esc(b.target || '')}</td><td>${b.watt_low ? b.watt_low + '–' + b.watt_high + ' W' : '—'}</td></tr>`).join('')}</table>` : ''}
  <details><summary class="muted small">Begründung (${(t.reasons || []).length} Faktoren)</summary>
    <ul class="small">${(t.reasons || []).map(r => `<li>${esc(r)}</li>`).join('')}</ul></details>
  ${t.alternative ? `<p class="small muted">Alternative: <b>${esc(t.alternative.name)}</b> (${t.alternative.duration_min || 0} min)</p>` : ''}`;
}

/* ---------------------------- Trainingsplan ------------------------------ */
V.plan = async (el) => {
  const p = await API.get('/coach/plan');
  el.innerHTML = `<div class="card">
    <h3>7-Tage-Plan <span class="muted small">Stand: ${esc(p.generated || '')}${p.trigger_reason ? ' · Anlass: ' + esc(p.trigger_reason) : ''}</span></h3>
    ${(p.days || []).map(d => `
      <div class="plan-day ${d.priority === 'hoch' ? 'hard' : d.kind === 'rest_day' ? 'rest' : ''}">
        <b>${esc(d.date)} (${esc(d.weekday)})</b> — ${esc(d.title)}
        <span class="muted">· ${d.duration_min || 0} min · ${esc(d.zone || '')} · geschätzt ${nf(d.est_tss, 0)} TSS · Priorität ${esc(d.priority)}</span>
        <div class="small muted">${esc(d.reason)}</div>
        <div style="margin-top:4px">
          <button class="btn small" data-ev="completed" data-d="${d.date}" data-t="${esc(d.title)}">✓ absolviert</button>
          <button class="btn small" data-ev="skipped" data-d="${d.date}">✗ ausgelassen</button>
        </div>
      </div>`).join('')}
    <p class="small muted">Regeln: ${(p.rules || []).join(' · ')}</p>
    <button class="btn" id="regenBtn">Plan komplett neu berechnen</button>
  </div>
  <div class="card"><h3>Fortschrittsanalyse <span class="muted small">(gemessene MMP-Werte, letzte 90 vs. vorherige 90 Tage)</span></h3>
    <div id="progBox"></div></div>`;
  async function reload() { V.plan(el); }
  document.getElementById('regenBtn').onclick = async () => { await API.post('/coach/plan/regenerate'); reload(); };
  el.querySelectorAll('[data-ev]').forEach(b => b.onclick = async () => {
    await API.post('/coach/plan/event', { type: b.dataset.ev, date: b.dataset.d, title: b.dataset.t });
    reload();
  });
  const pr = await API.get('/coach/progress');
  document.getElementById('progBox').innerHTML = `<div class="tablewrap"><table>
    <tr><th>Bereich</th><th>aktuell</th><th>davor</th><th>Δ</th><th>Bewertung</th></tr>
    ${pr.power_trends.map(t => `<tr><td>${esc(t.label)}</td><td>${t.current_w ? nf(t.current_w, 0) + ' W' : 'keine Daten'}</td>
      <td>${t.previous_w ? nf(t.previous_w, 0) + ' W' : '—'}</td>
      <td>${t.delta_w != null ? (t.delta_w > 0 ? '+' : '') + nf(t.delta_w, 1) + ' W' : '—'}</td>
      <td><span class="badge ${t.status === 'verbessert' ? 'good' : t.status === 'rückläufig' ? 'bad' : t.status === 'stabil' ? 'info' : 'warn'}">${esc(t.status)}</span></td></tr>`).join('')}
    </table><p class="small muted">${esc(pr.note || '')}</p></div>`;
};

/* ------------------------- Trainingsbibliothek --------------------------- */
V.library = async (el) => {
  const lib = await API.get('/coach/workouts');
  el.innerHTML = `<div class="card"><h3>Struktur-Vorlagen <span class="muted small">(Wattziele aus FTP bzw. bester 5-min-Leistung)</span></h3>
    <div class="grid cols-2">${lib.generated.map(w => workoutCard(w)).join('')}</div></div>
  <div class="card"><h3>Gespeicherte Workouts</h3><div id="savedList">${
    lib.saved.length ? `<table><tr><th>Titel</th><th>Ziel</th><th>Dauer</th><th>Zone</th><th>TSS</th></tr>${
      lib.saved.map(w => `<tr><td>${esc(w.title)}</td><td>${esc(w.goal || '')}</td><td>${w.duration_min || 0} min</td><td>${esc(w.intensity_zone || '')}</td><td>${nf(w.est_tss, 0)}</td></tr>`).join('')}</table>`
      : '<p class="muted">Noch nichts gespeichert — Vorlagen oben mit „Speichern“ übernehmen.</p>'}</div></div>`;
  el.querySelectorAll('[data-savewo]').forEach(b => b.onclick = async () => {
    const w = lib.generated.find(x => x.kind === b.dataset.savewo);
    await API.post('/coach/workouts/save', { title: w.name, goal: w.goal, duration_min: w.duration_min,
      intensity_zone: w.zone, structure: w.blocks, est_tss: w.est_tss });
    b.textContent = '✓ gespeichert'; b.disabled = true;
  });
};
function workoutCard(w) {
  return `<div class="card" style="margin:0"><h3>${esc(w.name)} <span class="badge">${esc(w.zone || '')}</span></h3>
    <p class="small muted">${esc(w.goal)} · ${w.duration_min || 0} min · ~${nf(w.est_tss, 0)} TSS</p>
    ${w.blocks?.length ? `<table><tr><th>Block</th><th>Dauer</th><th>Watt</th></tr>
      ${w.blocks.map(b => `<tr><td>${esc(b.type)}</td><td>${b.minutes != null ? b.minutes + ' min' : (b.seconds || 0) + ' s'}</td>
      <td>${b.watt_low ? b.watt_low + '–' + b.watt_high : '—'}</td></tr>`).join('')}</table>` : '<p class="muted small">Keine Struktur (Ruhetag).</p>'}
    <button class="btn small" data-savewo="${esc(w.kind)}">Speichern</button></div>`;
}

/* ----------------------------- Einstellungen ----------------------------- */
V.settings = async (el) => {
  const s = await API.get('/settings');
  el.innerHTML = `
  <div class="grid cols-2">
  <div class="card"><h3>Leistungswerte</h3>
    <form id="perfForm">
      <label>FTP (Watt) <input type="number" name="ftp" min="30" max="600" value="${s.ftp ?? ''}"></label>
      <label>Gültig ab <input type="date" name="effective_from"></label>
      <label>Gewicht (kg) <input type="number" name="weight" step="0.1" min="25" max="200" value="${s.weight ?? ''}"></label>
      <p class="small muted">Änderungen lösen eine vollständige Neuberechnung von IF/TSS/W/kg und Bestleistungen aus (bei vielen Aktivitäten etwas langsamer).</p>
      <button class="btn primary">Speichern</button> <span id="perfMsg" class="small muted"></span>
    </form>
    <details><summary class="muted small">Historie</summary><pre class="small">FTP: ${esc(JSON.stringify(s.ftp_history))}\nGewicht: ${esc(JSON.stringify(s.weight_history))}</pre></details>
  </div>
  <div class="card"><h3>Training & Planung</h3>
    <form id="planForm">
      <label>Ziel <select name="goal">
        ${[['ftp_up','FTP steigern'],['sprint','Sprint'],['p5min','5-Min-Leistung'],['climbing','Klettern'],
           ['endurance','Ausdauer'],['long_rides','Lange Fahrten'],['allround','Allround']]
          .map(([v, l]) => `<option value="${v}" ${s.goal === v ? 'selected' : ''}>${l}</option>`).join('')}
      </select></label>
      <label>Max. Einheitsdauer (min) <input type="number" name="max_duration_min" min="15" max="600" value="${s.max_duration_min}"></label>
      <label>Verfügbare Tage (Mo–So)
        <div style="display:flex;gap:6px;margin-top:6px">${s.available_days.map((d, i) =>
          `<label style="margin:0"><input type="checkbox" name="day${i}" ${d ? 'checked' : ''} style="width:auto"> ${['Mo','Di','Mi','Do','Fr','Sa','So'][i]}</label>`).join('')}</div>
      </label>
      <label>Zeitzone <input name="timezone" value="${esc(s.timezone)}"></label>
      <button class="btn primary">Speichern</button> <span id="planMsg" class="small muted"></span>
    </form>
  </div>
  </div>
  <div class="card"><h3>Leistungszonen (%FTP)</h3>
    <div class="tablewrap"><table id="zoneTbl"><tr><th>#</th><th>Name</th><th>von %</th><th>bis %</th></tr>
    ${s.power_zones.map(z => `<tr><td>${z.no}</td><td><input data-z="name" value="${esc(z.name)}" style="width:220px"></td>
      <td><input data-z="low" type="number" value="${z.low}" style="width:80px"></td>
      <td><input data-z="high" type="number" value="${z.high}" style="width:80px"></td></tr>`).join('')}
    </table></div>
    <button class="btn" id="saveZones">Zonen speichern</button> <span id="zoneMsg" class="small muted"></span>
  </div>
  <div class="card"><h3>Daten</h3>
    <a class="btn" href="/api/export" target="_blank">JSON-Export herunterladen</a>
    <button class="btn danger" id="wipeBtn">Alle Analysedaten löschen…</button>
    <p class="small muted">Löschen erzeugt zuerst eine Datensicherung der Datenbank; Upload-Originale bleiben erhalten.</p>
  </div>`;
  document.getElementById('perfForm').onsubmit = async e => {
    e.preventDefault();
    const fd = new FormData(e.target); const body = {};
    if (fd.get('ftp')) body.ftp = parseFloat(fd.get('ftp'));
    if (fd.get('weight')) body.weight = parseFloat(fd.get('weight'));
    if (fd.get('effective_from')) body.effective_from = fd.get('effective_from');
    try { await API.put('/settings', body); document.getElementById('perfMsg').textContent = 'Gespeichert & neu berechnet ✓'; }
    catch (err) { document.getElementById('perfMsg').textContent = 'Fehler: ' + err.message; }
  };
  document.getElementById('planForm').onsubmit = async e => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const body = { goal: fd.get('goal'), max_duration_min: parseInt(fd.get('max_duration_min')),
      available_days: [0,1,2,3,4,5,6].map(i => fd.get('day' + i) ? 1 : 0), timezone: fd.get('timezone') };
    try { await API.put('/settings', body); document.getElementById('planMsg').textContent = 'Gespeichert ✓'; }
    catch (err) { document.getElementById('planMsg').textContent = 'Fehler: ' + err.message; }
  };
  document.getElementById('saveZones').onclick = async () => {
    const rows = [...document.querySelectorAll('#zoneTbl tr')].slice(1);
    const zones = rows.map(tr => ({
      name: tr.querySelector('[data-z=name]').value,
      low: parseFloat(tr.querySelector('[data-z=low]').value),
      high: parseFloat(tr.querySelector('[data-z=high]').value) }));
    try { await API.put('/settings', { power_zones: zones }); document.getElementById('zoneMsg').textContent = 'Zonen gespeichert ✓'; }
    catch (err) { document.getElementById('zoneMsg').textContent = 'Fehler: ' + err.message; }
  };
  document.getElementById('wipeBtn').onclick = async () => {
    const pw = prompt('Zum Löschen aller Analyse-Aktivitäten dein Passwort eingeben (Backup wird erstellt, Original-Uploads bleiben):');
    if (!pw) return;
    const fd = new FormData(); fd.append('password', pw); fd.append('confirm', 'DELETE');
    try { const r = await API.postFD('/delete-all', fd); alert('Gelöscht. Backup: ' + r.backup); }
    catch (err) { alert('Fehler: ' + err.message); }
  };
};

/* -------------------------------- Import --------------------------------- */
V.import = async (el) => {
  el.innerHTML = `
  <div class="card">
    <h3>Aktivitäten importieren</h3>
    <p class="muted small">Unterstützt: FIT · FIT.GZ · TCX · GPX · ZIP (auch kompletter Strava-Datenexport mit Unterordnern).
      Duplikate werden über UID bzw. Startzeit/Dauer/Distanz erkannt und nicht doppelt angelegt.</p>
    <div class="dropzone" id="dz">Dateien hierher ziehen oder klicken zum Auswählen<br>
      <input type="file" id="fileIn" multiple accept=".fit,.gz,.tcx,.gpx,.zip" hidden></div>
    <div id="impProg" class="small muted"></div>
    <div id="impReport" class="tablewrap" style="margin-top:10px"></div>
  </div>
  <div class="card"><h3>Import-Protokoll</h3><div id="impLog" class="tablewrap"><div class="loading">Lädt…</div></div></div>
  <div class="card"><h3>Strava-Anbindung</h3><div id="stravaInfo" class="muted small"><div class="loading">Lädt…</div></div></div>`;
  const dz = document.getElementById('dz'), fi = document.getElementById('fileIn');
  dz.onclick = () => fi.click();
  dz.ondragover = e => { e.preventDefault(); dz.classList.add('over'); };
  dz.ondragleave = () => dz.classList.remove('over');
  dz.ondrop = e => { e.preventDefault(); dz.classList.remove('over'); upload(e.dataTransfer.files); };
  fi.onchange = () => upload(fi.files);
  async function upload(files) {
    if (!files.length) return;
    const prog = document.getElementById('impProg');
    prog.textContent = `Lade ${files.length} Datei(en) hoch… (Große ZIPs können dauern; Server-Limit pro Datei: 64 MB)`;
    const fd = new FormData();
    for (const f of files) fd.append('files', f);
    try {
      const r = await API.postFD('/import', fd);
      prog.textContent = `Fertig: ${r.summary.ok} importiert · ${r.summary.duplicates} Duplikate · ${r.summary.errors} Fehler`;
      document.getElementById('impReport').innerHTML = `<table><tr><th>Datei</th><th>Status</th><th>Meldung</th></tr>
        ${r.report.map(x => `<tr><td>${esc(x.filename)}</td>
          <td><span class="badge ${x.status === 'ok' ? 'good' : x.status === 'duplicate' ? 'warn' : 'bad'}">${esc(x.status)}</span></td>
          <td>${esc(x.message || x.name || '')}</td></tr>`).join('')}</table>`;
      loadLog();
    } catch (e) { prog.textContent = 'Fehler: ' + e.message; }
  }
  async function loadLog() {
    const rows = await API.get('/import/log');
    document.getElementById('impLog').innerHTML = rows.length ? `<table><tr><th>Zeit</th><th>Datei</th><th>Status</th><th>Meldung</th></tr>
      ${rows.map(r => `<tr><td>${esc(r.ts)}</td><td>${esc(r.filename || '')}</td>
        <td><span class="badge ${r.status === 'ok' ? 'good' : r.status === 'duplicate' ? 'warn' : r.status === 'error' ? 'bad' : ''}">${esc(r.status)}</span></td>
        <td>${esc(r.message || '')}</td></tr>`).join('')}</table>` : '<p class="muted">Noch keine Importe protokolliert.</p>';
  }
  loadLog();
  try {
    const si = await API.get('/strava/info');
    document.getElementById('stravaInfo').innerHTML =
      `<p><b>Empfohlener Weg: ${esc(si.primary_import)}</b></p><p>${esc(si.assessment)}</p>`;
  } catch (e) { document.getElementById('stravaInfo').textContent = e.message; }
};
