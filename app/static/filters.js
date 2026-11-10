// Personal exclusions stay in this browser. Global exclusions are enforced by the server.
window.MusicFilters = (() => {
  let hidden = [];
  let dialog;
  let revision = null;
  let checking;
  let generation = 0;
  const storageKey = 'radio-hidden-tracks-v1';
  const normalize = value => (value || '').normalize('NFKD').replace(/\p{M}/gu, '').toLocaleLowerCase().replaceAll('’', "'").trim();
  const same = (a, b) => normalize(a.artist) === normalize(b.artist) && normalize(a.title) === normalize(b.title);
  const admin = () => !!window.RadioAdmin?.authenticated();
  let previousAdmin = admin();
  try {
    const stored = JSON.parse(localStorage.getItem(storageKey) || '[]');
    if (Array.isArray(stored)) hidden = stored.filter(t => t && typeof t.title === 'string' && t.title.length > 0 && t.title.length <= 500 && (t.artist == null || (typeof t.artist === 'string' && t.artist.length <= 300))).slice(0, 50);
  } catch (_) { /* In-memory preferences still work. */ }
  function el(tag, text = '', className = '') {
    const result = document.createElement(tag); result.textContent = text; result.className = className; return result;
  }
  function apply(params) {
    params.delete('exclusions');
    if (hidden.length) params.set('exclusions', JSON.stringify(hidden));
    return params;
  }
  function labels() {
    document.querySelectorAll('[data-open-exclusions]').forEach(b => { b.textContent = admin() ? window.RadioI18n.t("ui.2874528de083") : window.RadioI18n.t("ui.a0d66a45cc6f", {p0: (hidden.length)}); });
  }
  function changed() {
    generation++;
    window.RadioCache.clear();
    labels();
    window.dispatchEvent(new Event('radio:filterschange'));
  }
  function acceptRevision(value, initializing = false) {
    if (typeof value !== 'string' || value === revision) return;
    const initial = revision === null && initializing;
    revision = value;
    if (initial) { window.RadioCache.clear(); labels(); return; }
    changed();
    window.dispatchEvent(new Event('radio:globalfilterschange'));
  }
  async function refresh() {
    if (checking) return checking;
    const current = generation;
    checking = fetch('/api/exclusions/revision', {cache: 'no-store', credentials: 'same-origin'})
      .then(async response => {
        if (!response.ok) throw new Error(window.RadioI18n.t("ui.2fba583dc587"));
        const data = await response.json();
        if (current === generation) acceptRevision(data.revision, true);
      }).finally(() => { checking = null; });
    return checking;
  }
  async function filteredFetch(url, options = {}, requireRevision = true) {
    if (requireRevision && revision === null) await refresh();
    const current = generation;
    const response = await fetch(url, {...options, cache: 'no-store'});
    const json = response.json.bind(response);
    response.json = async () => {
      const data = await json();
      if (current !== generation) throw new DOMException(window.RadioI18n.t("ui.b44ac1f86095"), 'AbortError');
      return data;
    };
    return response;
  }
  function save() {
    try { localStorage.setItem(storageKey, JSON.stringify(hidden)); } catch (_) { /* Optional. */ }
    changed();
  }
  async function hide(track, global = admin()) {
    const item = {artist: (track.artist || '').trim() || null, title: track.title.trim()};
    if (!item.title) throw new Error(window.RadioI18n.t("ui.11f2257e14ed"));
    if (global) {
      const data = await window.RadioAdmin.request('/api/admin/exclusions', item);
      acceptRevision(data.revision);
      return;
    }
    if (hidden.some(t => same(t, item))) return;
    if (hidden.length >= 50 || JSON.stringify([...hidden, item]).length > 11000) throw new Error(window.RadioI18n.t("ui.9e840240650c"));
    hidden.push(item); save();
  }
  async function promotePersonal() {
    if (!admin()) throw new Error(window.RadioI18n.t("ui.7bb273c6dc1b"));
    if (!hidden.length) return;
    const data = await window.RadioAdmin.request('/api/admin/exclusions/bulk', {items: hidden.map(t => ({...t}))});
    acceptRevision(data.revision);
    return data;
  }
  function exports(params) {
    const selection = new URLSearchParams(params);
    ['limit', 'page', 'page_size', 'snapshot_id'].forEach(k => selection.delete(k));
    window.RadioExport?.configure(selection);
  }
  async function open() {
    dialog?.close(); dialog?.remove();
    const panel = el('dialog', '', 'exclusions-dialog'); dialog = panel;
    const global = admin();
    panel.setAttribute('aria-labelledby', 'exclusions-title');
    const heading = el('h2', global ? window.RadioI18n.t("ui.2874528de083") : window.RadioI18n.t("ui.320f8db636bc")); heading.id = 'exclusions-title';
    const close = el('button', window.RadioI18n.t("ui.711e5f2e198d"), 'text-button'); close.type = 'button'; close.onclick = () => panel.close();
    const note = el('p', global
      ? 'Cette blacklist s’applique à tous et aux statistiques. Les nouvelles diffusions ne sont plus collectées ; l’historique est conservé hors des résultats.'
      : window.RadioI18n.t("ui.d6fa10c1c64d"), 'hint');
    const list = el('div');
    const personal = el('div');
    const status = el('p', '', 'hint'); status.setAttribute('role', 'status');
    let entries = [];
    let listVersion = 0;
    function renderPersonal() {
      personal.replaceChildren();
      if (global) personal.append(el('h3', window.RadioI18n.t("ui.35946d8de7ab")));
      hidden.forEach((track, index) => {
        const row = el('div', '', 'hidden-track');
        const restore = el('button', window.RadioI18n.t("ui.4e06df949dce"), 'text-button'); restore.type = 'button';
        restore.onclick = () => { hidden.splice(index, 1); save(); renderPersonal(); };
        row.append(el('span', `${track.title} · ${track.artist || window.RadioI18n.t("ui.133df509234a")}`), restore); personal.append(row);
      });
      if (!hidden.length) personal.append(el('p', window.RadioI18n.t("ui.9b105ab96068"), 'hint'));
      if (global) {
        personal.append(el('p', window.RadioI18n.t("ui.00e1033777fe"), 'hint'));
        const promote = el('button', window.RadioI18n.t("ui.c13299a96b2d", {p0: (hidden.length)}), 'text-button'); promote.type = 'button';
        promote.disabled = !hidden.length || !admin();
        promote.onclick = async () => {
          promote.disabled = true;
          try {
            await promotePersonal(); renderPersonal(); await renderGlobal();
            status.textContent = window.RadioI18n.t("ui.ee70526a8dd7");
          } catch (error) { status.textContent = error.message; promote.disabled = !admin(); }
        };
        personal.append(promote);
      }
    }
    async function renderGlobal() {
      if (!global) return;
      const current = ++listVersion;
      const data = await window.RadioAdmin.request('/api/admin/exclusions');
      if (!panel.isConnected || !panel.open || current !== listVersion) return;
      entries = data.items;
      list.replaceChildren();
      for (const track of entries) {
        const row = el('div', '', 'hidden-track');
        const restore = el('button', window.RadioI18n.t("ui.15e3da14e6ee"), 'text-button'); restore.type = 'button';
        restore.onclick = async () => {
          restore.disabled = true;
          try {
            const result = await window.RadioAdmin.request(`/api/admin/exclusions/${track.id}`, {}, 'DELETE');
            acceptRevision(result.revision);
            await renderGlobal();
            status.textContent = window.RadioI18n.t("ui.90396f5ca215");
          } catch (error) { status.textContent = error.message; }
          finally { restore.disabled = !admin(); }
        };
        row.append(el('span', `${track.title} · ${track.artist || window.RadioI18n.t("ui.133df509234a")}`), restore); list.append(row);
      }
      if (!entries.length) list.append(el('p', window.RadioI18n.t("ui.4d185e90e77b"), 'hint'));
    }
    const form = el('form', '', 'exclusion-form');
    const title = el('input'); title.placeholder = window.RadioI18n.t("ui.85c34bb88407"); title.required = true; title.maxLength = 500;
    const artist = el('input'); artist.placeholder = window.RadioI18n.t("ui.35300ea3f704"); artist.maxLength = 300;
    const titleLabel = el('label', window.RadioI18n.t("ui.78e7920010aa")); titleLabel.append(title);
    const artistLabel = el('label', window.RadioI18n.t("ui.2966c11adad7")); artistLabel.append(artist);
    const add = el('button', global ? window.RadioI18n.t("ui.f70b66d7770a") : window.RadioI18n.t("ui.23114e119215"), 'text-button'); add.type = 'submit';
    form.append(titleLabel, artistLabel, add);
    form.onsubmit = async event => {
      event.preventDefault(); add.disabled = true;
      try {
        await hide({title: title.value, artist: artist.value}, global);
        form.reset(); renderPersonal(); await renderGlobal(); status.textContent = window.RadioI18n.t("ui.dc39cac03c63");
      } catch (error) { status.textContent = error.message; }
      finally { add.disabled = admin() !== global; }
    };
    const onAdmin = () => {
      if (admin() !== global) {
        add.disabled = true;
        if (global) {
          panel.querySelectorAll('button').forEach(button => { if (button !== close) button.disabled = true; });
          status.textContent = window.RadioI18n.t("ui.001990993ac3");
        } else panel.close();
      }
    };
    const onGlobal = () => { if (panel.open) renderGlobal().catch(error => { status.textContent = error.message; }); };
    window.addEventListener('radio:adminchange', onAdmin);
    window.addEventListener('radio:globalfilterschange', onGlobal);
    panel.addEventListener('close', () => {
      window.removeEventListener('radio:adminchange', onAdmin);
      window.removeEventListener('radio:globalfilterschange', onGlobal);
    }, {once: true});
    panel.append(close, heading, note, list, form, personal, status);
    window.RadioDialogs.dismissOutside(panel);
    document.body.append(panel); renderPersonal(); panel.showModal();
    try { await renderGlobal(); } catch (error) { status.textContent = error.message; }
  }
  document.addEventListener('click', event => { if (event.target.closest('[data-open-exclusions]')) open(); });
  const check = () => refresh().catch(() => { /* A later check retries; do not trust old cached results. */ window.RadioCache.clear(); });
  window.addEventListener('radio:pagechange', () => { labels(); check(); });
  window.addEventListener('radio:adminchange', () => {
    const active = admin();
    if (active !== previousAdmin) { previousAdmin = active; changed(); }
    else labels();
  });
  window.addEventListener('focus', check);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) check(); });
  window.setInterval(() => { if (!document.hidden) check(); }, 30000);
  labels();
  return {apply, hide, promotePersonal, exports, refresh, fetch: filteredFetch, live: (url, options) => filteredFetch(url, options, false), ready: () => revision !== null, key: () => `${revision}:${JSON.stringify(hidden)}`};
})();
