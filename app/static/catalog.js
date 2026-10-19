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
    img.addEventListener('error', () => img.remove(), {once: true});
    return img;
  }
  function preview(url, track) {
    const controls = el('div', '', 'preview-entry');
    controls.append(playButton(track, url), el('span', 'Écouter l’extrait'));
    return controls;
  }
  async function open(track, options = {}) {
    const current = ++serial;
    if (dialog) { dialog.close(); dialog.remove(); }
    dialog = el('dialog', '', 'catalog-dialog');
    const panel = dialog;
    const close = el('button', 'Fermer', 'text-button catalog-close');
    close.type = 'button';
    close.addEventListener('click', () => panel.close());
    window.RadioDialogs.dismissOutside(panel);
    const heading = el('h2', track.title);
    const headingId = `catalog-title-${current}`;
    heading.id = headingId;
    panel.setAttribute('aria-labelledby', headingId);
    const content = el('div');
    const message = el('p', 'Chargement des informations…', 'hint');
    message.setAttribute('role', 'status');
    const search = el('a', 'Voir les diffusions de ce titre', 'catalog-search-link');
    search.href = searchUrl(track);
    if (options.onBack) {
      const back = el('button', '← Retour à la recherche', 'text-button catalog-back'); back.type = 'button';
      back.onclick = () => { panel.close(); options.onBack(); };
      panel.append(back);
    }
    panel.append(close, heading, el('p', track.artist || 'Artiste inconnu'), message, search, content);
    const spotify = el('a', 'Rechercher sur Spotify', 'catalog-search-link');
    spotify.href = `https://open.spotify.com/search/${encodeURIComponent(`${track.artist || ''} ${track.title}`.trim())}`;
    spotify.target = '_blank'; spotify.rel = 'noopener noreferrer';
    const hide = el('button', 'Masquer ce titre', 'text-button'); hide.type = 'button';
    hide.onclick = () => { try { window.MusicFilters.hide(track); panel.close(); } catch (error) { message.textContent = error.message; } };
    const spotifyStatus = el('p', '', 'hint');
    const footer = el('div', '', 'catalog-footer');
    footer.append(hide);
    panel.append(spotify, spotifyStatus, footer);
    const spotifyParams = new URLSearchParams({artist: track.artist || '', title: track.title});
    fetch(`/api/catalog/spotify-match?${spotifyParams}`).then(response => response.ok ? response.json() : null).then(result => {
      if (current !== serial || !result) return;
      if (result.track?.url) {
        spotify.href = result.track.url;
        spotify.textContent = `Premier résultat Spotify : ${result.track.title} · ${result.track.artist}`;
      } else if (result.status === 'unavailable') {
        spotifyStatus.textContent = 'Lien direct Spotify indisponible. La recherche sur Spotify reste accessible.';
      }
    }).catch(() => {});
    document.body.append(panel);
    panel.showModal();
    try {
      const params = new URLSearchParams({artist: track.artist || '', title: track.title});
      const response = await fetch(`/api/catalog?${params}`);
      if (!response.ok) throw new Error('Informations indisponibles.');
      const data = await response.json();
      if (current !== serial) return;
      const states = {pending: 'En attente de collecte', matched: 'Correspondance trouvée', ambiguous: 'Plusieurs versions : validation nécessaire', not_found: 'Aucune correspondance trouvée', error: 'Fournisseur temporairement indisponible', rejected: 'Correspondance retirée manuellement'};
      message.textContent = `${states[data.status] || data.status}${data.manual ? ' · Choix manuel conservé' : ''}`;
      if (data.cover_url) content.append(artwork(data.cover_url, data.title));
      if (data.provider_id) {
        content.append(el('p', `${data.artist} · ${data.title} · ${duration(data.duration)}`));
        if (data.preview_url) content.append(preview(`/api/catalog/${data.track_id}/preview`, {...track, metadata: data}));
        else content.append(el('p', 'Aucun extrait disponible pour cette version.'));
        if (data.url) {
          const link = el('a', `Ouvrir sur ${({itunes: 'Apple Music', deezer: 'Deezer', spotify: 'Spotify'})[data.provider] || data.provider}`, 'cell-link');
          link.href = data.url;
          link.target = '_blank';
          link.rel = 'noopener noreferrer';
          content.append(link);
        }
        content.append(el('p', `Identifiant : ${data.recording_id}`, 'hint'));
      }
      if (data.lyrics) {
        const details = el('details');
        details.append(el('summary', 'Paroles · LRCLIB'), el('pre', data.lyrics.plain || data.lyrics.synced || 'Instrumental', 'lyrics'));
        content.append(details);
      } else content.append(el('p', 'Paroles non trouvées ou non validées.', 'hint'));
      if (data.candidates?.length && data.status !== 'matched') {
        content.append(el('p', `${data.candidates.length} propositions à vérifier :`, 'hint'));
        data.candidates.forEach(c => content.append(el('p', `${c.artist} — ${c.title} · ${duration(c.duration)}`)));
      }
      content.append(editor(track, data, message));
    } catch (error) { if (current === serial) message.textContent = error.message; }
  }
  function editor(track, metadata, message) {
    const details = el('details', '', 'catalog-editor');
    details.append(el('summary', 'Corriger les informations (administration)'));
    const form = el('form');
    function field(text, input) {
      const label = el('label', text);
      label.append(input);
      form.append(label);
      return input;
    }
    const tokenInput = el('input');
    tokenInput.type = 'password';
    tokenInput.autocomplete = 'off';
    const token = field('Clé administrateur', tokenInput);
    const provider = field('Fournisseur', el('select'));
    [['deezer', 'Deezer'], ['itunes', 'Apple Music (iTunes)'], ['spotify', 'Spotify'], ['lrclib', 'Paroles (LRCLIB)']].forEach(([value, label]) => {
      const option = el('option', label);
      option.value = value;
      provider.append(option);
    });
    const artist = field('Artiste recherché', el('input'));
    artist.value = track.artist || '';
    artist.maxLength = 300;
    const title = field('Titre recherché', el('input'));
    title.value = track.title;
    title.maxLength = 500;
    title.required = true;
    const search = el('button', 'Rechercher des propositions', 'hero-button');
    search.type = 'submit';
    form.append(search);
    const candidates = el('div');
    const feedback = el('p', '', 'hint');
    feedback.setAttribute('role', 'status');
    let busy = false;
    async function call(action, payload) {
      if (!token.value) throw new Error('Renseignez la clé administrateur du serveur.');
      const response = await fetch(`/api/catalog/${metadata.track_id}/${action}`, {
        method: 'POST', headers: {'Content-Type': 'application/json', Authorization: `Bearer ${token.value}`},
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Correction impossible.');
      return data;
    }
    async function apply(payload) {
      if (busy) return;
      busy = true;
      try {
        await call('correction', payload);
        feedback.textContent = 'Correction enregistrée. Rouvrez les détails pour la consulter.';
        message.textContent = 'Informations mises à jour · actualisez le classement pour les afficher.';
      } catch (error) { feedback.textContent = error.message; }
      finally { busy = false; }
    }
    function choices(items, lyrics = false) {
      candidates.replaceChildren();
      items.forEach(c => {
        const row = el('article', '', 'candidate');
        if (c.cover_url) row.append(artwork(c.cover_url, c.title));
        row.append(el('p', `${c.artist} — ${c.title} · ${c.album || ''} · ${duration(c.duration)}`));
        if (c.preview_url) row.append(preview(c.preview_url, c));
        if (lyrics) row.append(el('pre', c.plain || c.synced || 'Instrumental', 'lyrics'));
        const choose = el('button', 'Choisir cette version', 'text-button');
        choose.type = 'button';
        choose.addEventListener('click', () => apply({target: lyrics ? 'lyrics' : 'recording', candidate_id: lyrics ? c.id : `${c.provider}:${c.provider_id}`}));
        row.append(choose);
        candidates.append(row);
      });
    }
    choices(metadata.candidates || []);
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (busy) return;
      busy = true;
      search.disabled = true;
      feedback.textContent = 'Recherche chez le fournisseur…';
      const selectedProvider = provider.value;
      try {
        const data = await call('search', {provider: selectedProvider, artist: artist.value, title: title.value});
        choices(data.candidates, selectedProvider === 'lrclib');
        feedback.textContent = data.candidates.length ? 'Vérifiez la version et écoutez l’extrait avant de choisir.' : 'Aucun résultat. Modifiez la recherche ou le fournisseur.';
      } catch (error) { feedback.textContent = error.message; }
      finally { busy = false; search.disabled = false; }
    });
    const reject = el('button', 'Retirer la correspondance', 'text-button');
    reject.type = 'button';
    reject.addEventListener('click', () => apply({candidate_id: null}));
    const automatic = el('button', 'Relancer la recherche automatique', 'text-button');
    automatic.type = 'button';
    automatic.addEventListener('click', () => apply({automatic: true}));
    details.append(form, feedback, candidates, reject, automatic);
    return details;
  }
  return {button, open, playButton, searchUrl};
})();
