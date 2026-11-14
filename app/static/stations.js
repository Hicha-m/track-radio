// One local metadata request updates every station card; audio is controlled only by RadioPlayer.
(() => {
  let controller = null, generation = 0, mounted = false;
  let observations = new Map();
  let checkedAt = 0, requestedAt = 0;
  const rendered = new WeakMap(), artwork = new WeakMap();
  const cards = () => [...document.querySelectorAll('.station-card[data-station-id]')];
  const date = value => new Date(value).toLocaleString(window.RadioI18n?.locale || 'fr-FR');
  const experimental = document.querySelector('#experimental-radio-youtube');
  let historyDialog = null, historyRequest = null, historyStation = null, historyPage = 1, historyVersion = 0;
  function setExperimental(value = experimental?.checked) {
    const enabled = Boolean(value);
    if (experimental) experimental.checked = enabled;
    cards().forEach(card => { card.querySelector('.station-youtube-play').hidden = !enabled; });
    try { localStorage.setItem('radio-youtube-alternative', String(enabled)); } catch (_) { /* Session still works. */ }
  }
  function ensureHistoryDialog() {
    if (historyDialog) return historyDialog;
    historyDialog = document.createElement('dialog'); historyDialog.className = 'station-history-dialog';
    historyDialog.setAttribute('aria-labelledby', 'station-history-title');
    const header = document.createElement('div'); header.className = 'station-history-heading';
    const heading = document.createElement('h2'); heading.id = 'station-history-title';
    const close = document.createElement('button'); close.type = 'button'; close.className = 'text-button'; close.textContent = window.RadioI18n.t('ui.711e5f2e198d');
    close.onclick = () => historyDialog.close(); header.append(heading, close);
    const status = document.createElement('p'); status.className = 'hint'; status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
    const items = document.createElement('ol'); items.className = 'station-history-items';
    const more = document.createElement('button'); more.type = 'button'; more.className = 'text-button station-history-more'; more.textContent = window.RadioI18n.t('stations.history_more'); more.hidden = true;
    more.onclick = () => loadHistory(historyPage + 1, true);
    historyDialog.append(header, status, items, more); document.body.append(historyDialog);
    historyDialog.addEventListener('close', () => { historyVersion++; historyRequest?.abort(); historyRequest = null; });
    window.RadioDialogs.dismissOutside(historyDialog);
    historyDialog._heading = heading; historyDialog._status = status; historyDialog._items = items; historyDialog._more = more;
    return historyDialog;
  }
  async function loadHistory(page = 1, append = false) {
    const dialog = ensureHistoryDialog();
    if (!historyStation) return;
    historyRequest?.abort(); const controller = new AbortController(); historyRequest = controller;
    const ticket = ++historyVersion; historyPage = page;
    if (!append) dialog._items.replaceChildren();
    dialog._more.hidden = true; dialog._status.textContent = window.RadioI18n.t('stations.history_loading');
    const params = window.MusicFilters.apply(new URLSearchParams({view: 'plays', sort: 'newest', station_id: String(historyStation.id), page: String(page), page_size: '25'}));
    try {
      const response = await window.MusicFilters.fetch(`/api/explore?${params}`, {signal: controller.signal, cache: 'no-store'});
      const data = await response.json();
      if (!response.ok) throw new Error(window.RadioI18n.t('stations.history_error'));
      if (ticket !== historyVersion || !dialog.open) return;
      for (const play of data.items) {
        const li = document.createElement('li'), button = document.createElement('button');
        button.type = 'button';
        const title = document.createElement('strong'); title.textContent = play.title;
        const artist = document.createElement('small'); artist.textContent = play.artist || '—';
        button.append(title, artist);
        button.onclick = () => { dialog.close(); window.TrackCatalog.open({...play, station_id: historyStation.id}, {onBack: () => openHistory(historyStation.id, historyStation.name)}); };
        const time = document.createElement('time'); time.dateTime = play.detected_at; time.textContent = date(play.detected_at);
        li.append(button, time); dialog._items.append(li);
      }
      dialog._status.textContent = data.items.length ? `${data.items.length} · ${window.RadioI18n.t('stations.history')}` : window.RadioI18n.t('stations.history_empty');
      historyPage = data.page; dialog._more.hidden = !data.has_more;
    } catch (error) {
      if (error.name !== 'AbortError' && ticket === historyVersion) dialog._status.textContent = error.message || window.RadioI18n.t('stations.history_error');
    }
  }
  function openHistory(id, name) {
    const dialog = ensureHistoryDialog(); historyStation = {id: Number(id), name};
    dialog._heading.textContent = window.RadioI18n.t('stations.history_title', {station: name});
    historyPage = 1; if (!dialog.open) dialog.showModal(); void loadHistory();
  }
  function cover(button, url) {
    const previous = artwork.get(button);
    if (previous === (url || '')) return;
    artwork.set(button, url || '');
    const placeholder = document.createElement('span');
    placeholder.className = 'station-cover-placeholder'; placeholder.textContent = '♫'; placeholder.setAttribute('aria-hidden', 'true');
    button.replaceChildren(placeholder);
    if (!url) return;
    const image = document.createElement('img');
    image.className = 'station-cover-image'; image.alt = ''; image.loading = 'lazy'; image.referrerPolicy = 'no-referrer';
    image.onerror = () => image.remove(); image.src = url; button.append(image);
  }
  function render(card, entry, loading = false) {
    let state = entry?.status || 'unavailable';
    const now = checkedAt + performance.now() - requestedAt;
    const expires = Date.parse(entry?.fresh_until || '');
    if (state === 'current' && (!Number.isFinite(expires) || now > expires)) state = 'last_known';
    const track = ['current', 'last_known'].includes(state) && entry.track?.title ? entry.track : null;
    if (!track && state !== 'hidden') state = 'unavailable';
    const signature = JSON.stringify([state, track, entry?.observed_at, loading]);
    if (rendered.get(card) === signature) return;
    rendered.set(card, signature); card.dataset.nowStatus = state;
    const button = card.querySelector('.station-track');
    button.disabled = !track;
    button.onclick = track ? () => window.TrackCatalog.open({...track, station_id: Number(card.dataset.stationId)}) : null;
    button.setAttribute('aria-label', window.RadioI18n.t('stations.details', {station: card.querySelector('.station-history-open').textContent}));
    cover(button, track?.metadata?.cover_url);
    card.querySelector('.station-title').textContent = track?.title || window.RadioI18n.t(loading ? 'stations.loading' : 'stations.unavailable');
    card.querySelector('.station-artist').textContent = track?.artist || '';
    const status = card.querySelector('.station-now-status');
    status.textContent = track ? window.RadioI18n.t(state === 'current' ? 'stations.current' : 'stations.last_known', {time: entry.observed_at ? date(entry.observed_at) : '—'}) : '';
    status.title = track && entry.observed_at ? window.RadioI18n.t('stations.observed', {time: date(entry.observed_at)}) : '';
  }
  function age() {
    if (!mounted || document.hidden) return;
    cards().forEach(card => {
      const item = observations.get(card.dataset.stationId);
      render(card, item, !item && Boolean(controller));
    });
  }
  function reset() {
    generation++; controller?.abort(); controller = null; observations = new Map();
    cards().forEach(card => render(card, null, true));
  }
  async function load(force = false) {
    const targets = cards();
    if (!mounted || document.hidden || !targets.length || (controller && !force)) return;
    controller?.abort();
    const request = new AbortController(); controller = request;
    const ticket = ++generation, started = performance.now();
    const timeout = setTimeout(() => request.abort(), 12000);
    try {
      const params = window.MusicFilters.apply(new URLSearchParams());
      const response = await window.MusicFilters.live(`/api/stations/now-playing?${params}`, {signal: request.signal});
      if (!response.ok) throw new Error('station_metadata_unavailable');
      const data = await response.json();
      if (ticket !== generation || request.signal.aborted) return;
      if (!Array.isArray(data.items) || !Number.isFinite(Date.parse(data.checked_at))) throw new Error('invalid_station_metadata');
      checkedAt = Date.parse(data.checked_at); requestedAt = started;
      observations = new Map(data.items.map(item => [String(item.station_id), item]));
      age();
    } catch (_) {
      if (ticket !== generation) return;
      // Keep only explicitly dated last-known information through a temporary transport failure.
      observations.forEach(item => { if (item.status === 'current') item.status = 'last_known'; });
      controller = null; age();
    } finally {
      clearTimeout(timeout);
      if (ticket === generation) controller = null;
    }
  }
  try { setExperimental(localStorage.getItem('radio-youtube-alternative') === 'true'); } catch (_) { setExperimental(false); }
  experimental?.addEventListener('change', () => setExperimental());
  document.addEventListener('click', event => {
    const history = event.target.closest('.station-history-open');
    if (history) { openHistory(history.dataset.id, history.textContent.trim()); return; }
    const playlist = event.target.closest('.station-youtube-play');
    if (playlist && experimental?.checked) window.RadioPlaylists?.openStation(playlist.dataset.id);
  });
  window.addEventListener('radio:beforepagechange', () => { mounted = false; reset(); historyDialog?.close(); });
  window.addEventListener('radio:pagechange', () => { mounted = true; reset(); setExperimental(); load(); });
  window.addEventListener('radio:filterschange', () => { reset(); load(); if (historyDialog?.open) { historyPage = 1; void loadHistory(); } });
  window.addEventListener('radio:catalogchange', () => load(true));
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { generation++; controller?.abort(); controller = null; }
    else { age(); load(); }
  });
  setInterval(age, 1000);
  setInterval(() => load(), 30000);
})();

window.RadioPages = window.RadioPages || {};
window.RadioPages.stations = () => {
  const input = document.querySelector('#station-search');
  const status = document.querySelector('#station-search-status');
  const cards = [...document.querySelectorAll('.station-card')];
  const normalize = text => text.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase().trim();
  const search = () => {
    const query = normalize(input.value);
    let count = 0;
    cards.forEach(card => {
      card.hidden = !normalize(card.querySelector('.station-history-open').textContent).includes(query);
      if (!card.hidden) count++;
    });
    status.textContent = window.RadioI18n.t('stations.results', {count});
  };
  input.addEventListener('input', search); search();
  return () => input.removeEventListener('input', search);
};
