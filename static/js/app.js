/* App-Shell: Routing, Login-Gate, Theme. */

const NAV = [
  ['#/dashboard', '📈 Dashboard', V.dashboard],
  ['#/activities', '🚴 Aktivitäten', V.activities],
  ['#/powercurve', '⚡ Leistungskurve', V.powercurve],
  ['#/pbs', '🏆 Bestleistungen', V.pbs],
  ['#/load', '💪 Belastung', V.loadview],
  ['#/coach', '🧠 Coach', V.coach],
  ['#/plan', '🗓 Plan', V.plan],
  ['#/library', '📚 Vorlagen', V.library],
  ['#/import', '⬆️ Import', V.import],
  ['#/settings', '⚙️ Einstellungen', V.settings],
];

const view = document.getElementById('view');
const loginScreen = document.getElementById('loginScreen');

function setTheme(t) {
  document.documentElement.dataset.theme = t;
  localStorage.setItem('cpc-theme', t);
}
document.getElementById('themeBtn').onclick = () =>
  setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
setTheme(localStorage.getItem('cpc-theme') || 'dark');

function buildNav(active) {
  const nav = document.getElementById('nav');
  nav.innerHTML = NAV.map(([href, label]) =>
    `<a href="${href}" class="${href === active ? 'active' : ''}">${label}</a>`).join(' ');
}

async function route() {
  let h = location.hash || '#/dashboard';
  const m = h.match(/^#\/activity\/(\d+)$/);
  try {
    if (m) { await render(V.activityDetail, '#/activities', m[1]); return; }
    const entry = NAV.find(([href]) => href === h) || NAV[0];
    if (!NAV.find(([href]) => href === h)) location.hash = '#/dashboard';
    await render(entry[2], entry[0]);
  } catch (e) {
    if (e.status === 401) return showLogin();
    view.innerHTML = `<div class="error">Fehler beim Laden: ${esc(e.message)}</div>`;
  }
}

async function render(fn, navHref, arg) {
  buildNav(navHref);
  view.innerHTML = '<div class="loading">Lädt…</div>';
  CHARTS.registry = {};
  const holder = document.createElement('div');
  view.innerHTML = '';
  view.appendChild(holder);
  await fn(holder, arg);
}

function showLogin() {
  loginScreen.classList.remove('hidden');
  API.status().then(s => {
    document.getElementById('loginTitle').textContent = s.needs_setup ? 'Konto einrichten' : 'Anmeldung';
    document.getElementById('loginHint').textContent = s.needs_setup
      ? 'Noch kein Konto vorhanden — Benutzername und Passwort (≥ 8 Zeichen) wählen.' : '';
  }).catch(() => {});
}

document.getElementById('loginForm').onsubmit = async e => {
  e.preventDefault();
  const err = document.getElementById('loginError');
  err.classList.add('hidden');
  try {
    await API.login(document.getElementById('lUser').value.trim(),
                    document.getElementById('lPass').value);
    loginScreen.classList.add('hidden');
    document.getElementById('logoutBtn').classList.remove('hidden');
    route();
  } catch (ex) {
    err.textContent = ex.message + ' (Erstanlage schlägt fehl, wenn bereits ein Konto existiert.)';
    err.classList.remove('hidden');
  }
};

document.getElementById('logoutBtn').onclick = async () => {
  try { await API.logout(); } catch (_) {}
  showLogin();
};

window.addEventListener('hashchange', route);

(async function boot() {
  try {
    const s = await API.status();
    if (s.authenticated) {
      loginScreen.classList.add('hidden');
      document.getElementById('logoutBtn').classList.remove('hidden');
      route();
    } else {
      showLogin();
      if (s.needs_setup) route.call(null) && buildNav('#/dashboard'); // Navigation bleibt unsichtbar hinter Login
    }
  } catch (e) {
    view.innerHTML = `<div class="error">Server nicht erreichbar: ${esc(e.message)}</div>`;
  }
})();
