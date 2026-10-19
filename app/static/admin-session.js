// The admin key is sent only at login. The session cookie is HttpOnly.
window.RadioAdmin = (() => {
  let session = {authenticated: false};
  let timer;
  let refreshing;
  let generation = 0;
  function update(value) {
    session = value;
    clearTimeout(timer);
    if (value.authenticated) timer = setTimeout(() => update({authenticated: false}), Math.max(0, new Date(value.expires_at) - Date.now()));
    const controls = document.querySelector('#admin-session-controls');
    if (controls) controls.hidden = !value.authenticated;
    window.dispatchEvent(new Event('radio:adminchange'));
    return value;
  }
  async function refresh() {
    if (refreshing) return refreshing;
    const current = generation;
    refreshing = fetch('/api/admin/session', {credentials: 'same-origin', cache: 'no-store'})
      .then(async response => {
        const data = response.ok ? await response.json() : {authenticated: false};
        return current === generation ? update(data) : session;
      }).catch(() => current === generation ? update({authenticated: false}) : session)
      .finally(() => { refreshing = null; });
    return refreshing;
  }
  async function login(token) {
    generation++;
    if (!window.isSecureContext) throw new Error('Une connexion HTTPS est nécessaire.');
    const response = await fetch('/api/admin/login', {method: 'POST', cache: 'no-store', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-Admin-Login': '1'}, body: JSON.stringify({token})});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Connexion impossible.');
    return update(data);
  }
  async function request(url, payload, method = 'POST', signal) {
    if (!session.authenticated) await refresh();
    if (!session.authenticated) throw new Error('Session administrateur expirée. Connectez-vous sur /admin.');
    const response = await fetch(url, {method: payload === undefined ? 'GET' : method,
      cache: 'no-store', credentials: 'same-origin', signal,
      headers: payload === undefined ? {} : {'Content-Type': 'application/json', 'X-CSRF-Token': session.csrf_token},
      ...(payload === undefined ? {} : {body: JSON.stringify(payload)})});
    const data = await response.json();
    if (response.status === 401) { generation++; update({authenticated: false}); }
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Opération impossible.');
    if (url.endsWith('/correction') || url.endsWith('/complete')) {
      window.RadioCache.clear();
      window.dispatchEvent(new Event('radio:catalogchange'));
    }
    return data;
  }
  async function logout() {
    await request('/api/admin/logout', {});
    generation++;
    update({authenticated: false});
  }
  window.addEventListener('focus', refresh);
  document.addEventListener('click', event => {
    if (event.target.closest('[data-admin-logout]')) logout().catch(error => {
      const status = document.querySelector('#navigation-status');
      status.textContent = error.message; status.hidden = false;
    });
  });
  return {refresh, login, request, logout, authenticated: () => session.authenticated};
})();
