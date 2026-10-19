// Per-browser-tab cache: bounded, expiring, and optional when storage is unavailable.
window.RadioCache = (() => {
  const prefix = `radio-cache-v2:${document.body.dataset.assetVersion}:`;
  const memory = new Map();
  const maxAge = 5 * 60 * 1000;
  const limit = 40;
  try {
    for (let i = sessionStorage.length - 1; i >= 0; i--) {
      const key = sessionStorage.key(i);
      if (key.startsWith('radio-cache-') && !key.startsWith(prefix)) sessionStorage.removeItem(key);
    }
    for (const key of Object.keys(sessionStorage).filter(k => k.startsWith(prefix))) {
      const item = JSON.parse(sessionStorage.getItem(key));
      if (Date.now() - item.time < maxAge) memory.set(key.slice(prefix.length), item);
      else sessionStorage.removeItem(key);
    }
  } catch (_) { /* Private browsing or quota: continue with memory. */ }
  function get(key) {
    const item = memory.get(key);
    if (!item || Date.now() - item.time > maxAge) return null;
    memory.delete(key);
    memory.set(key, item);
    return item;
  }
  function put(key, data) {
    const item = {time: Date.now(), data};
    memory.delete(key);
    memory.set(key, item);
    while (memory.size > limit) {
      const oldest = memory.keys().next().value;
      memory.delete(oldest);
      try { sessionStorage.removeItem(prefix + oldest); } catch (_) { /* optional */ }
    }
    try { sessionStorage.setItem(prefix + key, JSON.stringify(item)); } catch (_) { /* optional */ }
  }
  return {get, put};
})();
