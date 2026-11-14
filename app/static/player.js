// Keep the live stream connected while excerpts or YouTube use the shared controls.
(() => {
  const radioAudio = document.querySelector('#radio-player');
  let excerptAudio = document.createElement('audio'); excerptAudio.id = 'excerpt-player'; excerptAudio.preload = 'none'; document.body.append(excerptAudio);
  let spareAudio = document.createElement('audio'); spareAudio.id = 'transition-player'; spareAudio.preload = 'none'; document.body.append(spareAudio);
  let fade = null, fadeTimer = null;
  let audio = radioAudio;
  let suspended = true;
  let video = null;
  let sequence = null;
  let previewVersion = 0;
  let previewRequest = null;
  function cancelSequence() {
    cancelFade(); previewVersion++; previewRequest?.abort(); previewRequest = null; sequence = null;
  }
  function previewUrl(track) {
    if (!track.metadata?.preview_url || !track.metadata.track_id) return track.preview_url;
    const params = window.MusicFilters.apply(new URLSearchParams());
    return `/api/catalog/${encodeURIComponent(track.metadata.track_id)}/preview${params.size ? '?' + params : ''}`;
  }
  function playingRadio() {
    return mode === 'radio' && radio && !suspended && !radioAudio.paused && !radioAudio.ended && !radioAudio.error ? {...radio} : null;
  }
  function makeSequence(track, url, context, returnRadio) {
    const items = [], seen = new Set();
    for (const item of context.slice(0, 100)) {
      const source = previewUrl(item), id = key(item, source);
      if (seen.has(id)) continue;
      seen.add(id); items.push({track: {...item}, url: source, key: id});
    }
    const id = key(track, url);
    let cursor = items.findIndex(item => item.key === id);
    if (cursor < 0) { items.splice(0, items.length, {track, url, key: id}); cursor = 0; }
    else items[cursor] = {track, url, key: id};
    return {items, cursor, currentIndex: cursor, returnRadio, attempted: new Set([id]), active: true, pending: false};
  }
  let userMuted = false;
  function quietRadio() { suspended = true; cancelRadioRecovery(); radioAudio.muted = true; }
  function stopExcerpt() { cancelFade(); for (const item of [excerptAudio, spareAudio]) { item.pause(); item.removeAttribute('src'); item.load(); } }
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
  const expandedMode = document.querySelector('#now-playing-mode');
  const expandedArt = document.querySelector('.now-playing-art');
  const expandedTitle = document.querySelector('#now-playing-title');
  const expandedArtist = document.querySelector('#now-playing-artist');
  const expandedSource = document.querySelector('#now-playing-source');
  const expandedStatus = document.querySelector('#now-playing-status');
  const expandedToggle = document.querySelector('#now-playing-toggle');
  const expandedPrevious = document.querySelector('#now-playing-previous');
  const expandedNext = document.querySelector('#now-playing-next');
  const expandedOpen = document.querySelector('#player-mode-toggle');
  let artworkKey = '';
  function expandedArtwork(image = null) {
    if (!expandedArt) return;
    if (!image) { expandedArt.replaceChildren(document.createTextNode('♫')); return; }
    const copy = image.cloneNode(); copy.alt = ''; copy.removeAttribute('id'); expandedArt.replaceChildren(copy);
  }
  function showArtwork(track = {}, station = null) {
    const url = track.metadata?.cover_url || track.cover_url;
    const fallback = station?.id ? `/api/stations/${encodeURIComponent(station.id)}/artwork` : '';
    const next = `${url || ''}|${fallback}`;
    if (next === artworkKey) return;
    artworkKey = next;
    if (!url && !fallback) { cover.replaceChildren(document.createTextNode('♫')); expandedArtwork(); return; }
    const img = document.createElement('img');
    img.alt = ''; img.referrerPolicy = 'no-referrer'; img.decoding = 'async';
    let usedFallback = !url;
    img.onerror = () => {
      if (artworkKey !== next) return;
      if (!usedFallback && fallback) { usedFallback = true; img.src = fallback; }
      else { cover.replaceChildren(document.createTextNode('♫')); expandedArtwork(); }
    };
    img.onload = () => {
      if (artworkKey !== next) return;
      cover.replaceChildren(img);
      expandedArtwork(img);
    };
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
  function updateExpandedMode(playing = false) {
    if (!expandedMode) return;
    expandedTitle.textContent = title.textContent;
    expandedArtist.textContent = artist.textContent;
    expandedSource.textContent = mode === 'radio'
      ? window.RadioI18n.t('player.radio_source', {station: name.textContent})
      : mode === 'video'
        ? currentTrack?.station_id || currentTrack?.station_name
          ? window.RadioI18n.t('player.youtube_source', {station: currentTrack.station_name || name.textContent})
          : window.RadioI18n.t('youtube.full_song')
        : window.RadioI18n.t('player.track_source');
    expandedStatus.textContent = label.textContent;
    expandedToggle.disabled = toggle.disabled;
    expandedToggle.setAttribute('aria-label', toggle.getAttribute('aria-label'));
    expandedToggle.setAttribute('aria-pressed', String(playing));
    expandedToggle.querySelector('path').setAttribute('d', playing ? 'M6 5h4v14H6zM14 5h4v14h-4z' : 'M8 5v14l11-7z');
    const navigation = window.RadioPlaylists?.navigationState?.() || {};
    expandedPrevious.disabled = !navigation.previous;
    expandedNext.disabled = !navigation.next;
    const image = cover.querySelector('img');
    if (image && !expandedArt.querySelector('img')) expandedArtwork(image);
  }
  function openMode() {
    updateExpandedMode(!toggle.disabled && toggle.getAttribute('aria-pressed') === 'true');
    expandedMode.showModal();
  }
  expandedOpen.addEventListener('click', openMode);
  document.querySelector('#now-playing-close').addEventListener('click', () => expandedMode.close());
  expandedToggle.addEventListener('click', () => toggle.click());
  expandedPrevious.addEventListener('click', () => window.RadioPlaylists?.previous());
  expandedNext.addEventListener('click', () => window.RadioPlaylists?.next());
  window.RadioDialogs.dismissOutside(expandedMode);
  trackButton.addEventListener('click', () => { if (currentTrack) window.TrackCatalog.open(currentTrack, {previewQueue: sequence?.items.map(item => item.track) || []}); });
  let radio = audio.getAttribute('src') ? {url: audio.getAttribute('src'), id: panel.dataset.stationId, name: name.textContent} : null;
  let radioRetryTimer = null, radioWatchdog = null, radioStableTimer = null;
  let radioRetryCount = 0;
  let radioReloadRequired = false;
  function cancelRadioRecovery(reset = false) {
    clearTimeout(radioRetryTimer); clearTimeout(radioWatchdog); clearTimeout(radioStableTimer);
    radioRetryTimer = radioWatchdog = radioStableTimer = null;
    if (reset) radioRetryCount = 0;
  }
  function radioActive() { return mode === 'radio' && radio && !suspended; }
  function watchRadio() {
    clearTimeout(radioStableTimer); radioStableTimer = null;
    if (radioActive() && radioRetryTimer === null && radioWatchdog === null) radioWatchdog = setTimeout(retryRadio, 12000);
  }
  function retryRadio() {
    if (!radioActive()) return;
    if (radioRetryTimer !== null) {
      status.textContent = window.RadioI18n.t('radio.reconnecting');
      return;
    }
    clearTimeout(radioWatchdog); radioWatchdog = null;
    clearTimeout(radioStableTimer); radioStableTimer = null;
    if (radioRetryCount >= 3) {
      radioReloadRequired = true;
      suspended = true; radioAudio.pause();
      label.textContent = window.RadioI18n.t('ui.ce6a03b5762e');
      status.textContent = window.RadioI18n.t('radio.retry_stopped'); sync();
      return;
    }
    const source = radio.url;
    status.textContent = window.RadioI18n.t('radio.reconnecting');
    radioRetryTimer = setTimeout(() => {
      radioRetryTimer = null;
      if (!radioActive() || radio.url !== source) return;
      radioAudio.load(); play({recovery: true});
    }, 1000 * 2 ** radioRetryCount++);
  }
  const autoplay = document.querySelector('#autoplay-tracks');
  const crossfade = document.querySelector('#crossfade-tracks');
  const fadeSeconds = document.querySelector('#crossfade-seconds');
  const showVideo = document.querySelector('#show-youtube-video');
  try {
    autoplay.checked = localStorage.getItem('radio-autoplay') !== 'false';
    crossfade.checked = localStorage.getItem('radio-crossfade') === 'true';
    const saved = Number(localStorage.getItem('radio-crossfade-seconds') || 3);
    fadeSeconds.value = Number.isFinite(saved) ? Math.max(.5, Math.min(10, saved)) : 3;
  } catch (_) { /* Keep safe defaults if storage is unavailable. */ }
  fadeSeconds.disabled = !crossfade.checked;
  function savePlaybackSettings() {
    const value = Number(fadeSeconds.value);
    fadeSeconds.value = Number.isFinite(value) ? Math.max(.5, Math.min(10, value)) : 3;
    fadeSeconds.disabled = !crossfade.checked;
    if (!crossfade.checked || !autoplay.checked) cancelFade();
    try {
      localStorage.setItem('radio-autoplay', String(autoplay.checked));
      localStorage.setItem('radio-crossfade', String(crossfade.checked));
      localStorage.setItem('radio-crossfade-seconds', fadeSeconds.value);
    } catch (_) { /* Preferences remain usable in this tab. */ }
  }
  [autoplay, crossfade, fadeSeconds].forEach(input => input.addEventListener('change', savePlaybackSettings));
  showVideo.checked = window.TrackYouTube.visible();
  showVideo.addEventListener('change', () => window.TrackYouTube.setVisible(showVideo.checked));
  window.addEventListener('radio:youtubevisibility', () => { showVideo.checked = window.TrackYouTube.visible(); });
  function fadeVolumes() {
    if (!fade) return;
    const ratio = Math.min(1, (performance.now() - fade.started) / fade.duration);
    const master = Number(volume.value);
    fade.old.volume = master * (1 - ratio); fade.next.volume = master * ratio;
    fade.old.muted = fade.next.muted = userMuted;
    if (ratio >= 1) cancelFade();
  }
  function cancelFade() {
    if (!fade) return;
    clearInterval(fadeTimer); fadeTimer = null;
    const previous = fade; fade = null;
    previous.old.pause(); previous.old.removeAttribute('src'); previous.old.load();
    previous.next.volume = Number(volume.value);
    previous.run.pending = false;
  }
  async function blendExcerpt(track, url, run, ticket) {
    const old = excerptAudio, next = spareAudio;
    run.attempted.add(key(track, url));
    next.volume = 0; next.muted = userMuted; next.src = url;
    try { await next.play(); } catch (_) {
      next.pause(); next.removeAttribute('src'); next.load();
      if (sequence === run && previewVersion === ticket) {
        run.pending = false; run.cursor = run.currentIndex;
      }
      return;
    }
    if (sequence !== run || previewVersion !== ticket || !run.active) {
      next.pause(); next.removeAttribute('src'); next.load(); return;
    }
    excerptAudio = next; spareAudio = old; audio = next;
    next.id = 'excerpt-player'; old.id = 'transition-player';
    previewVersion++; run.currentIndex = run.cursor; run.attempted.add(key(track, url));
    previewKey = key(track, url); setCurrentTrack(track); showArtwork(track);
    title.textContent = track.title; artist.textContent = track.artist || '';
    const remaining = Number.isFinite(old.duration) ? old.duration - old.currentTime : 0;
    fade = {old, next, run, started: performance.now(), duration: Math.max(1, Math.min(Number(fadeSeconds.value), remaining) * 1000)};
    fadeTimer = setInterval(fadeVolumes, 40); fadeVolumes(); sync(); syncProgress();
  }
  const listeningSettings = document.querySelector('#listening-settings');
  const resumeRadio = document.querySelector('#resume-radio-after-preview');
  const settingsToggle = document.querySelector('#listening-settings-toggle');
  resumeRadio.checked = true;
  try { resumeRadio.checked = localStorage.getItem('radio-resume-after-preview') !== 'false'; } catch (_) { /* Optional preference. */ }
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
    const targetRadio = sequence?.returnRadio || playingRadio();
    document.querySelector('#resume-radio-target').textContent = targetRadio ? window.RadioI18n.t("ui.fed8ff834c53", {p0: (targetRadio.name)}) : window.RadioI18n.t("ui.00497f225d08");
    document.querySelectorAll('.station-logo').forEach(img => {
      img.onerror = () => img.remove();
      if (img.complete && !img.naturalWidth) img.remove();
    });
    const playing = mode === 'video' ? Boolean(video?.playing()) : (mode === 'preview' && sequence?.pending) || !audio.paused && !audio.ended && !audio.error && (mode !== 'radio' || !suspended);
    toggle.disabled = mode === 'video' ? !video : !audio.getAttribute('src');
    toggle.setAttribute('aria-label', playing ? window.RadioI18n.t("ui.42dd586c06d6") : mode === 'preview' ? window.RadioI18n.t("ui.ccc216fa2e6b") : window.RadioI18n.t("ui.ec0a4373089c"));
    toggle.setAttribute('aria-pressed', String(playing));
    toggle.querySelector('path').setAttribute('d', playing ? 'M6 5h4v14H6zM14 5h4v14h-4z' : 'M8 5v14l11-7z');
    panel.classList.toggle('is-playing', playing);
    panel.dataset.mode = mode;
    document.documentElement.dataset.playerMode = mode;
    updateExpandedMode(playing);
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
    if (!fade) volume.value = mode === 'video' ? video?.volume() ?? Number(volume.value) : audio.volume;
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
      const params = window.MusicFilters.apply(new URLSearchParams({station_id: radio.id}));
      const response = await window.MusicFilters.live(`/api/stations/now-playing?${params}`, {cache: 'no-store'});
      if (!response.ok) throw new Error('radio_metadata_unavailable');
      const data = await response.json();
      if (current !== metadataVersion || mode !== 'radio') return;
      const entry = data.items.find(item => String(item.station_id) === String(radio.id));
      const track = entry?.track;
      if (!track) title.textContent = window.RadioI18n.t('stations.unavailable');
      else if (entry.status === 'last_known') title.textContent = window.RadioI18n.t('stations.last_known_player', {title: track.title});
      else title.textContent = track.title;
      setCurrentTrack(track ? {...track, station_id: radio.id} : null);
      artist.textContent = track?.artist || '';
      showArtwork(track || {}, radio);
      sync();
    } catch (_) {
      if (current !== metadataVersion || mode !== 'radio') return;
      title.textContent = currentTrack ? window.RadioI18n.t('stations.last_known_player', {title: currentTrack.title}) : window.RadioI18n.t('stations.unavailable');
      sync();
    }
  }
  async function play({recovery = false} = {}) {
    if (mode === 'video') { video?.play(); return; }
    if (mode === 'radio') {
      const reload = audio.error || audio.ended || radioReloadRequired;
      if (!recovery) cancelRadioRecovery(true);
      suspended = false; audio.muted = userMuted;
      // A failed/ended live connection needs a fresh request, even for the same station.
      if (reload) { audio.load(); radioReloadRequired = false; }
      label.textContent = audio.readyState >= 3 ? window.RadioI18n.t("ui.235df0b17a9e") : window.RadioI18n.t("ui.910919b77ec7");
      status.textContent = '';
      if (audio.paused || audio.readyState < 3) watchRadio();
    }
    if (mode === 'preview' && sequence) sequence.active = true;
    announceAudio();
    const source = audio.src, ticket = previewVersion;
    try { await audio.play(); } catch (error) {
      if (error.name !== 'AbortError' && audio.src === source && ticket === previewVersion) {
        if (mode === 'radio' && suspended) { sync(); return; }
        label.textContent = window.RadioI18n.t("ui.ce6a03b5762e");
        status.textContent = mode === 'preview' ? window.RadioI18n.t("ui.9f4b7303e42b") : window.RadioI18n.t("ui.080d5f7d0c0b");
        if (mode === 'preview' && sequence?.active) {
          if (error.name === 'NotAllowedError') {
            sequence.active = false; sequence.pending = false;
          } else advancePreview('error');
        } else if (mode === 'radio') {
          if (error.name === 'NotAllowedError') {
            suspended = true; cancelRadioRecovery();
          } else retryRadio();
        }
      }
    }
    sync();
  }
  function selectRadio(station) {
    cancelRadioRecovery(true);
    if (radio?.url !== station.url) radioReloadRequired = false;
    cancelSequence(); metadataVersion++;
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
  async function preview(track, url, context = []) {
    if (!url) return;
    const next = key(track, url);
    if (mode === 'preview' && previewKey === next && audio.getAttribute('src') && (sequence?.pending || (!audio.error && !audio.ended))) {
      if (sequence?.pending || !audio.paused) pause(); else await play();
      return;
    }
    const returnRadio = mode === 'preview' && sequence && !audio.ended && !audio.error
      ? sequence.returnRadio
      : playingRadio();
    const run = makeSequence(track, url, context, returnRadio);
    cancelSequence();
    await startExcerpt(track, url, run);
  }
  async function startExcerpt(track, url, run) {
    cancelFade(); metadataVersion++;
    announceAudio(); video = null; quietRadio(); audio = excerptAudio;
    audio.pause();
    sequence = run; run.active = true; run.pending = false; previewVersion++;
    run.currentIndex = run.cursor; run.attempted.add(key(track, url));
    audio.volume = Number(volume.value); audio.muted = userMuted;
    mode = 'preview'; previewKey = key(track, url);
    setCurrentTrack(track); showArtwork(track);
    audio.src = url; status.textContent = '';
    title.textContent = track.title || window.RadioI18n.t("ui.7224fc1ace77"); artist.textContent = track.artist || '';
    name.textContent = window.RadioI18n.t("ui.0f8745e4abeb", {p0: (track.provider || track.metadata?.provider ? ' · ' + (track.provider || track.metadata.provider) : '')});
    sync(); syncProgress(); await play();
  }
  function finishSequence(run, reason) {
    run.active = false; run.pending = false;
    label.textContent = reason === 'error' ? window.RadioI18n.t("ui.ce6a03b5762e")
      : window.RadioI18n.t(run.items.length > 1 && !run.returnRadio ? 'preview.finished' : 'ui.a4d4a1c61e92');
    sync();
  }
  async function advancePreview(reason) {
    const run = sequence;
    if (!run?.active || run.pending || mode !== 'preview') return;
    if (reason === 'ended' && !excerptAudio.ended) return;
    run.pending = true;
    if (run.returnRadio) {
      finishSequence(run, reason);
      if (reason === 'ended' && resumeRadio.checked) selectRadio(run.returnRadio);
      return;
    }
    if (!autoplay.checked) { finishSequence(run, reason); return; }
    if (run.cursor >= run.items.length - 1) { finishSequence(run, reason); return; }
    const ticket = previewVersion, controller = new AbortController(); previewRequest = controller;
    const current = () => sequence === run && ticket === previewVersion && run.active && !controller.signal.aborted;
    label.textContent = window.RadioI18n.t('preview.next'); sync();
    try {
      // A stored list is only an order: recheck visibility and the current excerpt before playing.
      await window.MusicFilters.refresh();
      if (!current()) return;
      while (++run.cursor < run.items.length) {
        const item = run.items[run.cursor];
        if (run.attempted.has(item.key)) continue;
        const id = item.track.metadata?.track_id;
        if (!item.url || !/^[a-f0-9]{64}$/.test(id || '')) { run.attempted.add(item.key); continue; }
        const params = window.MusicFilters.apply(new URLSearchParams({view: 'tracks', track_id: id, page_size: '1'}));
        const response = await window.MusicFilters.fetch(`/api/explore?${params}`, {signal: controller.signal});
        if (!current()) return;
        if (!response.ok) throw new Error('preview_unavailable');
        const data = await response.json();
        if (!current()) return;
        const fresh = data.items?.find(row => row.metadata?.track_id === id);
        const source = fresh && previewUrl(fresh);
        if (!source) { run.attempted.add(item.key); continue; }
        if (reason === 'crossfade') await blendExcerpt({...item.track, ...fresh}, source, run, ticket);
        else await startExcerpt({...item.track, ...fresh}, source, run);
        return;
      }
      if (current()) finishSequence(run, reason);
    } catch (error) {
      if (current() && error.name !== 'AbortError') {
        finishSequence(run, 'error'); status.textContent = window.RadioI18n.t('preview.unavailable');
      }
    } finally {
      if (previewRequest === controller) previewRequest = null;
    }
  }
  document.addEventListener('click', event => {
    const button = event.target.closest('.station-play');
    if (!button) return;
    if (mode === 'radio' && panel.dataset.stationId === button.dataset.id && !audio.paused && !suspended) { pause(); return; }
    selectRadio({id: button.dataset.id, url: button.dataset.url, name: button.dataset.name});
  });
  back.addEventListener('click', () => { if (radio) selectRadio(radio); });
  toggle.addEventListener('click', () => {
    const playing = mode === 'video' ? video?.playing() : (mode === 'preview' && sequence?.pending) || !audio.paused && !audio.ended && (mode !== 'radio' || !suspended);
    if (playing) pause(); else play();
  });
  progress.addEventListener('input', () => {
    cancelFade();
    if (mode === 'preview' && sequence?.pending) pause();
    if (mode === 'video') video?.seek(Number(progress.value) / 1000 * video.duration());
    else if (mode === 'preview' && Number.isFinite(audio.duration)) audio.currentTime = Number(progress.value) / 1000 * audio.duration;
    syncProgress();
  });
  function applyVolume() {
    const value = Number(volume.value);
    if (fade) fadeVolumes(); else excerptAudio.volume = value;
    excerptAudio.muted = spareAudio.muted = userMuted;
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
  document.addEventListener('keydown', event => {
    if (event.defaultPrevented || event.isComposing || event.ctrlKey || event.altKey || event.metaKey) return;
    const target = event.target;
    if (target instanceof Element && (target.isContentEditable || target.closest('input, textarea, select, button, a, summary, [role="button"], [role="slider"], [role="textbox"], [role="combobox"], [role="menuitem"]'))) return;
    const key = event.key.toLowerCase();
    if (key === ' ' && !toggle.disabled) {
      event.preventDefault();
      if (!event.repeat) toggle.click();
    } else if (key === 'q' || key === 'd') {
      event.preventDefault();
      volume.value = Math.max(0, Math.min(1, Math.round((Number(volume.value) + (key === 'q' ? -.02 : .02)) * 100) / 100));
      userMuted = false; applyVolume();
    }
  });
  for (const sourceAudio of [radioAudio, excerptAudio, spareAudio]) {
    const listen = (event, callback) => sourceAudio.addEventListener(event, () => { if (sourceAudio === audio && mode !== 'video') callback(); });
  listen('play', () => { label.textContent = mode === 'radio' ? window.RadioI18n.t("ui.910919b77ec7") : window.RadioI18n.t("ui.a90bd5662fcc"); sync(); });
  listen('playing', () => {
    label.textContent = mode === 'radio' ? window.RadioI18n.t("ui.235df0b17a9e") : window.RadioI18n.t("ui.7224fc1ace77");
    if (mode === 'radio') {
      clearTimeout(radioWatchdog); radioWatchdog = null;
      clearTimeout(radioRetryTimer); radioRetryTimer = null;
      clearTimeout(radioStableTimer);
      status.textContent = '';
      radioStableTimer = setTimeout(() => { if (radioActive()) radioRetryCount = 0; }, 30000);
    }
    sync();
  });
  listen('waiting', () => { label.textContent = window.RadioI18n.t("ui.66474911d953"); if (mode === 'radio') watchRadio(); });
  listen('stalled', () => { if (mode === 'radio' && audio.readyState < 3) watchRadio(); });
  listen('pause', () => {
    if (audio.getAttribute('src') && !audio.error && !audio.ended && (mode === 'radio' || sequence?.active && !sequence.pending)) label.textContent = window.RadioI18n.t("ui.239c064e0d40");
    sync();
  });
  listen('ended', () => {
    if (mode === 'preview') { advancePreview('ended'); return; }
    if (mode === 'radio') retryRadio();
    label.textContent = mode === 'radio' ? window.RadioI18n.t("ui.3a3b98e30872") : window.RadioI18n.t("ui.a4d4a1c61e92"); sync();
  });
  listen('error', () => {
    if (!sourceAudio.error) return;
    label.textContent = window.RadioI18n.t("ui.ce6a03b5762e"); status.textContent = window.RadioI18n.t("ui.7b6e9406532f"); sync();
    if (mode === 'preview') advancePreview('error');
    else if (mode === 'radio') retryRadio();
  });
  listen('timeupdate', () => {
    if (mode === 'preview' && autoplay.checked && crossfade.checked && !sequence?.returnRadio && !audio.paused && !audio.ended && Number.isFinite(audio.duration)) {
      const remaining = audio.duration - audio.currentTime;
      if (remaining > 0 && remaining <= Math.min(Number(fadeSeconds.value), audio.duration / 2)) advancePreview('crossfade');
    }
  });
  ['timeupdate', 'loadedmetadata', 'durationchange', 'emptied', 'seeked'].forEach(event => listen(event, syncProgress));
  }

  window.addEventListener('radio:pagechange', sync);
  window.addEventListener('radio:filterschange', () => {
    const playingExcerpt = mode === 'preview' && sequence;
    cancelSequence();
    if (playingExcerpt) {
      stopExcerpt(); previewKey = null; setCurrentTrack(null); showArtwork();
      title.textContent = window.RadioI18n.t('preview.stopped'); artist.textContent = '';
      label.textContent = window.RadioI18n.t('preview.stopped'); status.textContent = '';
      sync(); syncProgress();
    } else if (mode === 'radio' && radio) {
      metadataVersion++; title.textContent = window.RadioI18n.t("ui.a031d88118a6"); artist.textContent = '';
      setCurrentTrack(null);
      showArtwork({}, radio); refreshNowPlaying();
    }
  });
  function pause() {
    cancelFade();
    if (mode === 'video') video?.pause();
    else if (mode === 'radio') { quietRadio(); label.textContent = window.RadioI18n.t("ui.239c064e0d40"); }
    else {
      if (sequence) { sequence.active = false; sequence.pending = false; sequence.cursor = sequence.currentIndex; }
      previewVersion++; previewRequest?.abort(); previewRequest = null;
      audio.pause(); label.textContent = window.RadioI18n.t("ui.239c064e0d40");
    }
    sync();
  }
  function startVideo(adapter, track) {
    cancelSequence(); quietRadio(); excerptAudio.pause(); video = adapter; metadataVersion++;
    mode = 'video'; previewKey = null; setCurrentTrack(track); showArtwork(track);
    const station = track.station_id && document.querySelector(`.station-history-open[data-id="${CSS.escape(String(track.station_id))}"]`);
    title.textContent = track.title; artist.textContent = track.artist || '';
    name.textContent = track.station_name || station?.textContent.trim() || window.RadioI18n.t("ui.fb7accfff8c6");
    video.setVolume(Number(volume.value), userMuted);
    sync(); syncProgress();
  }
  function videoStopped() {
    if (mode !== 'video') return;
    video = null; mode = 'preview'; audio = excerptAudio; stopExcerpt();
    label.textContent = window.RadioI18n.t("ui.adf3f3af5692"); sync(); syncProgress();
  }
  window.RadioPlayer = {autoplay: () => autoplay.checked, preview, previewUrl, key, sync, pause, openMode, startVideo, videoStopped, videoState() {
    if (mode === 'video') { userMuted = video?.muted?.() ?? userMuted; syncVolume(); label.textContent = video?.playing() ? window.RadioI18n.t("ui.fb7accfff8c6") : window.RadioI18n.t("ui.239c064e0d40"); sync(); syncProgress(); }
  }};
  window.setInterval(() => { if (mode === 'video') { userMuted = video?.muted?.() ?? userMuted; syncVolume(); syncProgress(); } }, 500);

  radioAudio.muted = true;
  if (radio) { showArtwork({}, radio); refreshNowPlaying(); }
  window.setInterval(() => { if (!document.hidden && mode === 'radio') refreshNowPlaying(); }, 20000);
  syncVolume(); sync(); syncProgress();
})();
