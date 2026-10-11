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

const dialog = document.querySelector('#add-dialog');
document.querySelector('#show-add').addEventListener('click', () => dialog.showModal());
dialog.querySelector('.close').addEventListener('click', () => dialog.close());

document.querySelector('#add-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const status = form.querySelector('.form-status');
  const data = new FormData(form);
  status.textContent = 'Ajout en cours…';
  const response = await fetch('/api/stations', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      name: data.get('name'),
      stream_url: data.get('stream_url'),
      enabled: data.get('enabled') === 'on',
    }),
  });
  if (!response.ok) {
    const body = await response.json();
    status.textContent = body.detail || "Impossible d'ajouter cette radio.";
    return;
  }
  status.textContent = 'Radio ajoutée. Le collecteur va la prendre en charge.';
  window.setTimeout(() => window.location.reload(), 700);
});
