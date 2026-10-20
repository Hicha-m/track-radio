// Search the local radio catalogue. Typing never calls a music provider.
(() => {
  let dialog;
  let request;
  let debounce;
  let version = 0;
  let refresh;
  let lastKey = '';
  let lastUpdated = 0;
  let scroll = 0;
  function el(tag, text = '', className = '') {
    const node = document.createElement(tag);
    node.textContent = text;
    node.className = className;
    return node;
  }
  function open() {
    if (dialog?.open) { dialog.querySelector('input').focus(); return; }
    if (dialog) {
      dialog.showModal(); dialog.querySelector('input').focus({preventScroll: true}); dialog.scrollTop = scroll;
      if (Date.now() - lastUpdated > 60000 || lastKey !== `${dialog.querySelector('input').value.trim()}:${window.MusicFilters.key()}`) refresh();
      return;
    }
    dialog = el('dialog', '', 'music-search-dialog');
    dialog.setAttribute('aria-labelledby', 'music-search-title');
    const title = el('h2', 'Rechercher', 'music-search-title');
    title.id = 'music-search-title';
    const close = el('button', 'Fermer', 'text-button');
    close.type = 'button';
    close.addEventListener('click', () => dialog.close());
    const header = el('div', '', 'music-search-heading');
    header.append(title, close);
    const label = el('label', '', 'music-search-input');
    label.append(el('span', 'Un titre, un artiste', 'sr-only'));
    const input = el('input');
    input.type = 'search';
    input.placeholder = 'Un titre, un artiste…';
    input.maxLength = 200;
    input.autocomplete = 'off';
    try { input.value = sessionStorage.getItem('radio-search-query') || ''; } catch (_) { /* Optional. */ }
    label.append(input);
    const status = el('p', 'Retrouvez les musiques diffusées sur vos radios.', 'hint');
    status.setAttribute('role', 'status');
    const results = el('div', '', 'music-search-results');
    const more = el('a', 'Voir tous les résultats', 'music-search-more');
    more.hidden = true;
    dialog.append(header, label, status, results, more);
    dialog.addEventListener('close', () => {
      scroll = dialog.scrollTop;
      version++;
      request?.abort();
      clearTimeout(debounce);
    });
    const panel = dialog;
    async function search() {
      const q = input.value.trim();
      request?.abort();
      const current = ++version;
      results.replaceChildren();
      results.removeAttribute('aria-busy');
      more.hidden = true;
      if (q.length < 2) { status.textContent = 'Saisissez au moins deux caractères.'; return; }
      const params = window.MusicFilters.apply(new URLSearchParams({view: 'tracks', q, sort: 'frequency', page_size: '12'}));
      request = new AbortController();
      status.textContent = 'Recherche…';
      results.setAttribute('aria-busy', 'true');
      try {
        const key = `quick-search:${q}:${window.MusicFilters.key()}`;
        const cached = window.RadioCache.get(key);
        let data = cached && Date.now() - cached.time < 60000 ? cached.data : null;
        if (!data) {
          const response = await window.MusicFilters.fetch(`/api/explore?${params}`, {signal: request.signal});
          if (!response.ok) throw new Error('Recherche indisponible. Réessayez.');
          data = await response.json();
          window.RadioCache.put(key, data);
        }
        if (current !== version || !panel.open) return;
        status.textContent = data.items.length ? 'Morceaux' : 'Aucune musique trouvée. Essayez un autre titre ou artiste.';
        for (const track of data.items) {
          const row = el('div', '', 'music-search-row');
          const detail = el('button', '', 'music-search-result');
          detail.type = 'button';
          const art = el('span', (track.artist || track.title).slice(0, 2).toUpperCase(), 'search-result-art');
          if (track.metadata?.cover_url) {
            const image = el('img');
            image.src = track.metadata.cover_url;
            image.alt = '';
            image.loading = 'lazy';
            image.addEventListener('error', () => image.remove(), {once: true});
            art.append(image);
          }
          const text = el('span', '', 'track-copy');
          text.append(el('strong', track.title), el('small', track.artist || 'Artiste inconnu'));
          detail.append(art, text);
          detail.addEventListener('click', () => { panel.close(); window.TrackCatalog.open(track, {onBack: open}); });
          row.append(detail, window.TrackCatalog.playButton(track));
          results.append(row);
        }
        more.href = `/recherche?${new URLSearchParams({view: 'tracks', q, sort: 'frequency'})}`;
        more.hidden = !data.items.length;
        lastKey = `${q}:${window.MusicFilters.key()}`; lastUpdated = Date.now();
      } catch (error) { if (error.name !== 'AbortError' && current === version) status.textContent = error.message; }
      finally { if (current === version) results.removeAttribute('aria-busy'); }
    }
    input.addEventListener('input', () => {
      try { sessionStorage.setItem('radio-search-query', input.value); } catch (_) { /* Optional. */ }
      clearTimeout(debounce);
      request?.abort();
      version++;
      debounce = setTimeout(search, 250);
    });
    refresh = search;
    window.RadioDialogs.dismissOutside(dialog);
    document.body.append(dialog);
    dialog.showModal();
    input.focus();
    if (input.value.trim().length >= 2) search();
  }
  document.addEventListener('click', event => {
    if (event.target.closest('[data-open-search]')) { event.preventDefault(); open(); }
  });
  document.addEventListener('keydown', event => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault(); open();
    }
  });
  window.addEventListener('radio:filterschange', () => { if (dialog?.open) refresh(); });
  window.QuickSearch = {open};
  window.addEventListener('radio:pagechange', () => { if (dialog?.open) dialog.close(); });
})();
