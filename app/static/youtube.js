// No Google script, search or video is loaded before the visitor requests YouTube.
window.TrackYouTube = (() => {
  let api;
  function loadApi() {
    if (window.YT?.Player) return Promise.resolve();
    if (api) return api;
    api = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      const timer = setTimeout(() => { script.remove(); reject(new Error('Lecteur YouTube indisponible.')); }, 15000);
      const previous = window.onYouTubeIframeAPIReady;
      window.onYouTubeIframeAPIReady = () => { clearTimeout(timer); previous?.(); resolve(); };
      script.onerror = () => { clearTimeout(timer); script.remove(); reject(new Error('Lecteur YouTube indisponible.')); };
      script.src = 'https://www.youtube.com/iframe_api'; document.head.append(script);
    }).catch(error => { api = null; throw error; });
    return api;
  }
  function mount(panel, content, links, trackId) {
    const el = (tag, text = '') => { const node = document.createElement(tag); node.textContent = text; return node; };
    const button = el('button', 'YouTube'); button.type = 'button'; button.className = 'platform-link'; links.append(button);
    const section = el('section'); section.className = 'catalog-youtube'; section.hidden = true;
    const heading = el('h3', 'Vidéo YouTube');
    const close = el('button', 'Fermer la vidéo'); close.type = 'button'; close.className = 'text-button';
    const status = el('p'); status.className = 'hint'; status.setAttribute('role', 'status');
    const choices = el('div'); choices.className = 'youtube-choices';
    const viewport = el('div'); viewport.className = 'youtube-viewport';
    section.append(heading, close, status, choices, viewport); content.append(section);
    let player; let controller; let version = 0; let available = [];
    const audio = document.querySelector('#radio-player');
    function stop() {
      version++; controller?.abort();
      try { player?.destroy(); } catch (_) { /* The dialog may already have been detached by navigation. */ }
      player = null;
      viewport.replaceChildren(); choices.replaceChildren(); section.hidden = true; button.disabled = false;
    }
    close.onclick = () => { stop(); button.focus(); };
    async function show(video) {
      if (!/^[A-Za-z0-9_-]{11}$/.test(video.id)) return;
      const current = ++version;
      delete section.dataset.videoError; delete section.dataset.videoState;
      player?.destroy(); player = null; viewport.replaceChildren(); choices.replaceChildren();
      window.RadioPlayer.pause(); section.hidden = false;
      status.textContent = `Chargement de ${video.title} · ${video.channel}…`;
      try {
        await loadApi();
        if (!panel.open || current !== version) return;
        const iframe = el('iframe'); iframe.title = video.title;
        iframe.allow = 'autoplay; encrypted-media; picture-in-picture; fullscreen'; iframe.allowFullscreen = true;
        iframe.referrerPolicy = 'strict-origin-when-cross-origin';
        const url = new URL(`https://www.youtube-nocookie.com/embed/${video.id}`);
        url.search = new URLSearchParams({enablejsapi: '1', origin: location.origin, autoplay: '1', playsinline: '1', rel: '0'});
        iframe.src = url; viewport.append(iframe);
        player = new window.YT.Player(iframe, {host: 'https://www.youtube-nocookie.com', events: {
          onReady: event => { if (current === version) { event.target.playVideo(); status.textContent = `${video.title} · ${video.channel}. Utilisez ▶ dans la vidéo si nécessaire.`; } },
          onStateChange: event => {
            if (current !== version) return;
            section.dataset.videoState = String(event.data);
            if (event.data === 1) window.RadioPlayer.pause();
          },
          onAutoplayBlocked: () => { if (current === version) status.textContent = 'Appuyez sur ▶ dans le lecteur YouTube pour lancer la vidéo.'; },
          onError: event => {
            if (current !== version) return;
            section.dataset.videoError = String(event?.data ?? 'unknown');
            status.textContent = 'Cette vidéo ne peut pas être lue ici. Essayez une autre proposition ou ouvrez-la sur YouTube.';
            choices.replaceChildren();
            for (const alternative of available.filter(item => item.id !== video.id)) {
              const choice = el('button', `${alternative.title} · ${alternative.channel}`); choice.type = 'button'; choice.className = 'text-button'; choice.onclick = () => show(alternative); choices.append(choice);
            }
            const external = el('a', 'Ouvrir cette vidéo sur YouTube'); external.href = `https://www.youtube.com/watch?v=${video.id}`; external.target = '_blank'; external.rel = 'noopener noreferrer'; choices.append(external);
          },
        }});
        section.scrollIntoView({block: 'nearest', behavior: 'smooth'});
      } catch (error) { if (current === version) status.textContent = error.message; }
    }
    button.onclick = async () => {
      stop(); section.hidden = false; button.disabled = true;
      controller = new AbortController(); const current = version;
      status.textContent = 'Recherche de la vidéo du morceau…';
      try {
        const params = window.MusicFilters.apply(new URLSearchParams());
        const response = await window.MusicFilters.fetch(`/api/catalog/${encodeURIComponent(trackId)}/youtube?${params}`, {signal: controller.signal});
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Recherche YouTube indisponible.');
        if (!panel.open || current !== version) return;
        available = data.candidates || [];
        if (data.status === 'matched' && data.candidates?.length) await show(data.candidates[0]);
        else if (data.candidates?.length) {
          status.textContent = 'Plusieurs vidéos peuvent correspondre : choisissez celle à écouter.';
          for (const video of data.candidates) {
            const choice = el('button', `${video.title} · ${video.channel}`); choice.type = 'button'; choice.className = 'text-button'; choice.onclick = () => show(video); choices.append(choice);
          }
          section.scrollIntoView({block: 'nearest', behavior: 'smooth'});
        } else status.textContent = 'Aucune vidéo correspondant à ce morceau et disponible en lecture intégrée.';
      } catch (error) { if (error.name !== 'AbortError' && current === version) status.textContent = error.message; }
      finally { if (panel.open) button.disabled = false; }
    };
    audio.addEventListener('play', stop);
    window.addEventListener('radio:filterschange', stop);
    panel.addEventListener('close', () => {
      stop(); audio.removeEventListener('play', stop); window.removeEventListener('radio:filterschange', stop);
    }, {once: true});
  }
  return {mount};
})();
