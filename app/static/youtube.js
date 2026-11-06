// One video host survives page and dialog navigation; Google loads only on demand.
window.TrackYouTube = (() => {
  let api, player, controller, version = 0, activeTrack, currentVideo, available = [], anchor;
  let ready = false;
  const el = (tag, text = '', className = '') => { const node = document.createElement(tag); node.textContent = text; node.className = className; return node; };
  const dock = el('section', '', 'catalog-youtube youtube-dock'); dock.id = 'youtube-dock'; dock.hidden = true;
  const heading = el('h3', window.RadioI18n.t("ui.46aa38caf70b"));
  const close = el('button', window.RadioI18n.t("ui.5e02c28191a4"), 'text-button'); close.type = 'button';
  const floating = el('button', window.RadioI18n.t("ui.d7c6736fa9f6"), 'text-button'); floating.type = 'button';
  const pip = el('button', window.RadioI18n.t("ui.5b22b5373a8e"), 'text-button'); pip.type = 'button';
  const status = el('p', '', 'hint'); status.setAttribute('role', 'status');
  const choices = el('div', '', 'youtube-choices');
  const viewport = el('div', '', 'youtube-viewport');
  const toolbar = el('div', '', 'youtube-toolbar'); toolbar.append(heading, floating, pip, close);
  dock.append(toolbar, status, choices, viewport); document.body.append(dock);
  function relocate(parent) {
    if (dock.parentNode !== parent) {
      if (parent.moveBefore) parent.moveBefore(dock, null);
      else if (!player) parent.append(dock);
      else return false;
    }
    dock.classList.toggle('is-floating', parent === document.body);
    floating.hidden = parent === document.body;
    return true;
  }
  function minimize() { relocate(document.body); }
  function stop() {
    version++; controller?.abort(); controller = null;
    try { player?.destroy(); } catch (_) { /* A failed embed can already be detached. */ }
    player = null; ready = false; activeTrack = null; currentVideo = null;
    viewport.replaceChildren(); choices.replaceChildren(); dock.hidden = true;
    relocate(document.body); window.RadioPlayer?.videoStopped();
  }
  close.onclick = stop; floating.onclick = minimize;
  pip.hidden = typeof document.documentElement.requestFullscreen !== 'function';
  pip.textContent = window.RadioI18n.t("ui.dee312b0129c");
  pip.onclick = async () => {
    // The embedded cross-origin video owns native PiP; only its controls may offer it.
    try { await viewport.requestFullscreen(); }
    catch (_) { status.textContent = window.RadioI18n.t("ui.feb8fa426c9b"); }
  };
  function loadApi() {
    if (window.YT?.Player) return Promise.resolve();
    if (api) return api;
    api = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      const fail = () => { clearTimeout(timer); script.remove(); reject(new Error(window.RadioI18n.t("ui.7a63eba3c325"))); };
      const timer = setTimeout(fail, 15000);
      const previous = window.onYouTubeIframeAPIReady;
      window.onYouTubeIframeAPIReady = () => { clearTimeout(timer); previous?.(); resolve(); };
      script.onerror = fail; script.src = 'https://www.youtube.com/iframe_api'; document.head.append(script);
    }).catch(error => { api = null; throw error; });
    return api;
  }
  const adapter = {
    playing: () => player?.getPlayerState?.() === 1,
    play: () => player?.playVideo?.(), pause: () => player?.pauseVideo?.(),
    duration: () => player?.getDuration?.() || 0, position: () => player?.getCurrentTime?.() || 0,
    seek: value => player?.seekTo?.(value, true), volume: () => ready && Number.isFinite(player?.getVolume?.()) ? player.getVolume() / 100 : null,
    setVolume: (value, muted = false) => { player?.setVolume?.(value * 100); if (muted || value === 0) player?.mute?.(); else player?.unMute?.(); },
    muted: () => ready ? player?.isMuted?.() || false : undefined,
  };
  async function show(video) {
    if (!/^[A-Za-z0-9_-]{11}$/.test(video.id)) return;
    const current = ++version; currentVideo = video;
    delete dock.dataset.videoError; delete dock.dataset.videoState;
    player?.destroy(); player = null; ready = false; viewport.replaceChildren(); choices.replaceChildren();
    dock.hidden = false; status.textContent = window.RadioI18n.t("ui.f9032b20af36", {p0: (video.title), p1: (video.channel)});
    window.RadioPlayer.startVideo(adapter, activeTrack);
    try {
      await loadApi();
      if (current !== version) return;
      const iframe = el('iframe'); iframe.title = video.title;
      iframe.allow = 'autoplay; encrypted-media; picture-in-picture; fullscreen'; iframe.allowFullscreen = true;
      iframe.referrerPolicy = 'strict-origin-when-cross-origin';
      const url = new URL(`https://www.youtube-nocookie.com/embed/${video.id}`);
      url.search = new URLSearchParams({enablejsapi: '1', origin: location.origin, autoplay: '1', playsinline: '1', rel: '0'});
      iframe.src = url; viewport.append(iframe);
      player = new window.YT.Player(iframe, {host: 'https://www.youtube-nocookie.com', events: {
        onReady: event => {
          if (current !== version) return;
          ready = true;
          window.RadioPlayer.startVideo(adapter, activeTrack); event.target.playVideo();
          status.textContent = window.RadioI18n.t("ui.f5e700fd0436", {p0: (video.title), p1: (video.channel)});
        },
        onStateChange: event => {
          if (current !== version) return;
          dock.dataset.videoState = String(event.data); window.RadioPlayer.videoState();
        },
        onAutoplayBlocked: () => { if (current === version) status.textContent = window.RadioI18n.t("ui.fe115b53936d"); },
        onError: event => {
          if (current !== version) return;
          dock.dataset.videoError = String(event?.data ?? 'unknown');
          status.textContent = window.RadioI18n.t("ui.363fbfbed3b7");
          choices.replaceChildren();
          for (const alternative of available.filter(item => item.id !== video.id)) addChoice(alternative);
          const external = el('a', window.RadioI18n.t("ui.37a3b2361152")); external.href = `https://www.youtube.com/watch?v=${video.id}`; external.target = '_blank'; external.rel = 'noopener noreferrer'; choices.append(external);
        },
      }});
    } catch (error) { if (current === version) status.textContent = error.message; }
  }
  function addChoice(video) {
    const button = el('button', `${video.title} · ${video.channel}`, 'text-button'); button.type = 'button'; button.onclick = () => show(video); choices.append(button);
  }
  async function start(track, panel, content) {
    stop(); activeTrack = track; anchor = panel;
    // moveBefore preserves iframe state on supported browsers. Else float from the outset.
    if (content?.moveBefore) relocate(content);
    else { relocate(document.body); panel?.close(); }
    dock.hidden = false; controller = new AbortController(); const current = version;
    status.textContent = window.RadioI18n.t("ui.1bc5757c9939");
    try {
      const params = window.MusicFilters.apply(new URLSearchParams());
      const response = await window.MusicFilters.fetch(`/api/catalog/${encodeURIComponent(track.metadata?.track_id || track.track_id)}/youtube?${params}`, {signal: controller.signal});
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? window.RadioI18n.t(data.detail) : window.RadioI18n.t("ui.8f99f67c9c6a"));
      if (current !== version) return;
      available = data.candidates || [];
      const selected = data.selected || (data.status === 'matched' ? available[0] : null);
      if (selected) await show(selected);
      else if (available.length && data.status !== 'rejected') {
        status.textContent = window.RadioI18n.t("ui.105ebf437b14"); available.forEach(addChoice);
      } else status.textContent = window.RadioI18n.t("ui.b2c013c4927b");
      if (!dock.classList.contains('is-floating')) dock.scrollIntoView({block: 'nearest', behavior: 'smooth'});
    } catch (error) { if (error.name !== 'AbortError' && current === version) status.textContent = error.message; }
  }
  function button(track, action) {
    const button = el('button', window.RadioI18n.t("ui.fb7accfff8c6"), 'youtube-button platform-link'); button.type = 'button';
    button.setAttribute('aria-label', window.RadioI18n.t("ui.7204ecace0ab", {p0: (track.title)}));
    button.onclick = event => { event.stopPropagation(); if (action) action(); else window.TrackCatalog.open(track, {youtube: true}); };
    return button;
  }
  function mount(panel, content, links, trackId, track, controls, autoplay = false) {
    track = {...track, track_id: trackId};
    const play = () => {
      if (activeTrack?.track_id === trackId && currentVideo) {
        anchor = panel; if (content.moveBefore) relocate(content); dock.hidden = false; return;
      }
      start(track, panel, content);
    };
    links.append(button(track, play)); controls?.append(button(track, play));
    // Close moves the same iframe out before navigation removes the dialog.
    panel.addEventListener('cancel', () => { if (anchor === panel) minimize(); });
    const nativeClose = panel.close.bind(panel);
    panel.close = (...args) => { if (anchor === panel) minimize(); nativeClose(...args); };
    panel.addEventListener('close', () => { if (anchor === panel) minimize(); }, {once: true});
    if (activeTrack?.track_id === trackId && player && content.moveBefore) { anchor = panel; relocate(content); }
    if (autoplay) play();
  }
  window.addEventListener('radio:audioactive', stop);
  window.addEventListener('radio:filterschange', stop);
  window.addEventListener('radio:globalfilterschange', stop);
  window.addEventListener('radio:beforepagechange', minimize);
  return {mount, button, stop, minimize};
})();
