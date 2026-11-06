// Keep the live stream connected while excerpts or YouTube use the shared controls.
(() => {
  const radioAudio = document.querySelector('#radio-player');
  const excerptAudio = document.createElement('audio'); excerptAudio.id = 'excerpt-player'; excerptAudio.preload = 'none'; document.body.append(excerptAudio);
  let audio = radioAudio;
  let suspended = true;
  let video = null;
  let userMuted = false;
  function quietRadio() { suspended = true; radioAudio.muted = true; }
  function stopExcerpt() { excerptAudio.pause(); excerptAudio.removeAttribute('src'); excerptAudio.load(); }
  function announceAudio() { window.dispatchEvent(new Event('radio:audioactive')); }

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
  let currentTrack = null;
  const trackButton = document.querySelector('.player-copy');
  function setCurrentTrack(track) {
    currentTrack = track?.title ? track : null;
    trackButton.disabled = !currentTrack;
    trackButton.setAttribute('aria-label', currentTrack ? window.RadioI18n.t("ui.b04c6850d768", {p0: (currentTrack.title)}) : window.RadioI18n.t("ui.5e54e23ea65e"));
  }
  trackButton.addEventListener('click', () => { if (currentTrack) window.TrackCatalog.open(currentTrack); });
  let radio = audio.getAttribute('src') ? {url: audio.getAttribute('src'), id: panel.dataset.stationId, name: name.textContent} : null;
  const listeningSettings = document.querySelector('#listening-settings');
  const resumeRadio = document.querySelector('#resume-radio-after-preview');
  const settingsToggle = document.querySelector('#listening-settings-toggle');
  try { resumeRadio.checked = localStorage.getItem('radio-resume-after-preview') === 'true'; } catch (_) { /* Optional preference. */ }
  resumeRadio.addEventListener('change', () => {
    try { localStorage.setItem('radio-resume-after-preview', String(resumeRadio.checked)); } catch (_) { /* Keep the choice during this session. */ }
  });
  settingsToggle.addEventListener('click', () => listeningSettings.showModal());
  document.querySelector('#listening-settings-close').addEventListener('click', () => listeningSettings.close());
  window.RadioDialogs.dismissOutside(listeningSettings);
  listeningSettings.addEventListener('close', () => settingsToggle.focus());
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
    document.querySelector('#resume-radio-target').textContent = radio ? window.RadioI18n.t("ui.fed8ff834c53", {p0: (radio.name)}) : window.RadioI18n.t("ui.00497f225d08");
    document.querySelectorAll('.station-logo').forEach(img => {
      img.onerror = () => img.remove();
      if (img.complete && !img.naturalWidth) img.remove();
    });
    const playing = mode === 'video' ? Boolean(video?.playing()) : !audio.paused && !audio.ended && !audio.error && (mode !== 'radio' || !suspended);
    toggle.disabled = mode === 'video' ? !video : !audio.getAttribute('src');
    toggle.setAttribute('aria-label', playing ? window.RadioI18n.t("ui.42dd586c06d6") : mode === 'preview' ? window.RadioI18n.t("ui.ccc216fa2e6b") : window.RadioI18n.t("ui.ec0a4373089c"));
    toggle.setAttribute('aria-pressed', String(playing));
    toggle.querySelector('path').setAttribute('d', playing ? 'M6 5h4v14H6zM14 5h4v14h-4z' : 'M8 5v14l11-7z');
    panel.classList.toggle('is-playing', playing);
    panel.dataset.mode = mode;
    document.documentElement.dataset.playerMode = mode;
    seek.hidden = mode === 'radio';
    document.querySelector('.player-live').hidden = mode !== 'radio';
    back.hidden = mode === 'radio' || !radio;
    document.querySelectorAll('.station-card').forEach(card => {
      const button = card.querySelector('.station-play');
      const selected = mode === 'radio' && button.dataset.id === panel.dataset.stationId;
      card.classList.toggle('selected', selected);
      button.setAttribute('aria-pressed', String(selected && playing));
      button.setAttribute('aria-label', `${selected && playing ? window.RadioI18n.t("ui.42dd586c06d6") : window.RadioI18n.t("ui.b3266f2ad37b")} ${button.dataset.name}`);
      button.firstElementChild.textContent = selected && playing ? 'Ⅱ' : '▶';
    });
    document.querySelectorAll('[data-preview-key]').forEach(button => {
      const active = mode === 'preview' && previewKey === button.dataset.previewKey && playing;
      button.textContent = active ? 'Ⅱ' : '▶';
      button.setAttribute('aria-pressed', String(active));
      button.setAttribute('aria-label', `${active ? window.RadioI18n.t("ui.d3d23e41d7b4") : window.RadioI18n.t("ui.d15b0fe2b67c")} ${button.dataset.previewTitle}`);
    });
  }
  function syncProgress() {
    const total = mode === 'video' ? video?.duration() || 0 : Number.isFinite(audio.duration) ? audio.duration : 0;
    const position = mode === 'video' ? video?.position() || 0 : audio.currentTime || 0;
    progress.disabled = mode === 'radio' || total <= 0;
    progress.value = total ? Math.min(1000, position / total * 1000) : 0;
    progress.style.setProperty('--progress', `${Number(progress.value) / 10}%`);
    elapsed.textContent = time(position);
    duration.textContent = time(total);
    progress.setAttribute('aria-valuetext', window.RadioI18n.t("ui.c35a60b008ba", {p0: (time(position)), p1: (time(total))}));
  }
  function syncVolume() {
    volume.value = mode === 'video' ? video?.volume() ?? Number(volume.value) : audio.volume;
    const silent = userMuted || Number(volume.value) === 0;
    mute.setAttribute('aria-label', silent ? window.RadioI18n.t("ui.d52a0aa00ed0") : window.RadioI18n.t("ui.4bda29ddf960"));
    mute.setAttribute('aria-pressed', String(silent));
    mute.dataset.muted = String(silent);
    try { localStorage.setItem('radio-player-volume', String(volume.value)); } catch (_) { /* Optional. */ }
  }
  async function refreshNowPlaying() {
    const current = ++metadataVersion;
    if (mode !== 'radio' || !radio?.id) return;
    try {
      const response = await window.MusicFilters.fetch(`/api/plays?station_id=${encodeURIComponent(radio.id)}&limit=1`, {cache: 'no-store'});
      if (!response.ok) return;
      const [track] = await response.json();
      if (current !== metadataVersion || mode !== 'radio') return;
      title.textContent = track?.title || window.RadioI18n.t("ui.a031d88118a6");
      setCurrentTrack(track);
      artist.textContent = track?.artist || '';
      showArtwork(track || {}, radio);
    } catch (_) { /* Keep the previous radio information through a temporary failure. */ }
  }
  async function play() {
    if (mode === 'video') { video?.play(); return; }
    if (mode === 'radio') { suspended = false; audio.muted = userMuted; label.textContent = audio.readyState >= 3 ? window.RadioI18n.t("ui.235df0b17a9e") : window.RadioI18n.t("ui.910919b77ec7"); }
    announceAudio();
    const source = audio.src;
    try { await audio.play(); } catch (error) {
      if (error.name !== 'AbortError' && audio.src === source) {
        label.textContent = window.RadioI18n.t("ui.ce6a03b5762e");
        status.textContent = mode === 'preview' ? window.RadioI18n.t("ui.9f4b7303e42b") : window.RadioI18n.t("ui.080d5f7d0c0b");
      }
    }
    sync();
  }
  function selectRadio(station) {
    metadataVersion++;
    announceAudio(); video = null; stopExcerpt(); audio = radioAudio;
    const changed = radio?.url !== station.url;
    if (changed) { radioAudio.pause(); radioAudio.removeAttribute('src'); radioAudio.load(); }
    mode = 'radio'; previewKey = null; radio = station; suspended = false; radioAudio.muted = userMuted;

    setCurrentTrack(null);
    showArtwork({}, radio);
    panel.dataset.stationId = station.id;
    if (audio.src !== new URL(station.url, location.href).href) audio.src = station.url;
    name.textContent = station.name;
    title.textContent = window.RadioI18n.t("ui.b81e67f27242"); artist.textContent = '';
    sync(); refreshNowPlaying(); play();
  }
  async function preview(track, url) {
    const next = key(track, url);
    if (mode === 'preview' && previewKey === next) {
      if (audio.paused || audio.ended) await play(); else pause();
      return;
    }
    metadataVersion++;
    announceAudio(); video = null; quietRadio(); audio = excerptAudio;
    audio.volume = Number(volume.value); audio.muted = userMuted;
    mode = 'preview'; previewKey = next;
    setCurrentTrack(track);
    showArtwork(track);
    audio.src = url;
    title.textContent = track.title || window.RadioI18n.t("ui.7224fc1ace77"); artist.textContent = track.artist || '';
    name.textContent = window.RadioI18n.t("ui.0f8745e4abeb", {p0: (track.provider || track.metadata?.provider ? ' · ' + (track.provider || track.metadata.provider) : '')});
    sync(); syncProgress(); await play();
  }
  document.addEventListener('click', event => {
    const button = event.target.closest('.station-play');
    if (!button) return;
    if (mode === 'radio' && panel.dataset.stationId === button.dataset.id && !audio.paused && !suspended) { pause(); return; }
    selectRadio({id: button.dataset.id, url: button.dataset.url, name: button.dataset.name});
  });
  back.addEventListener('click', () => { if (radio) selectRadio(radio); });
  toggle.addEventListener('click', () => {
    const playing = mode === 'video' ? video?.playing() : !audio.paused && !audio.ended && (mode !== 'radio' || !suspended);
    if (playing) pause(); else play();
  });
  progress.addEventListener('input', () => {
    if (mode === 'video') video?.seek(Number(progress.value) / 1000 * video.duration());
    else if (mode === 'preview' && Number.isFinite(audio.duration)) audio.currentTime = Number(progress.value) / 1000 * audio.duration;
    syncProgress();
  });
  function applyVolume() {
    const value = Number(volume.value);
    excerptAudio.volume = value; excerptAudio.muted = userMuted;
    radioAudio.volume = value; radioAudio.muted = userMuted || suspended || mode !== 'radio';
    if (mode === 'video') video?.setVolume(value, userMuted);
    syncVolume();
  }
  mute.addEventListener('click', () => {
    userMuted = !(userMuted || Number(volume.value) === 0);
    if (!userMuted && Number(volume.value) === 0) volume.value = .5;
    applyVolume();
  });
  volume.addEventListener('input', () => { userMuted = false; applyVolume(); });
  for (const sourceAudio of [radioAudio, excerptAudio]) {
    const listen = (event, callback) => sourceAudio.addEventListener(event, () => { if (sourceAudio === audio && mode !== 'video') callback(); });
  listen('play', () => { label.textContent = mode === 'radio' ? window.RadioI18n.t("ui.910919b77ec7") : window.RadioI18n.t("ui.a90bd5662fcc"); sync(); });
  listen('playing', () => { label.textContent = mode === 'radio' ? window.RadioI18n.t("ui.235df0b17a9e") : window.RadioI18n.t("ui.7224fc1ace77"); sync(); });
  listen('waiting', () => { label.textContent = window.RadioI18n.t("ui.66474911d953"); });
  listen('pause', () => { label.textContent = window.RadioI18n.t("ui.239c064e0d40"); sync(); });
  listen('ended', () => {
    if (mode === 'preview' && resumeRadio.checked && radio) { selectRadio(radio); return; }
    label.textContent = mode === 'radio' ? window.RadioI18n.t("ui.3a3b98e30872") : window.RadioI18n.t("ui.a4d4a1c61e92"); sync();
  });
  listen('error', () => { label.textContent = window.RadioI18n.t("ui.ce6a03b5762e"); status.textContent = window.RadioI18n.t("ui.7b6e9406532f"); sync(); });
  ['timeupdate', 'loadedmetadata', 'durationchange', 'emptied', 'seeked'].forEach(event => listen(event, syncProgress));
  }

  window.addEventListener('radio:pagechange', sync);
  window.addEventListener('radio:globalfilterschange', () => {
    if (mode === 'radio' && radio) {
      metadataVersion++; title.textContent = window.RadioI18n.t("ui.a031d88118a6"); artist.textContent = '';
      setCurrentTrack(null);
      showArtwork({}, radio); refreshNowPlaying();
    }
  });
  function pause() {
    if (mode === 'video') video?.pause();
    else if (mode === 'radio') { quietRadio(); label.textContent = window.RadioI18n.t("ui.239c064e0d40"); }
    else audio.pause();
    sync();
  }
  function startVideo(adapter, track) {
    quietRadio(); excerptAudio.pause(); video = adapter; metadataVersion++;
    mode = 'video'; previewKey = null; setCurrentTrack(track); showArtwork(track);
    title.textContent = track.title; artist.textContent = track.artist || ''; name.textContent = window.RadioI18n.t("ui.fb7accfff8c6");
    video.setVolume(Number(volume.value), userMuted);
    sync(); syncProgress();
  }
  function videoStopped() {
    if (mode !== 'video') return;
    video = null; mode = 'preview'; audio = excerptAudio; stopExcerpt();
    label.textContent = window.RadioI18n.t("ui.adf3f3af5692"); sync(); syncProgress();
  }
  window.RadioPlayer = {preview, key, sync, pause, startVideo, videoStopped, videoState() {
    if (mode === 'video') { userMuted = video?.muted?.() ?? userMuted; syncVolume(); label.textContent = video?.playing() ? window.RadioI18n.t("ui.fb7accfff8c6") : window.RadioI18n.t("ui.239c064e0d40"); sync(); syncProgress(); }
  }};
  window.setInterval(() => { if (mode === 'video') { userMuted = video?.muted?.() ?? userMuted; syncVolume(); syncProgress(); } }, 500);

  radioAudio.muted = true;
  if (radio) { showArtwork({}, radio); refreshNowPlaying(); }
  window.setInterval(() => { if (!document.hidden && mode === 'radio') refreshNowPlaying(); }, 20000);
  syncVolume(); sync(); syncProgress();
})();
