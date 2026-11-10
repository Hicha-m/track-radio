// A bounded, in-memory selection. Only explicit search/play/export actions contact providers.
window.RadioPlaylists = (() => {
  let daily = false;
  let scope = {}, activeScope = {}, opening = 0, panel, rows = [], generation = 0, request, playing = false;
  const t = (key, args) => window.RadioI18n.t(`playlist.${key}`, args);
  const el = (tag, text = '') => { const node = document.createElement(tag); node.textContent = text; return node; };
  const find = name => panel.querySelector(`[data-playlist-${name}]`);
  const selected = () => rows.filter(row => row.included);
  function cancel() { generation++; request?.abort(); request = null; }
  function edit() { cancel(); stopPlayback(); find('stop').hidden = true; }
  function stopPlayback() { if (playing) window.TrackYouTube.stop(); playing = false; if (panel?.open) find('pause').disabled = true; }
  function update() {
    const count = selected().length;
    find('export').disabled = find('resolve').disabled = !count;
    find('play').disabled = !selected().some(row => row.video);
  }
  function render() {
    const list = find('items'); list.replaceChildren();
    rows.forEach((row, index) => {
      const li = el('li'), head = el('div'); head.className = 'playlist-item-head';
      const include = el('input'); include.type = 'checkbox'; include.checked = row.included;
      include.setAttribute('aria-label', t('include', {title: row.title}));
      include.onchange = () => { edit(); row.included = include.checked; update(); };
      head.append(include, el('strong', `${row.title} · ${row.artist || '—'}`));
      for (const [direction, delta] of [['up', -1], ['down', 1]]) {
        const button = el('button', delta < 0 ? '↑' : '↓'); button.type = 'button';
        button.setAttribute('aria-label', t(direction, {title: row.title}));
        button.disabled = index + delta < 0 || index + delta >= rows.length;
        button.onclick = () => { edit(); [rows[index], rows[index + delta]] = [rows[index + delta], rows[index]]; render(); list.children[index + delta].querySelector(`button[aria-label="${CSS.escape(t(direction, {title: row.title}))}"]`)?.focus(); };
        head.append(button);
      }
      li.append(head, el('small', t('plays', {count: row.plays})));
      const choices = el('select'); choices.setAttribute('aria-label', t('choose') + ' : ' + row.title);
      const blank = el('option', t('choose')); blank.value = ''; choices.append(blank);
      for (const candidate of row.candidates || []) {
        if (!/^[A-Za-z0-9_-]{11}$/.test(candidate.id)) continue;
        const option = el('option', `${candidate.title} · ${candidate.channel}`); option.value = candidate.id; choices.append(option);
      }
      choices.value = row.video?.id || '';
      choices.onchange = () => { edit(); row.video = row.candidates.find(item => item.id === choices.value) || null; row.state = row.video ? 'matched' : 'unresolved'; render(); };
      li.append(choices, el('small', t(row.state || 'unresolved'))); list.append(li);
    });
    update();
  }
  async function jsonRequest(url, options = {}) {
    request = new AbortController();
    const timer = setTimeout(() => request?.abort(), 25000);
    try {
      const response = await window.MusicFilters.fetch(url, {...options, signal: request.signal});
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? window.RadioI18n.t(data.detail) : t('failed'));
      return data;
    } finally { clearTimeout(timer); }
  }
  const payload = list => list.map(row => ({track_id: row.track_id, video_id: row.video?.id || null}));
  const params = () => window.MusicFilters.apply(new URLSearchParams());
  async function validate(list) {
    return jsonRequest(`/api/playlists/export?${params()}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({items: payload(list), destination: 'youtube'})});
  }
  async function prepare(event) {
    event?.preventDefault(); cancel(); stopPlayback(); rows = []; render();
    const current = generation; find('status').textContent = t('loading');
    find('stop').hidden = true;
    try {
      await window.MusicFilters.refresh();
      // A discovered global revision intentionally invalidates the pending composition.
      if (current !== generation || !panel.open) return;
      const query = new URLSearchParams(daily ? {station_ids: activeScope.station_ids || ''} : activeScope);
      const form = find('form'); query.set('limit', form.elements.limit.value); if (!daily) query.set('sort', form.elements.sort.value);
      window.MusicFilters.apply(query);
      const data = await jsonRequest(`/api/playlists/${daily ? 'daily' : 'selection'}?${query}`);
      if (current !== generation || !panel.open) return;
      rows = data.items.map(row => ({...row, included: true, candidates: row.candidates || [], video: row.video || null})); render();
      if (daily) find('scope').textContent = window.RadioI18n.t('daily.scope', {date: data.date});
      find('status').textContent = rows.length ? t('ready', {count: rows.length}) : t('empty');
    } catch (error) { if (current === generation) find('status').textContent = error.name === 'AbortError' ? t('failed') : error.message; }
  }
  async function resolve() {
    cancel(); stopPlayback(); const current = generation, list = [...selected()];
    find('resolve').disabled = true; find('stop').hidden = false;
    try {
      await window.MusicFilters.refresh();
      if (current !== generation) return;
      for (const [index, row] of list.entries()) {
        find('status').textContent = t('progress', {current: index + 1, total: list.length});
        try {
          const data = await jsonRequest(`/api/catalog/${row.track_id}/youtube?${params()}`);
          if (current !== generation || !panel.open) return;
          row.candidates = data.status === 'rejected' ? [] : data.candidates || [];
          row.video = data.selected || (data.status === 'matched' ? row.candidates[0] : null);
          if (data.status === 'rejected') row.video = null;
          if (row.video && !row.candidates.some(v => v.id === row.video.id)) row.candidates.unshift(row.video);
          row.state = row.video ? 'matched' : ['not_found', 'rejected', 'pending'].includes(data.status) ? data.status : 'unresolved';
          render();
        } catch (error) {
          if (current !== generation) return;
          row.state = 'error'; row.video = null; render();
          // A quota, timeout or provider failure stops the batch; completed choices survive.
          find('status').textContent = error.name === 'AbortError' ? t('error') : error.message;
          return;
        }
      }
      find('status').textContent = t('resolved', {count: selected().filter(row => row.video).length});
    } catch (error) { if (current === generation) find('status').textContent = error.message; }
    finally { if (current === generation) { find('stop').hidden = true; update(); } }
  }
  async function play() {
    cancel(); stopPlayback(); const current = generation;
    const queue = [...selected().filter(row => row.video)]; let cursor = 0;
    const videos = new Set();
    async function next() {
      if (current !== generation || !panel.open) return;
      if (cursor > 0 && !window.RadioPlayer.autoplay()) { stopPlayback(); find('status').textContent = t('finished'); return; }
      if (cursor >= queue.length) { stopPlayback(); find('status').textContent = t('finished'); return; }
      const row = queue[cursor++], playerVersion = window.TrackYouTube.generation();
      try {
        await window.MusicFilters.refresh();
        if (current !== generation) return;
        const data = await validate([row]);
        if (current !== generation || playerVersion !== window.TrackYouTube.generation() || !panel.open) return;
        const track = data.items[0];
        if (!track?.video || videos.has(track.video.id)) return next();
        videos.add(track.video.id); playing = true; find('pause').disabled = false;
        find('status').textContent = t('playing', {current: cursor, total: queue.length, title: track.title});
        window.TrackYouTube.playSelected(track, track.video, next, () => {
          if (current !== generation) return;
          find('status').textContent = t('video_error'); next();
        }, panel);
      } catch (_) { if (current === generation) { stopPlayback(); find('status').textContent = t('check_failed'); } }
    }
    if (!queue.length) { find('status').textContent = t('no_videos'); return; }
    await next();
  }
  async function download() {
    cancel(); stopPlayback(); const current = generation, list = [...selected()];
    const destination = find('destination').value;
    find('export').disabled = true;
    try {
      await window.MusicFilters.refresh();
      if (current !== generation) return;
      request = new AbortController();
      const timer = setTimeout(() => request?.abort(), 25000);
      let response, blob;
      try {
        response = await fetch(`/api/playlists/export?${params()}`, {method: 'POST', cache: 'no-store', signal: request.signal, headers: {'Content-Type': 'application/json'}, body: JSON.stringify({items: payload(list), destination, format: 'csv'})});
        if (!response.ok) throw new Error(t('failed'));
        blob = await response.blob();
      } finally { clearTimeout(timer); }
      if (current !== generation || !panel.open) return;
      const url = URL.createObjectURL(blob), link = el('a'); link.href = url; link.download = `radio-playlist-${destination}.csv`;
      document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 60000);
      find('status').textContent = t('exported') + (Number(response.headers.get('X-Playlist-Skipped')) ? ' ' + t('skipped', {count: Number(response.headers.get('X-Playlist-Skipped'))}) : '');
    } catch (error) { if (current === generation) find('status').textContent = error.name === 'AbortError' ? t('failed') : error.message; }
    finally { if (current === generation) update(); }
  }
  async function open(asDaily = false) {
    daily = asDaily;
    const attempt = ++opening;
    panel?.close(); panel?.remove(); cancel(); stopPlayback();
    await window.MusicFilters.refresh().catch(() => {});
    if (attempt !== opening) return;
    activeScope = {...scope};
    panel = document.querySelector('#playlist-template').content.firstElementChild.cloneNode(true);
    rows = []; cancel();
    if (daily) { panel.querySelector('h2').textContent = window.RadioI18n.t('daily.open'); find('form').elements.sort.disabled = true; }
    find('scope').textContent = t('scope', {stations: activeScope.station_ids || t('all'), since: activeScope.since ? new Date(activeScope.since).toLocaleString(window.RadioI18n.locale) : t('beginning'), until: activeScope.until ? new Date(activeScope.until).toLocaleString(window.RadioI18n.locale) : t('now')});
    find('close').onclick = () => panel.close();
    find('pause').onclick = () => window.TrackYouTube.toggle();
    find('video-visible').checked = window.TrackYouTube.visible();
    find('video-visible').onchange = () => window.TrackYouTube.setVisible(find('video-visible').checked);
    find('form').onsubmit = prepare; find('resolve').onclick = resolve; find('play').onclick = play; find('export').onclick = download;
    find('stop').onclick = () => { cancel(); find('stop').hidden = true; find('status').textContent = t('stopped'); update(); };
    const opened = panel;
    panel.addEventListener('close', () => { if (panel !== opened) return; cancel(); stopPlayback(); rows = []; }, {once: true});
    window.RadioDialogs.dismissOutside(panel); document.body.append(panel); panel.showModal();
    if (daily) await prepare();
  }
  window.addEventListener('radio:filterschange', () => {
    cancel(); stopPlayback(); rows = [];
    if (panel?.open) { render(); find('stop').hidden = true; find('status').textContent = t('changed'); }
  });
  window.addEventListener('radio:audioactive', () => { cancel(); stopPlayback(); });
  window.addEventListener('radio:beforepagechange', () => { opening++; panel?.close(); });
  document.addEventListener('click', event => { if (event.target.closest('[data-open-playlist]')) open(); else if (event.target.closest('[data-open-daily]')) open(true); });
  return {configure(value) { const query = new URLSearchParams(value); scope = {}; for (const key of ['since', 'until']) if (query.has(key)) scope[key] = query.get(key); scope.station_ids = query.get('station_ids') || query.get('station_id') || ''; }};
})();
