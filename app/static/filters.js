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
    document.querySelectorAll('[data-open-exclusions]').forEach(b => { b.textContent = admin() ? 'Titres masqués pour tous' : `Titres masqués (${hidden.length})`; });
  }
  function changed() {
    generation++;
    window.RadioCache.clear();
    labels();
    window.dispatchEvent(new Event('radio:filterschange'));
  }
  function acceptRevision(value) {
    if (typeof value !== 'string' || value === revision) return;
    revision = value;
    changed();
    window.dispatchEvent(new Event('radio:globalfilterschange'));
  }
  async function refresh() {
    if (checking) return checking;
    const current = generation;
    checking = fetch('/api/exclusions/revision', {cache: 'no-store', credentials: 'same-origin'})
      .then(async response => {
        if (!response.ok) throw new Error('Masquage global indisponible. Réessayez.');
        const data = await response.json();
        if (current === generation) acceptRevision(data.revision);
      }).finally(() => { checking = null; });
    return checking;
  }
  async function filteredFetch(url, options = {}) {
    if (revision === null) await refresh();
    const current = generation;
    const response = await fetch(url, {...options, cache: 'no-store'});
    const json = response.json.bind(response);
    response.json = async () => {
      const data = await json();
      if (current !== generation) throw new DOMException('Filtres modifiés', 'AbortError');
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
    if (!item.title) throw new Error('Saisissez un titre.');
    if (global) {
      const data = await window.RadioAdmin.request('/api/admin/exclusions', item);
      acceptRevision(data.revision);
      return;
    }
    if (hidden.some(t => same(t, item))) return;
    if (hidden.length >= 50 || JSON.stringify([...hidden, item]).length > 11000) throw new Error('La liste est pleine. Retirez un titre masqué.');
    hidden.push(item); save();
  }
  async function promotePersonal() {
    if (!admin()) throw new Error('Session administrateur requise.');
    if (!hidden.length) return;
    const data = await window.RadioAdmin.request('/api/admin/exclusions/bulk', {items: hidden.map(t => ({...t}))});
    acceptRevision(data.revision);
    return data;
  }
  function exports(params) {
    const selection = new URLSearchParams(params);
    ['limit', 'page', 'page_size', 'snapshot_id'].forEach(k => selection.delete(k));
    document.querySelectorAll('[data-export-format]').forEach(link => {
      selection.set('format', link.dataset.exportFormat);
      link.href = `/api/export?${selection}`;
    });
  }
  async function open() {
    dialog?.close(); dialog?.remove();
    const panel = el('dialog', '', 'exclusions-dialog'); dialog = panel;
    const global = admin();
    panel.setAttribute('aria-labelledby', 'exclusions-title');
    const heading = el('h2', global ? 'Titres masqués pour tous' : 'Mes titres masqués'); heading.id = 'exclusions-title';
    const close = el('button', 'Fermer', 'text-button'); close.type = 'button'; close.onclick = () => panel.close();
    const note = el('p', global
      ? 'Cette blacklist s’applique à tous et aux statistiques. Les nouvelles diffusions ne sont plus collectées ; l’historique est conservé hors des résultats.'
      : 'Votre liste reste sur cet appareil et s’ajoute au masquage global. Elle ne peut pas réafficher un titre masqué pour tous.', 'hint');
    const list = el('div');
    const personal = el('div');
    const status = el('p', '', 'hint'); status.setAttribute('role', 'status');
    let entries = [];
    let listVersion = 0;
    function renderPersonal() {
      personal.replaceChildren();
      if (global) personal.append(el('h3', 'Mes exclusions personnelles sur cet appareil'));
      hidden.forEach((track, index) => {
        const row = el('div', '', 'hidden-track');
        const restore = el('button', 'Retirer de ma liste', 'text-button'); restore.type = 'button';
        restore.onclick = () => { hidden.splice(index, 1); save(); renderPersonal(); };
        row.append(el('span', `${track.title} · ${track.artist || 'Artiste inconnu'}`), restore); personal.append(row);
      });
      if (!hidden.length) personal.append(el('p', 'Aucun titre masqué personnellement.', 'hint'));
      if (global) {
        personal.append(el('p', 'Ces exclusions restent actives pendant votre session admin. Retirer une exclusion personnelle ne change pas la blacklist globale.', 'hint'));
        const promote = el('button', `Appliquer mes ${hidden.length} exclusions personnelles à tous`, 'text-button'); promote.type = 'button';
        promote.disabled = !hidden.length || !admin();
        promote.onclick = async () => {
          promote.disabled = true;
          try {
            await promotePersonal(); renderPersonal(); await renderGlobal();
            status.textContent = 'Exclusions appliquées à tous. Votre liste personnelle est conservée.';
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
        const restore = el('button', 'Réafficher pour tous', 'text-button'); restore.type = 'button';
        restore.onclick = async () => {
          restore.disabled = true;
          try {
            const result = await window.RadioAdmin.request(`/api/admin/exclusions/${track.id}`, {}, 'DELETE');
            acceptRevision(result.revision);
            await renderGlobal();
            status.textContent = 'Masquage global retiré. Les exclusions personnelles restent actives.';
          } catch (error) { status.textContent = error.message; }
          finally { restore.disabled = !admin(); }
        };
        row.append(el('span', `${track.title} · ${track.artist || 'Artiste inconnu'}`), restore); list.append(row);
      }
      if (!entries.length) list.append(el('p', 'Aucun titre masqué pour tous.', 'hint'));
    }
    const form = el('form', '', 'exclusion-form');
    const title = el('input'); title.placeholder = 'Titre exact'; title.required = true; title.maxLength = 500;
    const artist = el('input'); artist.placeholder = 'Artiste (vide si inconnu)'; artist.maxLength = 300;
    const titleLabel = el('label', 'Titre'); titleLabel.append(title);
    const artistLabel = el('label', 'Artiste'); artistLabel.append(artist);
    const add = el('button', global ? 'Masquer pour tous' : 'Masquer pour moi', 'text-button'); add.type = 'submit';
    form.append(titleLabel, artistLabel, add);
    form.onsubmit = async event => {
      event.preventDefault(); add.disabled = true;
      try {
        await hide({title: title.value, artist: artist.value}, global);
        form.reset(); renderPersonal(); await renderGlobal(); status.textContent = 'Filtre enregistré.';
      } catch (error) { status.textContent = error.message; }
      finally { add.disabled = admin() !== global; }
    };
    const onAdmin = () => {
      if (admin() !== global) {
        add.disabled = true;
        if (global) {
          panel.querySelectorAll('button').forEach(button => { if (button !== close) button.disabled = true; });
          status.textContent = 'Session administrateur expirée. Reconnectez-vous pour modifier la liste globale.';
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
  return {apply, hide, promotePersonal, exports, refresh, fetch: filteredFetch, key: () => `${revision}:${JSON.stringify(hidden)}`};
})();
