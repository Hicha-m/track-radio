// Generate links locally: no provider search runs when a track dialog opens.
window.TrackPlatforms = (() => {
  const providers = {deezer: 'Deezer', itunes: 'Apple Music', spotify: 'Spotify'};
  function links(data, track) {
    const result = [];
    const seen = new Set();
    for (const source of [data, ...(data.additional_sources || [])]) {
      if (source.provider === 'spotify' || !source.url || seen.has(source.provider)) continue;
      try { if (new URL(source.url).protocol !== 'https:') continue; } catch (_) { continue; }
      result.push({label: `Ouvrir sur ${providers[source.provider] || source.provider}`, url: source.url});
      seen.add(source.provider);
    }
    const spotify = [data, ...(data.additional_sources || [])].find(source => source.provider === 'spotify' && /^[A-Za-z0-9]{22}$/.test(source.provider_id || ''));
    const query = encodeURIComponent(`${data.artist || track.artist || ''} ${data.title || track.title}`.trim());
    const path = spotify ? `track/${spotify.provider_id}` : `search/${query}`;
    result.push({label: spotify ? 'Ouvrir sur Spotify' : 'Rechercher sur Spotify', url: `https://open.spotify.com/${path}`, appUrl: spotify ? `spotify:track:${spotify.provider_id}` : `spotify:search:${query}`});
    return result;
  }
  let cancelPending = () => {};
  function openWithFallback(target) {
    cancelPending();
    let timer;
    let active = true;
    const original = window.location.href;
    function cancel() {
      active = false; clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('pagehide', cancel);
      window.removeEventListener('blur', cancel);
      window.removeEventListener('radio:pagechange', cancel);
      window.removeEventListener('popstate', cancel);
    }
    function onVisibility() { if (document.visibilityState === 'hidden') cancel(); }
    cancelPending = cancel;
    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('pagehide', cancel);
    window.addEventListener('blur', cancel);
    window.addEventListener('radio:pagechange', cancel);
    window.addEventListener('popstate', cancel);
    timer = setTimeout(() => {
      const fallback = active && document.visibilityState !== 'hidden' && window.location.href === original;
      cancel();
      if (fallback) window.location.assign(target.url);
    }, 1000);
    try { window.location.assign(target.appUrl); }
    catch (_) { /* A refused protocol still falls back after one second. */ }
  }
  return {links, openWithFallback};
})();
