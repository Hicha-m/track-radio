const player = document.querySelector('#radio-player');
const playerName = document.querySelector('#player-name');
const playerPanel = document.querySelector('.player');
const trackTitle = document.querySelector('#track-title');
const trackArtist = document.querySelector('#track-artist');

function setNowPlaying(title, artist = '') {
  trackTitle.textContent = title;
  trackArtist.textContent = artist;
}

async function refreshNowPlaying(stationId) {
  if (!stationId) {
    setNowPlaying('Sélectionnez une radio');
    return;
  }
  try {
    const response = await fetch(`/api/plays?station_id=${encodeURIComponent(stationId)}&limit=1`, {cache: 'no-store'});
    if (!response.ok) return;
    const [track] = await response.json();
    setNowPlaying(track ? track.title : 'Titre indisponible', track?.artist || '');
  } catch (_) {
    setNowPlaying('Titre indisponible');
  }
}

document.querySelectorAll('.station-play').forEach((button) => {
  button.addEventListener('click', async () => {
    if (player.src !== button.dataset.url) player.src = button.dataset.url;
    playerName.textContent = button.dataset.name;
    playerPanel.dataset.stationId = button.dataset.id;
    refreshNowPlaying(button.dataset.id);
    try { await player.play(); } catch (_) { /* Le navigateur affiche l'erreur. */ }
  });
});

refreshNowPlaying(playerPanel.dataset.stationId);
window.setInterval(() => refreshNowPlaying(playerPanel.dataset.stationId), 20000);

const form = document.querySelector('#explorer-form');
const results = document.querySelector('#results');
const statusLine = document.querySelector('#result-status');
const previous = document.querySelector('#previous-page');
const next = document.querySelector('#next-page');
const pageLabel = document.querySelector('#page-label');
const live = document.querySelector('#live');
let page = 1;
let snapshot = null;
let request = null;
let version = 0;
let debounce = null;
let resettingFromStation = false;
let lastInput = 0;
let shownPage = 1;

function element(tag, text = '', className = '') {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function action(text, handler, title = '') {
  const button = element('button', text, 'cell-link');
  button.type = 'button';
  button.title = title;
  button.addEventListener('click', handler);
  return button;
}

function dateValue(value) {
  if (!value) return null;
  return new Date(/(Z|[+-]\d{2}:\d{2})$/.test(value) ? value : `${value}Z`);
}

function formatDate(value) {
  const date = dateValue(value);
  return date && !Number.isNaN(date.getTime()) ? date.toLocaleString('fr-FR') : '—';
}

function paramsFromForm() {
  const params = new URLSearchParams();
  for (const [key, value] of new FormData(form)) {
    if (!String(value).trim()) continue;
    if (['since', 'until', 'seen_since', 'seen_until'].includes(key)) {
      params.set(key, new Date(value).toISOString());
    } else {
      params.set(key, value === 'on' ? 'true' : value);
    }
  }
  return params;
}

function setView(view) {
  form.elements.view.value = view;
  document.querySelectorAll('[data-view]').forEach((button) => {
    const active = button.dataset.view === view;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
  for (const option of form.elements.sort.options) {
    option.disabled = Boolean(option.dataset.only && option.dataset.only !== view);
  }
  if (!form.elements.sort.value || form.elements.sort.selectedOptions[0]?.disabled) form.elements.sort.value = 'newest';
  form.elements.min_plays.disabled = view !== 'tracks';
}

function selectArtist(artist) {
  form.elements.q.value = '';
  form.elements.artist.value = artist || '';
  form.elements.title.value = '';
  form.elements.artist_status.value = artist ? 'known' : 'missing';
  form.elements.exact.checked = true;
  setView('tracks');
  form.elements.sort.value = 'frequency';
  load(true);
}

function selectTrack(item) {
  form.elements.q.value = '';
  form.elements.title.value = item.title;
  form.elements.artist.value = item.artist || '';
  form.elements.artist_status.value = item.artist ? 'any' : 'missing';
  form.elements.exact.checked = true;
  setView('plays');
  form.elements.sort.value = 'newest';
  load(true);
}

function render(data) {
  const tracks = form.elements.view.value === 'tracks';
  const table = document.createElement('table');
  const header = document.createElement('tr');
  const columns = tracks
    ? [['Musique', 'title'], ['Artiste', 'artist'], ['Passages', 'frequency'], ['Stations', null], ['Première diffusion', 'oldest'], ['Dernière diffusion', 'newest']]
    : [['Diffusion', 'newest'], ['Musique', 'title'], ['Artiste', 'artist'], ['Station', 'station'], ['Source / détails', null]];
  columns.forEach(([label, sort]) => {
    const th = element('th');
    th.scope = 'col';
    if (sort) th.append(action(label, () => {
      form.elements.sort.value = sort;
      load(true);
    }, 'Trier les résultats'));
    else th.textContent = label;
    header.append(th);
  });
  const head = document.createElement('thead');
  head.append(header);
  table.append(head);
  const body = document.createElement('tbody');
  for (const item of data.items) {
    const row = document.createElement('tr');
    const cell = (content) => {
      const td = document.createElement('td');
      td.append(typeof content === 'string' ? document.createTextNode(content) : content);
      row.append(td);
    };
    if (!tracks) cell(formatDate(item.detected_at));
    cell(action(item.title, () => selectTrack(item), 'Voir les passages de cette musique'));
    cell(action(item.artist || 'Artiste inconnu', () => selectArtist(item.artist), 'Voir les musiques de cet artiste'));
    if (tracks) {
      cell(Number(item.plays).toLocaleString('fr-FR'));
      cell(String(item.stations));
      cell(formatDate(item.first_at));
      cell(formatDate(item.last_at));
    } else {
      cell(action(item.station_name, () => {
        form.elements.station_id.value = String(item.station_id);
        load(true);
      }, 'Restreindre les résultats à cette station'));
      const details = document.createElement('details');
      details.append(element('summary', item.source.toUpperCase()));
      details.append(element('p', `Passage #${item.id} · Station #${item.station_id}`));
      details.append(element('p', `Dernière observation : ${formatDate(item.last_seen_at)}`));
      details.append(element('pre', item.raw_metadata));
      cell(details);
    }
    body.append(row);
  }
  table.append(body);
  results.replaceChildren(data.items.length ? table : element('p', 'Aucun résultat pour ces filtres.', 'empty'));
  shownPage = data.page;
  previous.disabled = data.page <= 1;
  next.disabled = !data.has_more;
  pageLabel.textContent = `Page ${data.page}`;
  const first = (data.page - 1) * data.page_size + 1;
  const last = first + data.items.length - 1;
  const noun = tracks ? 'Musiques' : 'Passages';
  statusLine.textContent = data.items.length
    ? `${noun} ${first}–${last}${data.has_more ? ' · suite disponible' : ''} · actualisé à ${new Date().toLocaleTimeString('fr-FR')}`
    : 'Aucun résultat. Essayez de retirer un filtre.';
}

async function load(reset = false) {
  window.clearTimeout(debounce);
  if (!form.reportValidity()) return;
  if (reset) { page = 1; snapshot = null; }
  if (request) request.abort();
  request = new AbortController();
  const current = ++version;
  const params = paramsFromForm();
  const shared = params.toString();
  history.replaceState(null, '', `${location.pathname}${shared ? `?${shared}` : ''}`);
  const active = [...params].filter(([key, value]) => !['view', 'sort', 'page_size', 'artist_status', 'min_plays'].includes(key) && value);
  if (form.elements.artist_status.value !== 'any') active.push(['artist_status', 'active']);
  if (!form.elements.min_plays.disabled && Number(form.elements.min_plays.value) > 1) active.push(['min_plays', 'active']);
  document.querySelector('#active-filters').textContent = active.length ? `${active.length} filtre(s) actif(s)` : 'Tout l’historique actif';
  params.set('page', page);
  if (snapshot !== null && page > 1) params.set('snapshot_id', snapshot);
  results.setAttribute('aria-busy', 'true');
  statusLine.textContent = 'Recherche en cours…';
  try {
    const response = await fetch(`/api/explore?${params}`, {signal: request.signal, cache: 'no-store'});
    const data = await response.json();
    if (current !== version) return;
    if (!response.ok) {
      const detail = typeof data.detail === 'string' ? data.detail : data.detail?.[0]?.msg;
      throw new Error(detail || 'Recherche impossible. Réessayez.');
    }
    snapshot = data.snapshot_id;
    render(data);
  } catch (error) {
    if (error.name !== 'AbortError' && current === version) {
      page = shownPage;
      statusLine.textContent = error.message;
    }
  } finally {
    if (current === version) results.setAttribute('aria-busy', 'false');
  }
}

for (const [key, value] of new URLSearchParams(location.search)) {
  const input = form.elements.namedItem(key);
  if (!input) continue;
  if (input.type === 'checkbox') input.checked = value === 'true';
  else if (input.type === 'datetime-local') {
    const date = dateValue(value);
    if (date && !Number.isNaN(date.getTime())) {
      const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
      input.value = local.toISOString().slice(0, 16);
    }
  } else input.value = value;
}
if (!['plays', 'tracks'].includes(form.elements.view.value)) form.elements.view.value = 'plays';
if (!['25', '50', '100'].includes(form.elements.page_size.value)) form.elements.page_size.value = '50';
setView(form.elements.view.value);
form.addEventListener('submit', (event) => { event.preventDefault(); load(true); });
form.addEventListener('input', (event) => {
  lastInput = Date.now();
  window.clearTimeout(debounce);
  if (['text', 'search'].includes(event.target.type)) debounce = window.setTimeout(() => load(true), 350);
});
form.addEventListener('change', () => load(true));
form.addEventListener('reset', () => {
  if (!resettingFromStation) window.setTimeout(() => { setView('plays'); load(true); }, 0);
});
document.querySelectorAll('[data-view]').forEach((button) => button.addEventListener('click', () => {
  setView(button.dataset.view);
  load(true);
}));
document.querySelectorAll('.explore-station').forEach((button) => button.addEventListener('click', () => {
  resettingFromStation = true;
  form.reset();
  resettingFromStation = false;
  form.elements.station_id.value = button.dataset.id;
  form.elements.page_size.value = '50';
  form.elements.artist_status.value = 'any';
  setView('tracks');
  form.elements.sort.value = 'frequency';
  document.querySelector('#explorer-title').scrollIntoView({behavior: 'smooth', block: 'start'});
  load(true);
}));
previous.addEventListener('click', () => { if (page > 1) { page -= 1; load(); } });
next.addEventListener('click', () => { page += 1; load(); });
document.querySelector('#refresh').addEventListener('click', () => load(true));
window.setInterval(() => {
  if (live.checked && !document.hidden && page === 1 && Date.now() - lastInput > 1000 && !results.contains(document.activeElement)
      && !results.querySelector('details[open]') && !window.getSelection()?.toString()) load();
}, 10000);
document.addEventListener('visibilitychange', () => { if (!document.hidden && live.checked && page === 1) load(); });
load();
