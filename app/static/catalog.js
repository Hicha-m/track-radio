window.TrackCatalog = (() => {
  let dialog;
  let serial = 0;
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
    result.setAttribute('aria-label', `Écouter un extrait de ${track.title}`);
    result.setAttribute('aria-pressed', 'false');
    result.disabled = !url;
    result.title = url ? 'Écouter un extrait' : 'Aucun extrait disponible';
    result.addEventListener('click', event => { event.stopPropagation(); window.RadioPlayer.preview(track, url); });
    queueMicrotask(() => window.RadioPlayer.sync());
    return result;
  }
  function button(track) {
    const result = el('button', 'Détails', 'catalog-button');
    result.type = 'button';
    result.setAttribute('aria-label', `Détails de ${track.title}`);
    result.addEventListener('click', () => open(track));
    return result;
  }
  function duration(seconds) {
    return Number.isFinite(seconds) ? `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}` : 'Durée indisponible';
  }
  function artwork(url, title) {
    const img = el('img', '', 'catalog-art');
    img.src = url;
    img.alt = `Pochette de ${title}`;
    img.addEventListener('error', () => { const fallback = el('div', '♫', 'catalog-art catalog-art-placeholder'); fallback.setAttribute('aria-label', 'Pochette indisponible'); img.replaceWith(fallback); }, {once: true});
    return img;
  }
  function date(value) { return value ? new Date(value).toLocaleString('fr-FR') : 'Non renseignée'; }
  function external(label, url) {
    const link = el('a', label, 'platform-link'); link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer'; return link;
  }
  function fields(items) {
    const list = el('dl', '', 'catalog-fields');
    for (const [label, value] of items) { list.append(el('dt', label), el('dd', value || 'Non renseigné')); }
    return list;
  }
  async function open(track, options = {}) {
    const current = ++serial;
    if (dialog) { dialog.close(); dialog.remove(); }
    dialog = el('dialog', '', 'catalog-dialog');
    const panel = dialog;
    const close = el('button', '×', 'text-button catalog-close'); close.type = 'button'; close.setAttribute('aria-label', 'Fermer');
    close.addEventListener('click', () => panel.close());
    window.RadioDialogs.dismissOutside(panel);
    const heading = el('h2', track.title, 'catalog-title'); heading.id = `catalog-title-${current}`;
    panel.setAttribute('aria-labelledby', heading.id);
    const artist = el('p', track.artist || 'Artiste inconnu', 'catalog-artist');
    const message = el('p', 'Chargement des informations…', 'hint'); message.setAttribute('role', 'status');
    const content = el('div');
    if (options.onBack) {
      const back = el('button', '← Retour à la recherche', 'text-button catalog-back'); back.type = 'button';
      back.onclick = () => { panel.close(); options.onBack(); }; panel.append(back);
    }
    const edit = el('button', 'Modifier', 'text-button catalog-modify'); edit.type = 'button'; edit.hidden = true;
    panel.append(close, heading, artist, edit, message, content);
    const hide = el('button', 'Masquer ce titre', 'text-button'); hide.type = 'button';
    hide.onclick = () => { try { window.MusicFilters.hide(track); panel.close(); } catch (error) { message.textContent = error.message; } };
    const footer = el('div', '', 'catalog-footer'); footer.append(hide); panel.append(footer);
    const active = () => current === serial && panel.isConnected && panel.open;
    function adminState() { edit.hidden = !window.RadioAdmin.authenticated(); }
    window.addEventListener('radio:adminchange', adminState);
    panel.addEventListener('close', () => window.removeEventListener('radio:adminchange', adminState), {once: true});
    document.body.append(panel); panel.showModal();
    window.RadioAdmin.refresh().then(() => { if (active()) adminState(); });
    try {
      const params = new URLSearchParams({artist: track.artist || '', title: track.title});
      const response = await fetch(`/api/catalog?${params}`);
      if (!response.ok) throw new Error('Informations indisponibles.');
      const data = await response.json();
      if (!active()) return;
      const providers = {deezer: 'Deezer', itunes: 'Apple Music', spotify: 'Spotify'};
      const status = data.status === 'matched' ? data.manual ? 'Vérifié' : 'Automatique' : ({pending: 'En cours de recherche', ambiguous: 'Correspondance à vérifier', not_found: 'Correspondance absente', error: 'Catalogue temporairement indisponible', rejected: 'Correspondance retirée'})[data.status] || 'Non renseigné';
      heading.textContent = data.title || track.title; artist.textContent = data.artist || track.artist || 'Artiste inconnu';
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
      else { const placeholder = el('div', '♫', 'catalog-art catalog-art-placeholder'); placeholder.setAttribute('aria-label', 'Pochette indisponible'); summary.append(placeholder); }
      const essentials = el('div', '', 'catalog-essentials');
      if (data.primary_genre) essentials.append(el('p', data.primary_genre, 'catalog-genre'));
      essentials.append(fields([['Album', data.album], ['Durée', duration(data.duration)], ['Source principale', providers[data.provider] || data.provider || 'Non renseignée']]));
      const controls = el('div', '', 'preview-entry');
      controls.append(playButton({...track, metadata: data}), el('span', 'Écouter l’extrait'));
      essentials.append(message, controls); summary.append(essentials); content.append(summary);
      const radio = el('section', '', 'catalog-radio'); radio.append(el('h3', 'Diffusions radio'));
      const radioStatus = el('p', 'Chargement des détections…', 'hint'); radio.append(radioStatus); content.append(radio);
      const radioParams = new URLSearchParams(); if (track.station_id) radioParams.set('station_id', track.station_id);
      fetch(`/api/catalog/${data.track_id}/radio?${radioParams}`).then(async response => {
        if (!response.ok) throw new Error('Historique radio indisponible.'); return response.json();
      }).then(history => {
        if (!active()) return;
        radioStatus.textContent = `Détections collectées${track.station_id ? ' · radio sélectionnée' : ' · toutes les radios'}. Elles ne représentent pas nécessairement des diffusions distinctes.`;
        const counts = el('div', '', 'catalog-counts');
        for (const [label, value] of [['Détections', history.detections], ['Radios', history.stations], ['Première détection', date(history.first_at)], ['Dernière détection', date(history.last_at)]]) {
          const item = el('div'); item.append(el('strong', String(value)), el('span', label)); counts.append(item);
        }
        radio.append(counts, el('h4', 'Dernières détections'));
        const recent = el('ul', '', 'catalog-recent');
        for (const row of history.recent) { const item = el('li'); item.append(el('strong', row.station_name), el('time', date(row.detected_at))); recent.append(item); }
        if (!history.recent.length) recent.append(el('li', 'Aucune détection conservée.'));
        const search = el('a', 'Voir l’historique complet', 'catalog-search-link'); search.href = searchUrl(track);
        radio.append(recent, search);
      }).catch(error => { if (active()) radioStatus.textContent = error.message; });
      if (options.showHourly && location.pathname === '/stats') {
        const hourly = el('section', '', 'catalog-hourly'); hourly.append(el('h3', 'Heures de diffusion · 7 jours')); content.append(hourly);
        const hourlyParams = window.MusicFilters.apply(new URLSearchParams({track_id: data.track_id, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'}));
        if (track.station_id) hourlyParams.set('station_id', track.station_id);
        fetch(`/api/stats/hourly?${hourlyParams}`).then(async response => {
          if (!response.ok) throw new Error('Activité horaire indisponible.'); return response.json();
        }).then(activity => {
          if (!active()) return;
          hourly.append(window.RadioMetrics.hourly(activity.items));
          hourly.append(el('p', `Du ${new Date(activity.since).toLocaleDateString('fr-FR')} au ${new Date(activity.until).toLocaleDateString('fr-FR')} · ${activity.timezone}`, 'hint'));
        }).catch(error => { if (active()) hourly.append(el('p', error.message, 'hint')); });
      }
      const technical = el('details', '', 'catalog-technical'); technical.append(el('summary', 'Métadonnées et provenance'));
      technical.append(fields([['Titre officiel', data.title], ['Artiste principal', data.artist], ['Artistes invités', (data.featured_artists || []).join(', ')], ['Album', data.album], ['Durée', duration(data.duration)], ['Genre principal', data.primary_genre], ['Genres secondaires', (data.genres || []).slice(1).join(', ')], ['Source des genres', providers[data.genre_source] || data.genre_source], ['Date de sortie', data.release_date ? data.release_date.slice(0, 10) : null], ['ISRC', data.isrc], ['Fournisseur', providers[data.provider] || data.provider], ['Statut', status], ['Identifiant', data.recording_id]]));
      if (data.title && data.title !== track.title) technical.append(el('p', `Titre collecté : ${track.title}`, 'hint'));
      if (data.field_sources) {
        const labels = {genres: 'Genres', album: 'Album', release_date: 'Date de sortie', featured_artists: 'Invités'};
        technical.append(fields([['Provenance des champs', Object.entries(data.field_sources).map(([field, provider]) => `${labels[field] || field} : ${providers[provider] || provider}`).join(' · ')]]));
      }
      content.append(technical);
      const lyrics = el('details', '', 'catalog-lyrics'); lyrics.append(el('summary', 'Paroles'));
      if (data.lyrics) {
        lyrics.append(el('pre', data.lyrics.plain || data.lyrics.synced || 'Instrumental', 'lyrics'));
        lyrics.append(external('Source des paroles : LRCLIB', 'https://lrclib.net'));
      } else {
        lyrics.append(el('p', data.lyrics_status === 'pending' ? 'Paroles en cours de recherche' : data.lyrics_status === 'error' ? 'Service de paroles temporairement indisponible' : 'Paroles non disponibles', 'hint'));
      }
      content.append(lyrics);
      const platforms = el('details', '', 'catalog-platforms'); platforms.append(el('summary', 'Écouter sur une plateforme'));
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
      platforms.append(el('p', 'Spotify s’ouvre dans l’application, ou sur le web après une seconde si la page reste visible.', 'hint'));
      platforms.append(links); content.append(platforms);
    } catch (error) { if (active()) message.textContent = error.message; }
  }
  return {button, open, playButton, searchUrl};
})();
