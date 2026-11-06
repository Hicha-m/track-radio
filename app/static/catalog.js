window.TrackCatalog = (() => {
  let dialog;
  let serial = 0;
  window.addEventListener('radio:globalfilterschange', () => { serial++; dialog?.close(); });
  function el(tag, text = '', className = '') {
    const result = document.createElement(tag);
    result.textContent = text;
    result.className = className;
    return result;
  }
  function searchUrl(track) {
    const params = new URLSearchParams({view: 'plays', title: track.title, exact: 'true', sort: 'newest'});
    if (track.station_id) params.set('station_id', track.station_id);
    if (track.artist) params.set('artist', track.artist);
    else params.set('artist_status', 'missing');
    return `/recherche?${params}`;
  }
  function playButton(track, source = null) {
    const url = source || (track.metadata?.preview_url ? `/api/catalog/${track.metadata.track_id}/preview` : track.preview_url);
    const result = el('button', '▶', 'preview-button');
    result.type = 'button';
    result.dataset.previewKey = window.RadioPlayer.key(track, url || '');
    result.dataset.previewTitle = track.title;
    result.setAttribute('aria-label', window.RadioI18n.t("ui.933bfd50d39d", {p0: (track.title)}));
    result.setAttribute('aria-pressed', 'false');
    result.disabled = !url;
    result.title = url ? window.RadioI18n.t("ui.9e8d2e8f5781") : window.RadioI18n.t("ui.71b2e2a50b53");
    result.addEventListener('click', event => { event.stopPropagation(); window.RadioPlayer.preview(track, url); });
    queueMicrotask(() => window.RadioPlayer.sync());
    const controls = el('span', '', 'preview-actions'); controls.append(result, window.TrackYouTube.button(track));
    return controls;
  }
  function button(track) {
    const result = el('button', window.RadioI18n.t("ui.17eaee489b09"), 'catalog-button');
    result.type = 'button';
    result.setAttribute('aria-label', window.RadioI18n.t("ui.d523b64d2408", {p0: (track.title)}));
    result.addEventListener('click', () => open(track));
    return result;
  }
  function duration(seconds) {
    return Number.isFinite(seconds) ? `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}` : window.RadioI18n.t("ui.5c09e5cf2a99");
  }
  function artwork(url, title) {
    const img = el('img', '', 'catalog-art');
    img.src = url;
    img.alt = window.RadioI18n.t("ui.2c63fa996b93", {p0: (title)});
    img.addEventListener('error', () => { const fallback = el('div', '♫', 'catalog-art catalog-art-placeholder'); fallback.setAttribute('aria-label', window.RadioI18n.t("ui.6b3f26dae95e")); img.replaceWith(fallback); }, {once: true});
    return img;
  }
  function date(value) { return value ? new Date(value).toLocaleString((window.RadioI18n?.locale || 'fr-FR')) : window.RadioI18n.t("ui.831460cb0225"); }
  function external(label, url) {
    const link = el('a', label, 'platform-link'); link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer'; return link;
  }
  function fields(items) {
    const list = el('dl', '', 'catalog-fields');
    for (const [label, value] of items) { list.append(el('dt', label), el('dd', value || window.RadioI18n.t("ui.cb6c1fb76c7c"))); }
    return list;
  }
  function genreList(data) {
    const section = el('section', '', 'catalog-genres'); section.append(el('h3', window.RadioI18n.t("ui.3561abfe8067")));
    const genres = [...new Set((Array.isArray(data.genres) ? data.genres : []).filter(g => typeof g === 'string' && g.trim()).map(g => g.trim()))];
    if (!genres.length && data.primary_genre) genres.push(data.primary_genre);
    if (genres.length) {
      const list = el('ul'); genres.forEach(genre => list.append(el('li', genre))); section.append(list);
    } else section.append(el('p', window.RadioI18n.t("ui.8c77b0fc3b96"), 'hint'));
    return section;
  }
  async function open(track, options = {}) {
    const current = ++serial;
    if (dialog) { dialog.close(); dialog.remove(); }
    dialog = el('dialog', '', 'catalog-dialog');
    const panel = dialog;
    const close = el('button', '×', 'text-button catalog-close'); close.type = 'button'; close.setAttribute('aria-label', window.RadioI18n.t("ui.711e5f2e198d"));
    close.addEventListener('click', () => panel.close());
    window.RadioDialogs.dismissOutside(panel);
    const heading = el('h2', track.title, 'catalog-title'); heading.id = `catalog-title-${current}`;
    panel.setAttribute('aria-labelledby', heading.id);
    const artist = el('p', track.artist || window.RadioI18n.t("ui.133df509234a"), 'catalog-artist');
    const message = el('p', window.RadioI18n.t("ui.7bc9db6c0a32"), 'hint'); message.setAttribute('role', 'status');
    const content = el('div');
    const toolbar = el('div', '', 'catalog-toolbar');
    if (options.onBack) {
      const back = el('button', window.RadioI18n.t("ui.1079a835ea54"), 'text-button catalog-back'); back.type = 'button';
      back.onclick = () => { panel.close(); options.onBack(); }; toolbar.append(back);
    }
    const edit = el('button', window.RadioI18n.t("ui.42e37604b638"), 'text-button catalog-modify'); edit.type = 'button'; edit.hidden = true;
    toolbar.append(close);
    panel.append(toolbar, heading, artist, edit, message, content);
    const hide = el('button', window.RadioI18n.t("ui.81a8cb932481"), 'text-button'); hide.type = 'button';
    hide.onclick = async () => {
      const global = window.RadioAdmin.authenticated(); hide.disabled = true;
      try { await window.MusicFilters.hide(track, global); panel.close(); }
      catch (error) { message.textContent = error.message; }
      finally { hide.disabled = false; }
    };
    const footer = el('div', '', 'catalog-footer'); footer.append(hide); panel.append(footer);
    const active = () => current === serial && panel.isConnected && panel.open;
    function adminState() { edit.hidden = !window.RadioAdmin.authenticated(); hide.textContent = window.RadioAdmin.authenticated() ? window.RadioI18n.t("ui.f70b66d7770a") : window.RadioI18n.t("ui.23114e119215"); }
    window.addEventListener('radio:adminchange', adminState);
    panel.addEventListener('close', () => window.removeEventListener('radio:adminchange', adminState), {once: true});
    document.body.append(panel); panel.showModal();
    adminState();
    window.RadioAdmin.refresh().then(() => { if (active()) adminState(); });
    try {
      const params = new URLSearchParams({artist: track.artist || '', title: track.title});
      const response = await window.MusicFilters.fetch(`/api/catalog?${params}`);
      if (!response.ok) throw new Error(window.RadioI18n.t("ui.b7dfaba6f53b"));
      const data = await response.json();
      if (!active()) return;
      const providers = {deezer: window.RadioI18n.t("ui.4997d093758b"), itunes: window.RadioI18n.t("ui.3b00b32d64af"), spotify: window.RadioI18n.t("ui.7005c0064bda"), musicbrainz: 'MusicBrainz', lastfm: 'Last.fm'};
      const status = data.status === 'matched' ? data.manual ? window.RadioI18n.t("ui.64eb84a118ae") : window.RadioI18n.t("ui.89bd8db00289") : ({pending: window.RadioI18n.t("ui.ecc0cbc0269e"), ambiguous: window.RadioI18n.t("ui.20c99926dca1"), not_found: window.RadioI18n.t("ui.3067e21a1c87"), error: window.RadioI18n.t("ui.8c9c7a033df1"), rejected: window.RadioI18n.t("ui.3f67df0b751e")})[data.status] || window.RadioI18n.t("ui.cb6c1fb76c7c");
      heading.textContent = data.title || track.title; artist.textContent = data.artist || track.artist || window.RadioI18n.t("ui.133df509234a");
      message.textContent = status;
      edit.onclick = async () => {
        edit.disabled = true;
        try {
          const opened = await window.RadioAdmin.openEditor({...track, track_id: data.track_id}, {canOpen: active, onClose: () => open(track, options)});
          if (opened) panel.close(); else edit.disabled = false;
        } catch (error) { message.textContent = error.message; edit.disabled = false; }
      };
      const summary = el('section', '', 'catalog-summary');
      if (data.cover_url) summary.append(artwork(data.cover_url, heading.textContent));
      else { const placeholder = el('div', '♫', 'catalog-art catalog-art-placeholder'); placeholder.setAttribute('aria-label', window.RadioI18n.t("ui.6b3f26dae95e")); summary.append(placeholder); }
      const essentials = el('div', '', 'catalog-essentials');
      essentials.append(el('span', window.RadioI18n.t("ui.ec34e3485e5e"), 'eyebrow'), heading, artist);
      const controls = el('div', '', 'preview-entry');
      controls.append(playButton({...track, metadata: data}), el('span', data.preview_url ? window.RadioI18n.t("ui.ccc216fa2e6b") : window.RadioI18n.t("ui.71b2e2a50b53")));
      essentials.append(controls);
      essentials.append(fields([[window.RadioI18n.t("ui.f05e840e0069"), data.album], [window.RadioI18n.t("ui.71d0bad42af1"), duration(data.duration)], [window.RadioI18n.t("ui.d6c7b22ccab5"), providers[data.provider] || data.provider || window.RadioI18n.t("ui.831460cb0225")]]));
      essentials.append(genreList(data), message, edit); summary.append(essentials); content.append(summary);
      const radio = el('section', '', 'catalog-radio'); radio.append(el('h3', window.RadioI18n.t("ui.a5ab437b560e")));
      const radioStatus = el('p', window.RadioI18n.t("ui.e539ce5cf335"), 'hint'); radio.append(radioStatus); content.append(radio);
      const radioParams = window.MusicFilters.apply(new URLSearchParams()); if (track.station_id) radioParams.set('station_id', track.station_id);
      window.MusicFilters.fetch(`/api/catalog/${data.track_id}/radio?${radioParams}`).then(async response => {
        if (!response.ok) throw new Error(window.RadioI18n.t("ui.9ae2a14543b2")); return response.json();
      }).then(history => {
        if (!active()) return;
        radioStatus.textContent = window.RadioI18n.t("ui.799865de7cdd", {p0: (track.station_id ? window.RadioI18n.t("ui.f2d5a938d8da") : window.RadioI18n.t("ui.e1d58522c2fa"))});
        const counts = el('div', '', 'catalog-counts');
        for (const [label, value] of [[window.RadioI18n.t("ui.0d9b4cc2f95f"), history.detections], [window.RadioI18n.t("ui.745a8ec30dfa"), history.stations], [window.RadioI18n.t("ui.cdefb32a3d84"), date(history.first_at)], [window.RadioI18n.t("ui.fceee0a4da07"), date(history.last_at)]]) {
          const item = el('div'); item.append(el('strong', String(value)), el('span', label)); counts.append(item);
        }
        radio.append(counts, el('h4', window.RadioI18n.t("ui.572f3e7575ec")));
        const recent = el('ul', '', 'catalog-recent');
        for (const row of history.recent) { const item = el('li'); item.append(el('strong', row.station_name), el('time', date(row.detected_at))); recent.append(item); }
        if (!history.recent.length) recent.append(el('li', window.RadioI18n.t("ui.67a199a6f0d4")));
        const search = el('a', window.RadioI18n.t("ui.21c2eff8341a"), 'catalog-search-link'); search.href = searchUrl(track);
        radio.append(recent, search);
      }).catch(error => { if (active()) radioStatus.textContent = error.message; });
      if (options.showHourly && location.pathname === '/stats') {
        const hourly = el('section', '', 'catalog-hourly'); hourly.append(el('h3', window.RadioI18n.t("ui.2d08bef8bb21"))); content.append(hourly);
        const hourlyParams = window.MusicFilters.apply(new URLSearchParams({track_id: data.track_id, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'}));
        if (track.station_id) hourlyParams.set('station_id', track.station_id);
        window.MusicFilters.fetch(`/api/stats/hourly?${hourlyParams}`).then(async response => {
          if (!response.ok) throw new Error(window.RadioI18n.t("ui.1f2d5955a4d6")); return response.json();
        }).then(activity => {
          if (!active()) return;
          hourly.append(window.RadioMetrics.hourly(activity.items));
          hourly.append(el('p', window.RadioI18n.t("ui.e32f8bcfd480", {p0: (new Date(activity.since).toLocaleDateString((window.RadioI18n?.locale || 'fr-FR'))), p1: (new Date(activity.until).toLocaleDateString((window.RadioI18n?.locale || 'fr-FR'))), p2: (activity.timezone)}), 'hint'));
        }).catch(error => { if (active()) hourly.append(el('p', error.message, 'hint')); });
      }
      const technical = el('details', '', 'catalog-technical'); technical.append(el('summary', window.RadioI18n.t("ui.85c83b0ea991")));
      technical.append(fields([[window.RadioI18n.t("ui.d3385af48774"), data.title], [window.RadioI18n.t("ui.0677337c64c2"), data.artist], [window.RadioI18n.t("ui.0df5f71b2a70"), (data.featured_artists || []).join(', ')], [window.RadioI18n.t("ui.f05e840e0069"), data.album], [window.RadioI18n.t("ui.71d0bad42af1"), duration(data.duration)], [window.RadioI18n.t("ui.d29a31ee7a18"), data.release_date ? data.release_date.slice(0, 10) : null], ['ISRC', data.isrc], [window.RadioI18n.t("ui.08e6d9c4d8fa"), providers[data.provider] || data.provider], [window.RadioI18n.t("ui.dee377cfd8cd"), status], [window.RadioI18n.t("ui.f9e7d49e4745"), data.recording_id]]));
      if (data.title && data.title !== track.title) technical.append(el('p', window.RadioI18n.t("ui.e388e5a49fe5", {p0: (track.title)}), 'hint'));
      if (data.field_sources) {
        const labels = {genres: window.RadioI18n.t("ui.3561abfe8067"), album: window.RadioI18n.t("ui.f05e840e0069"), release_date: window.RadioI18n.t("ui.d29a31ee7a18"), featured_artists: window.RadioI18n.t("ui.ea68f511d443")};
        technical.append(fields([[window.RadioI18n.t("ui.1ee7e1cacc21"), Object.entries(data.field_sources).map(([field, provider]) => `${labels[field] || field} : ${providers[provider] || provider}`).join(' · ')]]));
      }
      content.append(technical);
      const lyrics = el('details', '', 'catalog-lyrics'); lyrics.append(el('summary', window.RadioI18n.t("ui.3aeca3ed77f2")));
      if (data.lyrics) {
        lyrics.append(el('pre', data.lyrics.plain || data.lyrics.synced || window.RadioI18n.t("ui.05082dc1436b"), 'lyrics'));
        const ovh = data.lyrics.provider === 'lyricsovh';
        lyrics.append(external(window.RadioI18n.t("ui.e865d7b30df3", {p0: (ovh ? 'lyrics.ovh' : 'LRCLIB')}), ovh ? 'https://lyrics.ovh' : 'https://lrclib.net'));
      } else {
        lyrics.append(el('p', data.lyrics_status === 'pending' ? window.RadioI18n.t("ui.5c82ec98c1c7") : data.lyrics_status === 'error' ? window.RadioI18n.t("ui.a563c478fafa") : window.RadioI18n.t("ui.9eb1c7a60272"), 'hint'));
      }
      content.append(lyrics);
      const platforms = el('details', '', 'catalog-platforms'); platforms.append(el('summary', window.RadioI18n.t("ui.6a4faac878b3")));
      const links = el('div', '', 'platform-links');
      for (const target of window.TrackPlatforms.links(data, track)) {
        const link = external(target.label, target.url);
        if (target.appUrl) {
          link.removeAttribute('target');
          link.addEventListener('click', event => {
            if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
            event.preventDefault(); window.TrackPlatforms.openWithFallback(target);
          });
        }
        links.append(link);
      }
      platforms.append(el('p', window.RadioI18n.t("ui.e30de89e4938"), 'hint'));
      platforms.append(links); content.append(platforms);
      window.TrackYouTube.mount(panel, content, links, data.track_id, {...track, metadata: data}, controls, options.youtube);
    } catch (error) { if (active()) message.textContent = error.message; }
  }
  return {button, open, playButton, searchUrl, genreList};
})();
