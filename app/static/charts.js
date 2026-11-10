window.RadioPages = window.RadioPages || {};
window.RadioPages.charts = () => {
const filters = document.querySelector('#chart-filters');
const controls = document.querySelector('#chart-controls');
const compact = window.matchMedia('(max-width: 760px)');
const resizeControls = () => { controls.open = !compact.matches; };
resizeControls(); compact.addEventListener('change', resizeControls);
const radioChoices = [...document.querySelectorAll('[data-chart-radio]')];
const initial = new URLSearchParams(location.search);
const initialIds = (initial.get('station_ids') || initial.get('station_id') || '').split(',');
radioChoices.forEach(input => { input.checked = initialIds.includes(input.value); });
function selectedIds() { return radioChoices.filter(input => input.checked).map(input => input.value).sort((a, b) => Number(a) - Number(b)); }
function stationParams(params) {
  const ids = selectedIds();
  if (ids.length === 1) params.set('station_id', ids[0]);
  else if (ids.length > 1) params.set('station_ids', ids.join(','));
}
const podium = document.querySelector('#chart-podium');
const list = document.querySelector('#chart-list');
const status = document.querySelector('#chart-status');
let days = new URLSearchParams(location.search).get('days') || '7';
if (!['1', '7', '30', 'all'].includes(days)) days = '7';
let pending, retryTimer;
let chartVersion = 0;
let renderedChart = null;
let chartScope = {};

function node(tag, text = '', className = '') {
  const result = document.createElement(tag);
  result.textContent = text;
  result.className = className;
  return result;
}
function cover(track, rank, large = false) {
  const art = node('div', '', `cover cover-${(rank - 1) % 6}`);
  art.setAttribute('aria-hidden', 'true');
  art.append(node('span', (track.artist || track.title).trim().slice(0, 2).toUpperCase(), 'cover-initials'));
  if (track.metadata?.cover_url) {
    const image = node('img', '', 'cover-image');
    image.src = track.metadata.cover_url;
    image.alt = '';
    image.loading = 'lazy';
    image.addEventListener('error', () => image.remove(), {once: true});
    art.append(image);
  }
  if (large) art.append(node('b', String(rank).padStart(2, '0'), 'cover-rank'));
  return art;
}
function copy(track) {
  const text = node('div', '', 'track-copy');
  text.append(node('strong', track.title), node('small', track.artist || window.RadioI18n.t("ui.133df509234a")));
  return text;
}
function count(track) {
  return node('span', window.RadioI18n.plural('airplay.one', 'airplay.other', track.plays), 'play-count');
}
function renderChart(tracks) {
  const signature = JSON.stringify([chartScope, tracks]);
  if (signature === renderedChart) return;
  renderedChart = signature;
  const cards = [];
  const rows = [];
  tracks.forEach((track, index) => {
    const rank = index + 1;
    const link = node('button');
    link.type = 'button';
    const detailTrack = {...track, history_scope: {...chartScope}, station_id: selectedIds().length === 1 ? selectedIds()[0] : null};
    link.addEventListener('click', () => window.TrackCatalog.open(detailTrack, {previewQueue: tracks}));
    link.setAttribute('aria-label', window.RadioI18n.t("ui.3de4c767b8b4", {p0: (rank), p1: (track.title), p2: (track.artist || window.RadioI18n.t("ui.133df509234a")), p3: (track.plays)}));
    if (index < 3) {
      const card = node('article', '', 'podium-card');
      link.className = 'podium-detail';
      const meta = node('div', '', 'podium-meta');
      meta.append(copy(track), count(track));
      link.append(cover(track, rank, true), meta);
      card.append(link, window.TrackCatalog.playButton(track, null, true, tracks));
      cards.push(card);
    } else {
      link.className = 'chart-row';
      link.append(node('span', String(rank).padStart(2, '0'), 'rank'), cover(track, rank), copy(track), count(track), node('span', '›', 'row-arrow'));
      const row = node('div', '', 'chart-track');
      row.append(link, window.TrackCatalog.playButton(track, null, true, tracks));
      rows.push(row);
    }
  });
  podium.replaceChildren(...cards);
  list.replaceChildren(...rows);
  if (!tracks.length) list.append(node('p', window.RadioI18n.t("ui.50bd7ac3ece8"), 'empty'));
  status.textContent = tracks.length ? window.RadioI18n.t("ui.8f7f0c04bc93", {p0: (tracks.length)}) : window.RadioI18n.t("ui.1d37e42be836");
}
function setScope(value) {
  chartScope = {...value};
  const exact = window.MusicFilters.apply(new URLSearchParams(chartScope));
  exact.set('view', 'tracks'); exact.set('sort', 'frequency'); window.MusicFilters.exports(exact);
  window.RadioPlaylists?.configure(chartScope);
}
function datedStatus(value) {
  return window.RadioI18n.t(Date.now() - Date.parse(value) > 60000 ? 'ranking.stale' : 'ranking.updated', {time: new Date(value).toLocaleString(window.RadioI18n?.locale || 'fr-FR')});
}
async function loadChart(force = false) {
  clearTimeout(retryTimer);
  if (pending) pending.abort();
  pending = new AbortController();
  const version = ++chartVersion;
  const params = new URLSearchParams({limit: '20'});
  stationParams(params);
  if (days !== 'all') params.set('since', new Date(Math.floor(Date.now() / 30000) * 30000 - Number(days) * 86400000).toISOString());
  chartScope = Object.fromEntries(params);
  delete chartScope.limit;
  window.MusicFilters.apply(params);
  const exportParams = new URLSearchParams(params);
  exportParams.set('view', 'tracks'); exportParams.set('sort', 'frequency');
  window.MusicFilters.exports(exportParams);
  window.RadioPlaylists?.configure(chartScope);
  const shared = new URLSearchParams({days});
  stationParams(shared);
  history.replaceState(null, '', `/?${shared}${location.hash}`);
  const periods = {'1': window.RadioI18n.t("ui.68294a9a19aa"), '7': window.RadioI18n.t("ui.1ffaa48893f1"), '30': window.RadioI18n.t("ui.558ee1cbc179"), all: window.RadioI18n.t("ui.0dd29de4afba")};
  controls.querySelector('summary').textContent = window.RadioI18n.t("ui.7b3d0d0c9bf0", {p0: (periods[days]), p1: (selectedIds().length ? selectedIds().length + window.RadioI18n.t("ui.0e97c9b48c9b") : window.RadioI18n.t("ui.f34cfabe0946"))});
  document.querySelector('#chart-subtitle').textContent = `${selectedIds().length ? radioChoices.filter(input => input.checked).map(input => input.parentElement.textContent.trim()).join(', ') : window.RadioI18n.t("ui.3d7e6d53866b")} · ${periods[days]}`;
  document.querySelectorAll('[data-days]').forEach(button => {
    const active = button.dataset.days === days;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
  const cacheKey = `chart:${selectedIds().join(',')}:${days}:${window.MusicFilters.key()}`;
  const cached = window.MusicFilters.ready() ? window.RadioCache.get(cacheKey) : null;
  if (cached) {
    setScope(cached.data.scope || chartScope);
    renderChart(cached.data.items || cached.data);
    status.textContent = datedStatus(cached.data.calculated_at || new Date(cached.time).toISOString());

  }
  list.setAttribute('aria-busy', 'true');
  if (!cached) status.textContent = window.RadioI18n.t("ui.92b44edc9279");
  try {
    const preparedParams = new URLSearchParams(params); preparedParams.delete('since'); preparedParams.set('period', days);
    const response = await window.MusicFilters.live(`/api/stats/prepared?${preparedParams}`, {signal: pending.signal, cache: 'no-store'});
    if (!response.ok) {
      if (response.status === 503 && version === chartVersion) retryTimer = setTimeout(() => loadChart(true), 2000);
      throw new Error(window.RadioI18n.t("ui.1855e03b96c6"));
    }
    const snapshot = await response.json();
    const data = snapshot.items;
    if (version === chartVersion) {
      chartScope = {...chartScope, until: snapshot.until};
      if (snapshot.since) chartScope.since = snapshot.since; else delete chartScope.since;
      setScope(chartScope);
      window.RadioCache.put(cacheKey, {items: data, scope: {...chartScope}, calculated_at: snapshot.calculated_at});
      renderChart(data);
      status.textContent = window.RadioI18n.t(snapshot.stale ? 'ranking.stale' : 'ranking.updated', {time: new Date(snapshot.calculated_at).toLocaleString(window.RadioI18n?.locale || 'fr-FR')});
    }
  } catch (error) {
    if (error.name !== 'AbortError' && version === chartVersion) {
      status.textContent = `${error.message}${cached ? window.RadioI18n.t("ui.5e11c8bfaea3") : ''}`;
      if (cached) { status.textContent = `${datedStatus(cached.data.calculated_at || new Date(cached.time).toISOString())} · ${error.message}`; return; }
      renderedChart = null;
      podium.replaceChildren();
      list.replaceChildren();
      const retry = node('button', window.RadioI18n.t("ui.af273d956b5b"), 'text-button');
      retry.type = 'button';
      retry.addEventListener('click', () => loadChart(true));
      list.append(retry);
    }
  } finally {
    if (version === chartVersion) list.setAttribute('aria-busy', 'false');
  }
}
filters.addEventListener('submit', event => event.preventDefault());
filters.querySelectorAll('[data-days]').forEach(button => button.addEventListener('click', () => { days = button.dataset.days; loadChart(); }));
radioChoices.forEach(input => input.addEventListener('change', () => loadChart()));
filters.querySelector('[data-chart-all]').addEventListener('click', () => {
  radioChoices.forEach(input => { input.checked = false; }); loadChart();
});
const timer = window.setInterval(() => { if (!document.hidden) loadChart(true); }, 30000);
const onVisibility = () => { if (!document.hidden) loadChart(true); };
document.addEventListener('visibilitychange', onVisibility);
const onFilters = () => loadChart(true);
window.addEventListener('radio:filterschange', onFilters);
window.addEventListener('radio:catalogchange', onFilters);
loadChart();

return () => {
  chartVersion++;
  pending?.abort(); clearTimeout(retryTimer);
  compact.removeEventListener('change', resizeControls);
  window.clearInterval(timer);
  document.removeEventListener('visibilitychange', onVisibility);
  window.removeEventListener('radio:filterschange', onFilters);
  window.removeEventListener('radio:catalogchange', onFilters);
};

};
