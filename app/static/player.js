(() => {
  const player = document.querySelector('#radio-player');
  const playerName = document.querySelector('#player-name');
  const panel = document.querySelector('.player');
  const trackTitle = document.querySelector('#track-title');
  const trackArtist = document.querySelector('#track-artist');
  const toggle = document.querySelector('#player-toggle');
  const playback = document.querySelector('#playback-label');
  const status = document.querySelector('#player-status');
  const mute = document.querySelector('#player-mute');
  const volume = document.querySelector('#player-volume');
  let nowPlayingVersion = 0;
  try {
    const saved = localStorage.getItem('radio-player-volume');
    if (saved !== null && Number.isFinite(Number(saved))) player.volume = Math.max(0, Math.min(1, Number(saved)));
  } catch (_) { /* Optional preference. */ }

  function setNowPlaying(title, artist = '') {
    trackTitle.textContent = title;
    trackArtist.textContent = artist;
  }
  function syncControls() {
    const playing = !player.paused && !player.ended;
    toggle.disabled = !player.getAttribute('src');
    toggle.setAttribute('aria-label', playing ? 'Mettre la radio en pause' : 'Écouter la radio');
    toggle.setAttribute('aria-pressed', String(playing));
    toggle.firstElementChild.textContent = playing ? 'Ⅱ' : '▶';
    panel.classList.toggle('is-playing', playing);
    document.querySelectorAll('.station-card').forEach(card => {
      const button = card.querySelector('.station-play');
      const selected = button.dataset.id === panel.dataset.stationId;
      card.classList.toggle('selected', selected);
      button.setAttribute('aria-pressed', String(selected && playing));
      button.setAttribute('aria-label', `${selected && playing ? 'Mettre en pause' : 'Écouter'} ${button.dataset.name}`);
      button.firstElementChild.textContent = selected && playing ? 'Ⅱ' : '▶';
    });
  }
  function syncVolume() {
    volume.value = player.volume;
    const silent = player.muted || player.volume === 0;
    mute.setAttribute('aria-label', silent ? 'Rétablir le son' : 'Couper le son');
    mute.setAttribute('aria-pressed', String(silent));
    mute.dataset.muted = String(silent);
    try { localStorage.setItem('radio-player-volume', String(player.volume)); } catch (_) { /* Optional. */ }
  }
  async function refreshNowPlaying(stationId) {
    const current = ++nowPlayingVersion;
    if (!stationId) { setNowPlaying('Sélectionnez une radio'); return; }
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
  async function play() {
    const source = player.src;
    try { await player.play(); } catch (error) {
      if (error.name !== 'AbortError' && player.src === source) {
        playback.textContent = 'Lecture indisponible';
        status.textContent = 'Lecture indisponible. Réessayez ou sélectionnez une autre radio.';
      }
    }
    syncControls();
  }
  document.addEventListener('click', event => {
    const button = event.target.closest('.station-play');
    if (!button) return;
    if (panel.dataset.stationId === button.dataset.id && !player.paused) { player.pause(); return; }
    if (player.src !== button.dataset.url) player.src = button.dataset.url;
    playerName.textContent = button.dataset.name;
    panel.dataset.stationId = button.dataset.id;
    setNowPlaying('Chargement du titre…');
    refreshNowPlaying(button.dataset.id);
    play();
  });
  toggle.addEventListener('click', () => { if (player.paused) play(); else player.pause(); });
  mute.addEventListener('click', () => {
    const silent = player.muted || player.volume === 0;
    if (silent && player.volume === 0) player.volume = 0.5;
    player.muted = !silent;
    syncVolume();
  });
  volume.addEventListener('input', () => {
    player.volume = Number(volume.value);
    player.muted = false;
    if (Math.abs(player.volume - Number(volume.value)) > 0.01) status.textContent = 'Utilisez les boutons de volume de votre appareil.';
    syncVolume();
  });
  player.addEventListener('play', () => {
    document.querySelectorAll('audio').forEach(other => { if (other !== player) other.pause(); });
    playback.textContent = 'Connexion au direct…';
    syncControls();
  });
  player.addEventListener('playing', () => { playback.textContent = 'En direct'; syncControls(); });
  player.addEventListener('waiting', () => { playback.textContent = 'Connexion au direct…'; });
  player.addEventListener('pause', () => { playback.textContent = 'En pause'; syncControls(); });
  player.addEventListener('ended', () => { playback.textContent = 'Direct interrompu'; syncControls(); });
  player.addEventListener('volumechange', syncVolume);
  player.addEventListener('error', () => {
    playback.textContent = 'Flux indisponible';
    status.textContent = 'Flux indisponible. Essayez une autre radio.';
    syncControls();
  });
  window.addEventListener('radio:pagechange', syncControls);
  refreshNowPlaying(panel.dataset.stationId);
  window.setInterval(() => { if (!document.hidden) refreshNowPlaying(panel.dataset.stationId); }, 20000);
  syncVolume();
  syncControls();
})();
