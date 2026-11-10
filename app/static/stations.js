// One local metadata request updates every station card; audio is controlled only by RadioPlayer.
(() => {
  let controller = null, generation = 0, mounted = false;
  let observations = new Map();
  let checkedAt = 0, requestedAt = 0;
  const rendered = new WeakMap(), artwork = new WeakMap();
  const cards = () => [...document.querySelectorAll('.station-card[data-station-id]')];
  const date = value => new Date(value).toLocaleString(window.RadioI18n?.locale || 'fr-FR');
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
    button.setAttribute('aria-label', window.RadioI18n.t('stations.details', {station: card.querySelector('.station-heading strong').textContent}));
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
  window.addEventListener('radio:beforepagechange', () => { mounted = false; reset(); });
  window.addEventListener('radio:pagechange', () => { mounted = true; reset(); load(); });
  window.addEventListener('radio:filterschange', () => { reset(); load(); });
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
      card.hidden = !normalize(card.querySelector('.station-heading strong').textContent).includes(query);
      if (!card.hidden) count++;
    });
    status.textContent = window.RadioI18n.t('stations.results', {count});
  };
  input.addEventListener('input', search); search();
  return () => input.removeEventListener('input', search);
};
