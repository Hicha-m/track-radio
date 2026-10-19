// Personal exclusions stay in this browser and are sent as filters, never deletions.
window.MusicFilters = (() => {
  let hidden = [];
  let dialog;
  const storageKey = 'radio-hidden-tracks-v1';
  const same = (a, b) => (a.artist || '').toLocaleLowerCase() === (b.artist || '').toLocaleLowerCase() && a.title.toLocaleLowerCase() === b.title.toLocaleLowerCase();
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
    document.querySelectorAll('[data-open-exclusions]').forEach(b => { b.textContent = `Titres masqués (${hidden.length})`; });
  }
  function save() {
    try { localStorage.setItem(storageKey, JSON.stringify(hidden)); } catch (_) { /* Optional. */ }
    labels();
    window.dispatchEvent(new Event('radio:filterschange'));
  }
  function hide(track) {
    const item = {artist: (track.artist || '').trim() || null, title: track.title.trim()};
    if (!item.title || hidden.some(t => same(t, item))) return;
    if (hidden.length >= 50 || JSON.stringify([...hidden, item]).length > 11000) throw new Error('La liste est pleine. Retirez un titre masqué.');
    hidden.push(item); save();
  }
  function exports(params) {
    const selection = new URLSearchParams(params);
    ['limit', 'page', 'page_size', 'snapshot_id'].forEach(k => selection.delete(k));
    document.querySelectorAll('[data-export-format]').forEach(link => {
      selection.set('format', link.dataset.exportFormat);
      link.href = `/api/export?${selection}`;
    });
  }
  function open() {
    dialog?.remove();
    dialog = el('dialog', '', 'exclusions-dialog');
    dialog.setAttribute('aria-labelledby', 'exclusions-title');
    const heading = el('h2', 'Titres masqués'); heading.id = 'exclusions-title';
    const close = el('button', 'Fermer', 'text-button'); close.type = 'button'; close.onclick = () => dialog.close();
    const note = el('p', 'Cette liste est conservée sur cet appareil. Les passages restent enregistrés.', 'hint');
    const list = el('div');
    const status = el('p', '', 'hint'); status.setAttribute('role', 'status');
    function render() {
      list.replaceChildren();
      hidden.forEach((track, index) => {
        const row = el('div', '', 'hidden-track');
        const restore = el('button', 'Réafficher', 'text-button'); restore.type = 'button';
        restore.onclick = () => { hidden.splice(index, 1); save(); render(); };
        row.append(el('span', `${track.title} · ${track.artist || 'Artiste inconnu'}`), restore); list.append(row);
      });
      if (!hidden.length) list.append(el('p', 'Aucun titre masqué.', 'hint'));
    }
    const form = el('form', '', 'exclusion-form');
    const title = el('input'); title.placeholder = 'Titre exact'; title.required = true; title.maxLength = 500;
    const artist = el('input'); artist.placeholder = 'Artiste (vide si inconnu)'; artist.maxLength = 300;
    const titleLabel = el('label', 'Titre'); titleLabel.append(title);
    const artistLabel = el('label', 'Artiste'); artistLabel.append(artist);
    const add = el('button', 'Masquer ce titre', 'text-button'); add.type = 'submit';
    form.append(titleLabel, artistLabel, add);
    form.onsubmit = event => {
      event.preventDefault();
      try { hide({title: title.value, artist: artist.value}); form.reset(); render(); status.textContent = 'Filtre enregistré.'; }
      catch (error) { status.textContent = error.message; }
    };
    const preset = el('button', 'Masquer Mouv’, NRJ / Hit Music Only ! et Les Dédicaces', 'text-button'); preset.type = 'button';
    preset.onclick = () => {
      try {
        [{artist: null, title: 'Mouv’'}, {artist: 'Hit Music Only !', title: 'NRJ'}, {artist: 'SKYROCK', title: 'Les Dédicaces'}].forEach(hide);
        render(); status.textContent = 'Annonces ajoutées aux titres masqués.';
      } catch (error) { status.textContent = error.message; }
    };
    dialog.append(close, heading, note, list, form, preset, status);
    window.RadioDialogs.dismissOutside(dialog);
    document.body.append(dialog); render(); dialog.showModal();
  }
  document.addEventListener('click', event => { if (event.target.closest('[data-open-exclusions]')) open(); });
  window.addEventListener('radio:pagechange', labels);
  labels();
  return {apply, hide, exports, key: () => JSON.stringify(hidden)};
})();
