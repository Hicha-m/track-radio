window.RadioPages = window.RadioPages || {};
window.RadioPages.charts = () => {
const filters = document.querySelector('#chart-filters');
const stationSelect = document.querySelector('#chart-station');
const podium = document.querySelector('#chart-podium');
const list = document.querySelector('#chart-list');
const status = document.querySelector('#chart-status');
let days = new URLSearchParams(location.search).get('days') || '7';
if (!['1', '7', '30', 'all'].includes(days)) days = '7';
let pending;
let chartVersion = 0;
let renderedChart = null;

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
  text.append(node('strong', track.title), node('small', track.artist || 'Artiste inconnu'));
  return text;
}
function count(track) {
  return node('span', `${Number(track.plays).toLocaleString('fr-FR')} ${track.plays === 1 ? 'diffusion' : 'diffusions'}`, 'play-count');
}
function renderChart(tracks) {
  const signature = JSON.stringify([stationSelect.value, tracks]);
  if (signature === renderedChart) return;
  renderedChart = signature;
  const cards = [];
  const rows = [];
  tracks.forEach((track, index) => {
    const rank = index + 1;
    const link = node('button');
    link.type = 'button';
    const detailTrack = {...track, station_id: stationSelect.value || null};
    link.addEventListener('click', () => window.TrackCatalog.open(detailTrack));
    link.setAttribute('aria-label', `${rank}. ${track.title}, ${track.artist || 'Artiste inconnu'}, ${track.plays} diffusions. Ouvrir les détails`);
    if (index < 3) {
      const card = node('article', '', 'podium-card');
      link.className = 'podium-detail';
      const meta = node('div', '', 'podium-meta');
      meta.append(copy(track), count(track));
      link.append(cover(track, rank, true), meta);
      card.append(link, window.TrackCatalog.playButton(track));
      cards.push(card);
    } else {
      link.className = 'chart-row';
      link.append(node('span', String(rank).padStart(2, '0'), 'rank'), cover(track, rank), copy(track), count(track), node('span', '›', 'row-arrow'));
      const row = node('div', '', 'chart-track');
      row.append(link, window.TrackCatalog.playButton(track));
      rows.push(row);
    }
  });
  podium.replaceChildren(...cards);
  list.replaceChildren(...rows);
  if (!tracks.length) list.append(node('p', 'Aucune diffusion sur cette période. Essayez une autre période ou une autre radio.', 'empty'));
  status.textContent = tracks.length ? `Top ${tracks.length} · Classement par nombre de diffusions observées` : 'Le classement attend ses premiers titres.';
}
async function loadChart(force = false) {
  if (pending) pending.abort();
  pending = new AbortController();
  const version = ++chartVersion;
  const params = new URLSearchParams({limit: '20'});
  if (stationSelect.value) params.set('station_id', stationSelect.value);
  if (days !== 'all') params.set('since', new Date(Math.floor(Date.now() / 30000) * 30000 - Number(days) * 86400000).toISOString());
  window.MusicFilters.apply(params);
  const exportParams = new URLSearchParams(params);
  exportParams.set('view', 'tracks'); exportParams.set('sort', 'frequency');
  window.MusicFilters.exports(exportParams);
  const shared = new URLSearchParams({days});
  if (stationSelect.value) shared.set('station_id', stationSelect.value);
  history.replaceState(null, '', `/?${shared}${location.hash}`);
  const periods = {'1': '24 dernières heures', '7': '7 derniers jours', '30': '30 derniers jours', all: 'Tout l’historique'};
  document.querySelector('#chart-subtitle').textContent = `${stationSelect.selectedOptions[0].textContent} · ${periods[days]}`;
  document.querySelectorAll('[data-days]').forEach(button => {
    const active = button.dataset.days === days;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
  const cacheKey = `chart:${stationSelect.value}:${days}:${window.MusicFilters.key()}`;
  const cached = window.RadioCache.get(cacheKey);
  if (cached) {
    renderChart(cached.data);
    status.textContent = `Classement mémorisé · ${new Date(cached.time).toLocaleTimeString('fr-FR')}`;
    if (!force && Date.now() - cached.time < 60000) {
      list.setAttribute('aria-busy', 'false');
      return;
    }
  }
  list.setAttribute('aria-busy', 'true');
  if (!cached) status.textContent = 'Actualisation du classement…';
  try {
    const response = await window.MusicFilters.fetch(`/api/stats/top?${params}`, {signal: pending.signal, cache: 'no-store'});
    if (!response.ok) throw new Error('Classement indisponible. Réessayez dans quelques instants.');
    const data = await response.json();
    if (version === chartVersion) {
      window.RadioCache.put(cacheKey, data);
      renderChart(data);
      status.textContent = `Top ${data.length} · actualisé à ${new Date().toLocaleTimeString('fr-FR')}`;
    }
  } catch (error) {
    if (error.name !== 'AbortError' && version === chartVersion) {
      status.textContent = `${error.message}${cached ? ' · Classement mémorisé conservé.' : ''}`;
      if (cached) return;
      renderedChart = null;
      podium.replaceChildren();
      list.replaceChildren();
      const retry = node('button', 'Réessayer', 'text-button');
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
stationSelect.addEventListener('change', () => loadChart());
const timer = window.setInterval(() => { if (!document.hidden) loadChart(true); }, 30000);
const onVisibility = () => { if (!document.hidden) loadChart(true); };
document.addEventListener('visibilitychange', onVisibility);
const onFilters = () => loadChart(true);
window.addEventListener('radio:filterschange', onFilters);
window.addEventListener('radio:catalogchange', onFilters);
loadChart();

return () => {
  chartVersion++;
  pending?.abort();
  window.clearInterval(timer);
  document.removeEventListener('visibilitychange', onVisibility);
  window.removeEventListener('radio:filterschange', onFilters);
  window.removeEventListener('radio:catalogchange', onFilters);
};

};
