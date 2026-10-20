// One persistent audio element serves both radio streams and catalogue excerpts.
(() => {
  const audio = document.querySelector('#radio-player');
  const panel = document.querySelector('.player');
  const title = document.querySelector('#track-title');
  const artist = document.querySelector('#track-artist');
  const name = document.querySelector('#player-name');
  const toggle = document.querySelector('#player-toggle');
  const label = document.querySelector('#playback-label');
  const status = document.querySelector('#player-status');
  const mute = document.querySelector('#player-mute');
  const volume = document.querySelector('#player-volume');
  const seek = document.querySelector('#player-seek');
  const progress = document.querySelector('#player-progress');
  const elapsed = document.querySelector('#player-elapsed');
  const duration = document.querySelector('#player-duration');
  const back = document.querySelector('#player-return-radio');
  const cover = document.querySelector('.player-cover');
  let artworkKey = '';
  function showArtwork(track = {}, station = null) {
    const url = track.metadata?.cover_url || track.cover_url;
    const fallback = station?.id ? `/api/stations/${encodeURIComponent(station.id)}/artwork` : '';
    const next = `${url || ''}|${fallback}`;
    if (next === artworkKey) return;
    artworkKey = next;
    cover.replaceChildren(document.createTextNode('♫'));
    if (!url && !fallback) return;
    const img = document.createElement('img');
    img.alt = ''; img.referrerPolicy = 'no-referrer';
    let usedFallback = !url;
    img.onerror = () => {
      if (artworkKey !== next) return;
      if (!usedFallback && fallback) { usedFallback = true; img.src = fallback; }
      else { img.remove(); }
    };
    img.onload = () => { if (artworkKey === next) cover.replaceChildren(img); };
    img.src = url || fallback;
  }
  let mode = 'radio';
  let previewKey = null;
  let radio = audio.getAttribute('src') ? {url: audio.getAttribute('src'), id: panel.dataset.stationId, name: name.textContent} : null;
  let metadataVersion = 0;
  try {
    const saved = localStorage.getItem('radio-player-volume');
    if (saved !== null && Number.isFinite(Number(saved))) audio.volume = Math.max(0, Math.min(1, Number(saved)));
  } catch (_) { /* Optional preference. */ }
  function key(track, url = '') {
    return track.metadata?.track_id || `${track.provider || ''}:${track.provider_id || ''}:${track.artist || ''}:${track.title || url}`;
  }
  function time(value) {
    return Number.isFinite(value) && value >= 0 ? `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, '0')}` : '0:00';
  }
  function sync() {
    document.querySelectorAll('.station-logo').forEach(img => {
      img.onerror = () => img.remove();
      if (img.complete && !img.naturalWidth) img.remove();
    });
    const playing = !audio.paused && !audio.ended && !audio.error;
    toggle.disabled = !audio.getAttribute('src');
    toggle.setAttribute('aria-label', playing ? 'Mettre en pause' : mode === 'preview' ? 'Écouter l’extrait' : 'Écouter la radio');
    toggle.setAttribute('aria-pressed', String(playing));
    toggle.firstElementChild.textContent = playing ? 'Ⅱ' : '▶';
    panel.classList.toggle('is-playing', playing);
    panel.dataset.mode = mode;
    document.documentElement.dataset.playerMode = mode;
    seek.hidden = mode !== 'preview';
    document.querySelector('.player-live').hidden = mode === 'preview';
    back.hidden = mode !== 'preview' || !radio;
    document.querySelectorAll('.station-card').forEach(card => {
      const button = card.querySelector('.station-play');
      const selected = mode === 'radio' && button.dataset.id === panel.dataset.stationId;
      card.classList.toggle('selected', selected);
      button.setAttribute('aria-pressed', String(selected && playing));
      button.setAttribute('aria-label', `${selected && playing ? 'Mettre en pause' : 'Écouter'} ${button.dataset.name}`);
      button.firstElementChild.textContent = selected && playing ? 'Ⅱ' : '▶';
    });
    document.querySelectorAll('[data-preview-key]').forEach(button => {
      const active = mode === 'preview' && previewKey === button.dataset.previewKey && playing;
      button.textContent = active ? 'Ⅱ' : '▶';
      button.setAttribute('aria-pressed', String(active));
      button.setAttribute('aria-label', `${active ? 'Mettre en pause l’extrait de' : 'Écouter un extrait de'} ${button.dataset.previewTitle}`);
    });
  }
  function syncProgress() {
    const total = Number.isFinite(audio.duration) ? audio.duration : 0;
    const position = audio.currentTime || 0;
    progress.disabled = mode !== 'preview' || total <= 0;
    progress.value = total ? Math.min(1000, position / total * 1000) : 0;
    progress.style.setProperty('--progress', `${Number(progress.value) / 10}%`);
    elapsed.textContent = time(position);
    duration.textContent = time(total);
    progress.setAttribute('aria-valuetext', `${time(position)} sur ${time(total)}`);
  }
  function syncVolume() {
    volume.value = audio.volume;
    const silent = audio.muted || audio.volume === 0;
    mute.setAttribute('aria-label', silent ? 'Rétablir le son' : 'Couper le son');
    mute.setAttribute('aria-pressed', String(silent));
    mute.dataset.muted = String(silent);
    try { localStorage.setItem('radio-player-volume', String(audio.volume)); } catch (_) { /* Optional. */ }
  }
  async function refreshNowPlaying() {
    const current = ++metadataVersion;
    if (mode !== 'radio' || !radio?.id) return;
    try {
      const response = await window.MusicFilters.fetch(`/api/plays?station_id=${encodeURIComponent(radio.id)}&limit=1`, {cache: 'no-store'});
      if (!response.ok) return;
      const [track] = await response.json();
      if (current !== metadataVersion || mode !== 'radio') return;
      title.textContent = track?.title || 'Titre indisponible';
      artist.textContent = track?.artist || '';
      showArtwork(track || {}, radio);
    } catch (_) { /* Keep the previous radio information through a temporary failure. */ }
  }
  async function play() {
    const source = audio.src;
    try { await audio.play(); } catch (error) {
      if (error.name !== 'AbortError' && audio.src === source) {
        label.textContent = 'Lecture indisponible';
        status.textContent = mode === 'preview' ? 'Extrait indisponible. Essayez un autre titre.' : 'Flux indisponible. Essayez une autre radio.';
      }
    }
    sync();
  }
  function selectRadio(station) {
    metadataVersion++;
    mode = 'radio'; previewKey = null; radio = station;
    showArtwork({}, radio);
    panel.dataset.stationId = station.id;
    if (audio.src !== new URL(station.url, location.href).href) audio.src = station.url;
    name.textContent = station.name;
    title.textContent = 'Chargement du titre…'; artist.textContent = '';
    sync(); refreshNowPlaying(); play();
  }
  async function preview(track, url) {
    const next = key(track, url);
    if (mode === 'preview' && previewKey === next) {
      if (audio.paused || audio.ended) await play(); else audio.pause();
      return;
    }
    metadataVersion++;
    mode = 'preview'; previewKey = next;
    showArtwork(track);
    audio.src = url;
    title.textContent = track.title || 'Extrait'; artist.textContent = track.artist || '';
    name.textContent = `Extrait${track.provider || track.metadata?.provider ? ' · ' + (track.provider || track.metadata.provider) : ''}`;
    sync(); syncProgress(); await play();
  }
  document.addEventListener('click', event => {
    const button = event.target.closest('.station-play');
    if (!button) return;
    if (mode === 'radio' && panel.dataset.stationId === button.dataset.id && !audio.paused) { audio.pause(); return; }
    selectRadio({id: button.dataset.id, url: button.dataset.url, name: button.dataset.name});
  });
  back.addEventListener('click', () => { if (radio) selectRadio(radio); });
  toggle.addEventListener('click', () => { if (audio.paused) play(); else audio.pause(); });
  progress.addEventListener('input', () => {
    if (mode === 'preview' && Number.isFinite(audio.duration)) audio.currentTime = Number(progress.value) / 1000 * audio.duration;
    syncProgress();
  });
  mute.addEventListener('click', () => {
    const silent = audio.muted || audio.volume === 0;
    if (silent && audio.volume === 0) audio.volume = 0.5;
    audio.muted = !silent; syncVolume();
  });
  volume.addEventListener('input', () => {
    audio.volume = Number(volume.value); audio.muted = false;
    if (Math.abs(audio.volume - Number(volume.value)) > .01) status.textContent = 'Utilisez les boutons de volume de votre appareil.';
    syncVolume();
  });
  audio.addEventListener('play', () => { label.textContent = mode === 'radio' ? 'Connexion au direct…' : 'Chargement de l’extrait…'; sync(); });
  audio.addEventListener('playing', () => { label.textContent = mode === 'radio' ? 'En direct' : 'Extrait'; sync(); });
  audio.addEventListener('waiting', () => { label.textContent = 'Chargement…'; });
  audio.addEventListener('pause', () => { label.textContent = 'En pause'; sync(); });
  audio.addEventListener('ended', () => { label.textContent = mode === 'radio' ? 'Direct interrompu' : 'Extrait terminé'; sync(); });
  audio.addEventListener('error', () => { label.textContent = 'Lecture indisponible'; status.textContent = 'Lecture indisponible. Réessayez ou choisissez un autre titre.'; sync(); });
  ['timeupdate', 'loadedmetadata', 'durationchange', 'emptied', 'seeked'].forEach(event => audio.addEventListener(event, syncProgress));
  audio.addEventListener('volumechange', syncVolume);
  window.addEventListener('radio:pagechange', sync);
  window.addEventListener('radio:globalfilterschange', () => {
    if (mode === 'radio') {
      metadataVersion++; title.textContent = 'Titre indisponible'; artist.textContent = '';
      showArtwork({}, radio); refreshNowPlaying();
    }
  });
  window.RadioPlayer = {preview, key, sync};
  if (radio) { showArtwork({}, radio); refreshNowPlaying(); }
  window.setInterval(() => { if (!document.hidden && mode === 'radio') refreshNowPlaying(); }, 20000);
  syncVolume(); sync(); syncProgress();
})();
