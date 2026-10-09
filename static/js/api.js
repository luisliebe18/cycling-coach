/* Kleiner API-Client: Session-Cookie wird automatisch mitgesendet. */
const API = {
  async req(path, opts = {}) {
    const o = { headers: {}, credentials: 'same-origin', ...opts };
    if (o.body && !(o.body instanceof FormData)) {
      o.headers['Content-Type'] = 'application/json';
      o.body = JSON.stringify(o.body);
    }
    const r = await fetch('/api' + path, o);
    if (r.status === 401) { const e = new Error('Nicht angemeldet'); e.status = 401; throw e; }
    let data = null;
    try { data = await r.json(); } catch (_) { /* leere Antwort */ }
    if (!r.ok) {
      const msg = (data && (data.detail || data.message)) || `HTTP ${r.status}`;
      const e = new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
      e.status = r.status; e.data = data; throw e;
    }
    return data;
  },
  get: (p) => API.req(p),
  post: (p, body) => API.req(p, { method: 'POST', body }),
  put: (p, body) => API.req(p, { method: 'PUT', body }),
  postFD: (p, fd) => API.req(p, { method: 'POST', body: fd }),
  login: (username, password) => API.post('/auth/login', { username, password }),
  logout: () => API.post('/auth/logout'),
  status: () => API.get('/auth/status'),
};
