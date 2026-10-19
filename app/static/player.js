const player = document.querySelector('#radio-player');
const playerName = document.querySelector('#player-name');
const playerPanel = document.querySelector('.player');
const trackTitle = document.querySelector('#track-title');
const trackArtist = document.querySelector('#track-artist');
let nowPlayingVersion = 0;

function setNowPlaying(title, artist = '') {
  trackTitle.textContent = title;
  trackArtist.textContent = artist;
}

async function refreshNowPlaying(stationId) {
  const current = ++nowPlayingVersion;
  if (!stationId) {
    setNowPlaying('Sélectionnez une radio');
    return;
  }
  try {
    const response = await fetch(`/api/plays?station_id=${encodeURIComponent(stationId)}&limit=1`, {cache: 'no-store'});
    if (!response.ok) return;
    const [track] = await response.json();
    if (current !== nowPlayingVersion) return;
    setNowPlaying(track ? track.title : 'Titre indisponible', track?.artist || '');
  } catch (_) {
    if (current === nowPlayingVersion) setNowPlaying('Titre indisponible');
  }
}

document.querySelectorAll('.station-play').forEach((button) => {
  button.addEventListener('click', async () => {
    if (player.src !== button.dataset.url) player.src = button.dataset.url;
    playerName.textContent = button.dataset.name;
    playerPanel.dataset.stationId = button.dataset.id;
    document.querySelectorAll('.station-card').forEach(card => {
      card.classList.toggle('selected', card.querySelector('.station-play').dataset.id === button.dataset.id);
    });
    setNowPlaying('Chargement du titre…');
    refreshNowPlaying(button.dataset.id);
    try { await player.play(); } catch (error) {
      if (error.name !== 'AbortError') playerName.textContent = `${button.dataset.name} · Lecture indisponible`;
    }
  });
});

refreshNowPlaying(playerPanel.dataset.stationId);
window.setInterval(() => refreshNowPlaying(playerPanel.dataset.stationId), 20000);

player.addEventListener('error', () => {
  playerName.textContent = 'Flux indisponible · Essayez une autre radio';
  document.querySelector('#player-status').textContent = playerName.textContent;
});

