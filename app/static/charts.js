const filters = document.querySelector('#chart-filters');
const stationSelect = document.querySelector('#chart-station');
const podium = document.querySelector('#chart-podium');
const list = document.querySelector('#chart-list');
const status = document.querySelector('#chart-status');
let days = new URLSearchParams(location.search).get('days') || '7';
if (!['1', '7', '30', 'all'].includes(days)) days = '7';
let pending;
let chartVersion = 0;

function node(tag, text = '', className = '') {
  const result = document.createElement(tag);
  result.textContent = text;
  result.className = className;
  return result;
}
function trackUrl(track) {
  const params = new URLSearchParams({view: 'plays', title: track.title, exact: 'true', sort: 'newest'});
  if (track.artist) params.set('artist', track.artist);
  else params.set('artist_status', 'missing');
  if (stationSelect.value) params.set('station_id', stationSelect.value);
  return `/recherche?${params}`;
}
function cover(track, rank, large = false) {
  // Placeholder only: future artwork can replace this node without changing the layout.
  const art = node('div', '', `cover cover-${(rank - 1) % 6}`);
  art.setAttribute('aria-hidden', 'true');
  art.append(node('span', (track.artist || track.title).trim().slice(0, 2).toUpperCase()));
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
  const cards = [];
  const rows = [];
  tracks.forEach((track, index) => {
    const rank = index + 1;
    const link = node('a');
    link.href = trackUrl(track);
    link.setAttribute('aria-label', `${rank}. ${track.title}, ${track.artist || 'Artiste inconnu'}, ${track.plays} diffusions. Voir les passages`);
    if (index < 3) {
      const card = node('article', '', 'podium-card');
      const meta = node('div', '', 'podium-meta');
      meta.append(copy(track), count(track));
      link.append(cover(track, rank, true), meta);
      card.append(link);
      cards.push(card);
    } else {
      link.className = 'chart-row';
      link.append(node('span', String(rank).padStart(2, '0'), 'rank'), cover(track, rank), copy(track), count(track), node('span', '↗', 'row-arrow'));
      rows.push(link);
    }
  });
  podium.replaceChildren(...cards);
  list.replaceChildren(...rows);
  if (!tracks.length) list.append(node('p', 'Aucune diffusion sur cette période. Essayez une autre période ou une autre radio.', 'empty'));
  status.textContent = tracks.length ? `Top ${tracks.length} · Classement par nombre de diffusions observées` : 'Le classement attend ses premiers titres.';
}
async function loadChart() {
  if (pending) pending.abort();
  pending = new AbortController();
  const version = ++chartVersion;
  const params = new URLSearchParams({limit: '20'});
  if (stationSelect.value) params.set('station_id', stationSelect.value);
  if (days !== 'all') params.set('since', new Date(Date.now() - Number(days) * 86400000).toISOString());
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
  list.setAttribute('aria-busy', 'true');
  status.textContent = 'Actualisation du classement…';
  try {
    const response = await fetch(`/api/stats/top?${params}`, {signal: pending.signal, cache: 'no-store'});
    if (!response.ok) throw new Error('Classement indisponible. Réessayez dans quelques instants.');
    const data = await response.json();
    if (version === chartVersion) renderChart(data);
  } catch (error) {
    if (error.name !== 'AbortError' && version === chartVersion) {
      status.textContent = error.message;
      podium.replaceChildren();
      list.replaceChildren();
      const retry = node('button', 'Réessayer', 'text-button');
      retry.type = 'button';
      retry.addEventListener('click', loadChart);
      list.append(retry);
    }
  } finally {
    if (version === chartVersion) list.setAttribute('aria-busy', 'false');
  }
}
filters.addEventListener('submit', event => event.preventDefault());
filters.querySelectorAll('[data-days]').forEach(button => button.addEventListener('click', () => { days = button.dataset.days; loadChart(); }));
stationSelect.addEventListener('change', loadChart);
window.setInterval(() => { if (!document.hidden) loadChart(); }, 30000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) loadChart(); });
loadChart();
