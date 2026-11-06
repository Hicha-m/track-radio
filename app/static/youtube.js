// One video host survives page and dialog navigation; Google loads only on demand.
window.TrackYouTube = (() => {
  let api, player, controller, version = 0, activeTrack, currentVideo, available = [], anchor;
  let ready = false;
  const el = (tag, text = '', className = '') => { const node = document.createElement(tag); node.textContent = text; node.className = className; return node; };
  const dock = el('section', '', 'catalog-youtube youtube-dock'); dock.id = 'youtube-dock'; dock.hidden = true;
  dock.setAttribute('aria-label', window.RadioI18n.t("ui.46aa38caf70b"));
  const close = el('button', '×', 'youtube-close'); close.type = 'button';
  close.setAttribute('aria-label', window.RadioI18n.t("ui.5e02c28191a4"));
  close.title = window.RadioI18n.t("ui.5e02c28191a4");
  const size = el('button', '↗', 'youtube-size'); size.type = 'button';
  const grip = el('button', '⋰', 'youtube-resize'); grip.type = 'button';
  grip.setAttribute('role', 'slider'); grip.setAttribute('aria-label', window.RadioI18n.t('youtube.size'));
  grip.setAttribute('aria-orientation', 'horizontal');
  const status = el('p', '', 'youtube-status'); status.setAttribute('role', 'status');
  const choices = el('div', '', 'youtube-choices');
  const viewport = el('div', '', 'youtube-viewport'); viewport.id = 'youtube-viewport';
  size.setAttribute('aria-controls', viewport.id); grip.setAttribute('aria-controls', viewport.id);
  const toolbar = el('div', '', 'youtube-toolbar'); toolbar.append(size, close);
  dock.append(toolbar, status, choices, viewport, grip); document.body.append(dock);
  dock.onpointerenter = () => dock.classList.add('is-hovered');
  dock.onpointerleave = () => dock.classList.remove('is-hovered');
  const widths = {floating: 360, inline: 640};
  let drag = null;
  const floating = () => dock.parentNode === document.body;
  function bounds() {
    let max = Math.min(720, dock.parentElement.clientWidth);
    if (floating()) {
      const playerTop = document.querySelector('.player')?.getBoundingClientRect().top ?? innerHeight;
      let bottom = Math.max(12, innerHeight - playerTop + 12);
      // On short landscape screens keep the entire dock reachable, with internal scrolling.
      if (innerHeight - bottom < 280) bottom = 12;
      dock.style.setProperty('--youtube-bottom', `${bottom}px`);
      max = Math.min(720, innerWidth - 24, Math.max(200, innerHeight - bottom - 84) * 16 / 9 + 18);
    }
    max = Math.floor(Math.max(1, max));
    return {min: Math.min(224, max), max};
  }
  function fit(width) {
    if (dock.hidden) return;
    const mode = floating() ? 'floating' : 'inline';
    const {min, max} = bounds();
    const value = Math.round(Math.max(min, Math.min(max, width ?? widths[mode])));
    if (width !== undefined) widths[mode] = value;
    dock.style.setProperty('--youtube-width', `${value}px`);
    grip.setAttribute('aria-valuemin', String(min)); grip.setAttribute('aria-valuemax', String(max));
    grip.setAttribute('aria-valuenow', String(value));
    grip.setAttribute('aria-valuetext', window.RadioI18n.t('youtube.pixels', {width: value}));
    const large = value >= max - 1;
    size.textContent = large ? '↙' : '↗';
    size.title = window.RadioI18n.t(large ? 'youtube.shrink' : 'youtube.enlarge');
    size.setAttribute('aria-label', size.title);
    const hasVideo = viewport.childElementCount > 0;
    size.hidden = grip.hidden = !hasVideo || max === min;
  }
  function endResize() {
    if (drag && grip.hasPointerCapture(drag.id)) grip.releasePointerCapture(drag.id);
    drag = null; dock.classList.remove('is-resizing');
  }
  size.onclick = () => {
    const {min, max} = bounds();
    fit(dock.getBoundingClientRect().width >= max - 1 ? Math.max(min, Math.min(360, max * .6)) : max);
  };
  grip.onpointerdown = event => {
    if (event.button !== 0) return;
    event.preventDefault(); grip.focus(); grip.setPointerCapture(event.pointerId);
    drag = {id: event.pointerId, x: event.clientX, width: dock.getBoundingClientRect().width, floating: floating()};
    dock.classList.add('is-resizing');
  };
  grip.onpointermove = event => {
    if (drag?.id !== event.pointerId) return;
    fit(drag.width + (event.clientX - drag.x) * (drag.floating ? -1 : 1));
  };
  grip.onpointerup = grip.onpointercancel = grip.onlostpointercapture = () => { drag = null; dock.classList.remove('is-resizing'); };
  grip.onkeydown = event => {
    const {min, max} = bounds();
    const value = Number(grip.getAttribute('aria-valuenow'));
    const next = {ArrowLeft: value - 32, ArrowDown: value - 32, ArrowRight: value + 32, ArrowUp: value + 32, Home: min, End: max}[event.key];
    if (next !== undefined) { event.preventDefault(); fit(next); }
  };
  function relocate(parent) {
    endResize();
    if (dock.parentNode !== parent) {
      if (parent.moveBefore) parent.moveBefore(dock, null);
      else if (!player) parent.append(dock);
      else return false;
    }
    dock.classList.toggle('is-floating', parent === document.body);
    fit();
    return true;
  }
  function minimize() {
    if (player && viewport.childElementCount && !dock.dataset.videoError) {
      relocate(document.body); anchor = null;
    } else stop();
  }
  function stop() {
    version++; controller?.abort(); controller = null; endResize();
    try { player?.destroy(); } catch (_) { /* A failed embed can already be detached. */ }
    player = null; ready = false; activeTrack = null; currentVideo = null; anchor = null; available = [];
    viewport.replaceChildren(); choices.replaceChildren(); status.textContent = ''; dock.hidden = true;
    dock.classList.remove('has-video', 'is-hovered'); delete dock.dataset.videoError; delete dock.dataset.videoState;
    relocate(document.body); window.RadioPlayer?.videoStopped();
  }
  close.onclick = () => {
    const target = anchor?.querySelector('.preview-entry .youtube-button');
    stop(); target?.focus();
  };
  window.addEventListener('resize', () => fit());
  const layout = new ResizeObserver(() => fit());
  const sharedPlayer = document.querySelector('.player'); if (sharedPlayer) layout.observe(sharedPlayer);
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
    dock.hidden = false; dock.classList.remove('has-video');
    status.textContent = window.RadioI18n.t("ui.1bc5757c9939"); fit();
    window.RadioPlayer.startVideo(adapter, activeTrack);
    try {
      await loadApi();
      if (current !== version) return;
      // Without a state-preserving move, float only once a video was actually selected.
      if (anchor?.open && !dock.parentNode.moveBefore) {
        const panel = anchor; anchor = null; relocate(document.body); panel.close();
      }
      const iframe = el('iframe'); iframe.title = video.title;
      iframe.allow = 'autoplay; encrypted-media; picture-in-picture; fullscreen'; iframe.allowFullscreen = true;
      iframe.referrerPolicy = 'strict-origin-when-cross-origin';
      const url = new URL(`https://www.youtube-nocookie.com/embed/${video.id}`);
      url.search = new URLSearchParams({enablejsapi: '1', origin: location.origin, autoplay: '1', playsinline: '1', rel: '0', controls: '0'});
      iframe.src = url; viewport.append(iframe); dock.classList.add('has-video'); fit();
      player = new window.YT.Player(iframe, {host: 'https://www.youtube-nocookie.com', events: {
        onReady: event => {
          if (current !== version) return;
          ready = true;
          window.RadioPlayer.startVideo(adapter, activeTrack); event.target.playVideo();
          status.textContent = ''; fit();
        },
        onStateChange: event => {
          if (current !== version) return;
          dock.dataset.videoState = String(event.data);
          if (event.data === 1) status.textContent = '';
          window.RadioPlayer.videoState();
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
    // Empty search/choice panels can move normally; loaded iframes require moveBefore.
    relocate(content || document.body);
    dock.hidden = false; controller = new AbortController(); const current = version;
    status.textContent = window.RadioI18n.t("ui.1bc5757c9939"); fit();
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
      if (activeTrack?.track_id === trackId && player && currentVideo && !dock.dataset.videoError) {
        if (content.moveBefore) { anchor = panel; relocate(content); }
        else { anchor = null; panel.close(); }
        dock.hidden = false; fit(); return;
      }
      start(track, panel, content);
    };
    (controls || links).append(button(track, play));
    // Close moves the same iframe out before navigation removes the dialog.
    const nativeClose = panel.close.bind(panel);
    panel.close = (...args) => {
      // Move after closing: the body is interactive again when the iframe changes parent.
      const attached = anchor === panel; nativeClose(...args); if (attached) minimize();
    };
    panel.addEventListener('close', () => { if (anchor === panel) minimize(); }, {once: true});
    if (activeTrack?.track_id === trackId && player && content.moveBefore) { anchor = panel; relocate(content); }
    if (autoplay) play();
  }
  window.addEventListener('radio:audioactive', stop);
  window.addEventListener('radio:filterschange', stop);
  window.addEventListener('radio:globalfilterschange', stop);
  window.addEventListener('radio:beforepagechange', () => { if (anchor?.open) anchor.close(); else minimize(); });
  return {mount, button, stop, minimize};
})();
