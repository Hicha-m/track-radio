window.RadioPages = window.RadioPages || {};
(() => {
  const el = window.RadioMetrics.el;
  const states = {pending: 'En attente', matched: 'Trouvé', not_found: 'Introuvable', ambiguous: 'À vérifier', error: 'Erreur temporaire', rejected: 'Retiré manuellement'};
  const state = value => states[value] || value;
  function duration(seconds) { return Number.isFinite(seconds) ? `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}` : 'Durée inconnue'; }
  function artwork(url, title) {
    const img = el('img', '', 'catalog-art'); img.src = url; img.alt = `Pochette de ${title}`;
    img.onerror = () => img.remove(); return img;
  }
  function preview(url, track) { return window.TrackCatalog.playButton(track, url); }
  function editor(track, metadata, message, request) {
    const details = el('details', '', 'catalog-editor');
    details.append(el('summary', 'Corriger les informations (administration)'));
    const genres = el('div');
    const renderGenres = () => genres.replaceChildren(window.TrackCatalog.genreList(metadata));
    renderGenres();
    const form = el('form');
    function field(text, input) {
      const label = el('label', text);
      label.append(input);
      form.append(label);
      return input;
    }
    const provider = field('Fournisseur', el('select'));
    [['deezer', 'Deezer'], ['itunes', 'Apple Music (iTunes)'], ['spotify', 'Spotify'], ['musicbrainz', 'MusicBrainz'], ['lastfm', 'Last.fm'], ['lrclib', 'Paroles (LRCLIB)'], ['lyricsovh', 'Paroles (lyrics.ovh)']].forEach(([value, label]) => {
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
    const pageSize = field('Résultats par page', el('select'));
    [10, 15, 25].forEach(value => { const option = el('option', String(value)); option.value = String(value); pageSize.append(option); });
    pageSize.value = '15'; pageSize.parentElement.hidden = true;
    form.append(search);
    const paging = el('nav', '', 'pagination'); paging.setAttribute('aria-label', 'Pages des propositions'); paging.hidden = true;
    const previous = el('button', '← Précédent', 'text-button'); previous.type = 'button';
    const pageLabel = el('span', 'Page 1');
    const next = el('button', 'Suivant →', 'text-button'); next.type = 'button';
    paging.append(previous, pageLabel, next);
    let searchPage = 1; let hasMore = false; let searchSnapshot = null;
    const paginated = () => ['lastfm', 'musicbrainz'].includes(provider.value);
    const lyricsProvider = value => ['lrclib', 'lyricsovh'].includes(value);
    function invalidatePaging() { paging.hidden = true; previous.disabled = next.disabled = true; }
    provider.onchange = () => { pageSize.parentElement.hidden = !paginated(); invalidatePaging(); };
    [artist, title, pageSize].forEach(input => input.addEventListener('input', invalidatePaging));
    const candidates = el('div');
    const feedback = el('p', '', 'hint');
    feedback.setAttribute('role', 'status');
    let busy = false;
    let completers = [];
    const fieldLabels = {genres: 'genres', album: 'album', release_date: 'date de sortie', featured_artists: 'artistes invités', duration: 'durée', cover_url: 'pochette', preview_url: 'extrait', isrc: 'ISRC', url: 'lien du fournisseur', additional_sources: 'lien du fournisseur'};
    function updateMetadata(data) {
      metadata = {...metadata, ...data, genres: data.genres || []}; renderGenres(); refreshCompleters();
    }
    function refreshCompleters() {
      for (const {candidate, container} of completers) {
        container.replaceChildren();
        const id = `${candidate.provider}:${candidate.provider_id}`;
        const fields = metadata.candidate_completions?.[id] || [];
        if (!fields.length) continue;
        const complete = el('button', 'Compléter les informations manquantes', 'text-button'); complete.type = 'button';
        complete.title = `Ajouter : ${fields.map(f => fieldLabels[f] || f).join(', ')}`;
        complete.onclick = async () => {
          if (busy) return;
          busy = true; complete.disabled = true;
          feedback.textContent = 'Complétion depuis cette proposition…';
          try {
            const data = await call('complete-candidate', {candidate_id: id, revision: metadata.revision});
            updateMetadata(data);
            const labels = data.completion.filled.map(f => fieldLabels[f] || f);
            feedback.textContent = labels.length ? `Informations complétées : ${labels.join(', ')}.` : 'Aucune information manquante à ajouter.';
            message.textContent = 'Informations mises à jour · les valeurs déjà renseignées ont été conservées.';
          } catch (error) { feedback.textContent = error.message; complete.disabled = false; }
          finally { busy = false; }
        };
        container.append(complete);
      }
    }
    async function call(action, payload) {
      const data = await request(`/api/catalog/${metadata.track_id}/${action}`, payload);
      return data;
    }
    async function apply(payload, responsePane = null) {
      if (busy) return;
      busy = true;
      try {
        const response = await call('correction', payload);
        if (responsePane) responsePane.textContent = JSON.stringify(response, null, 2);
        updateMetadata(response);
        feedback.textContent = payload.automatic
          ? response.relaunch === 'complement'
            ? 'Recherche de compléments mise en file chez les autres fournisseurs disponibles. La version choisie est conservée. Rouvrez la fiche après le traitement.'
            : 'Recherche automatique mise en file. Rouvrez la fiche après le traitement.'
          : 'Correction enregistrée. Rouvrez les détails pour la consulter.';
        message.textContent = payload.automatic ? 'Recherche en arrière-plan · les informations déjà renseignées sont conservées.' : 'Informations mises à jour · actualisez le classement pour les afficher.';
      } catch (error) { feedback.textContent = error.message; }
      finally { busy = false; }
    }
    function choices(items, lyrics = false) {
      candidates.replaceChildren(); completers = [];
      items.forEach(c => {
        const row = el('article', '', 'candidate candidate-layout');
        const presentation = el('div', '', 'candidate-presentation');
        if (c.cover_url) presentation.append(artwork(c.cover_url, c.title));
        presentation.append(el('p', `${c.artist} — ${c.title} · ${c.album || ''} · ${duration(c.duration)}`));
        if (c.preview_url && !lyrics) {
          const url = `/api/catalog/${metadata.track_id}/candidates/${encodeURIComponent(`${c.provider}:${c.provider_id}`)}/preview`;
          presentation.append(preview(url, c));
        }
        if (!lyrics) presentation.append(window.TrackCatalog.genreList(c));
        if (lyrics) {
          const absent = c.lyrics_status === 'not_found' ? 'Paroles non disponibles pour cette suggestion.' : c.lyrics_status === 'error' ? 'Service de paroles temporairement indisponible.' : 'Chargez les paroles pour vérifier cette suggestion.';
          presentation.append(el('pre', c.plain || c.synced || (c.instrumental ? 'Instrumental' : absent), 'lyrics'));
          if (c.provider === 'lyricsovh' && !c.plain && c.lyrics_status !== 'not_found') {
            const load = el('button', c.lyrics_status === 'error' ? 'Réessayer les paroles' : 'Charger les paroles', 'text-button'); load.type = 'button';
            load.onclick = () => enrichCandidate('candidate-lyrics', c.id, true);
            presentation.append(load);
          }
        }
        if (!lyrics && c.provider === 'musicbrainz' && c.release_id && !c.cover_url) {
          if (c.cover_status === 'not_found') presentation.append(el('p', 'Pochette indisponible pour cette édition.', 'hint'));
          else {
            const load = el('button', c.cover_status === 'error' ? 'Réessayer la pochette' : 'Charger la pochette', 'text-button'); load.type = 'button';
            load.onclick = () => enrichCandidate('candidate-artwork', `${c.provider}:${c.provider_id}`, false);
            presentation.append(load);
          }
        }
        const response = el('section', '', 'candidate-response');
        const responsePane = el('pre', JSON.stringify(c, null, 2));
        response.append(el('h4', 'Réponse de l’API'), responsePane);
        const choose = el('button', 'Choisir cette version', 'text-button');
        choose.type = 'button';
        choose.disabled = lyrics && !c.plain && !c.synced && !c.instrumental;
        choose.addEventListener('click', () => apply({target: lyrics ? 'lyrics' : 'recording', candidate_id: lyrics ? c.id : `${c.provider}:${c.provider_id}`}, responsePane));
        presentation.append(choose);
        if (!lyrics) {
          const container = el('div', '', 'candidate-completion');
          presentation.append(container); completers.push({candidate: c, container});
        }
        row.append(presentation, response);
        candidates.append(row);
      });
      refreshCompleters();
    }
    choices(metadata.candidates || []);
    const lyricChoices = el('button', 'Voir les propositions de paroles enregistrées', 'text-button');
    lyricChoices.type = 'button';
    lyricChoices.onclick = () => { invalidatePaging(); choices(metadata.lyrics_candidates || [], true); };

    async function enrichCandidate(action, candidateId, lyrics) {
      if (busy) return;
      busy = true;
      feedback.textContent = lyrics ? 'Recherche des paroles…' : 'Recherche de la pochette…';
      try {
        const data = await call(action, {candidate_id: candidateId, revision: metadata.revision});
        metadata = {...metadata, revision: data.revision, candidate_completions: data.candidate_completions,
          [lyrics ? 'lyrics_candidates' : 'candidates']: data.candidates};
        choices(data.candidates, lyrics);
        const status = lyrics ? data.lyrics_status : data.artwork_status;
        feedback.textContent = status === 'matched' ? (lyrics ? 'Paroles chargées. Vérifiez-les avant de choisir.' : 'Pochette chargée depuis cette édition.') : status === 'error' ? 'Fournisseur temporairement indisponible. Vous pouvez réessayer ou choisir une autre source.' : (lyrics ? 'Aucune parole disponible pour cette suggestion.' : 'Aucune pochette disponible pour cette édition.');
      } catch (error) { feedback.textContent = error.message; }
      finally { busy = false; }
    }
    async function searchCandidates(page = 1) {
      if (busy) return;
      const snapshot = page === 1 ? {provider: provider.value, artist: artist.value, title: title.value, limit: Number(pageSize.value)} : searchSnapshot;
      if (!snapshot) return;
      busy = true; search.disabled = previous.disabled = next.disabled = true;
      [provider, artist, title, pageSize].forEach(input => { input.disabled = true; });
      feedback.textContent = 'Recherche chez le fournisseur…';
      try {
        const data = await call('search', {...snapshot, page});
        searchSnapshot = snapshot; searchPage = data.page || 1; hasMore = !!data.has_more;
        const lyrics = lyricsProvider(snapshot.provider);
        metadata = {...metadata, revision: data.revision, candidate_completions: data.candidate_completions,
          [lyrics ? 'lyrics_candidates' : 'candidates']: data.candidates};
        choices(data.candidates, lyrics);
        paging.hidden = !['lastfm', 'musicbrainz'].includes(snapshot.provider);
        pageLabel.textContent = `Page ${searchPage}`;
        feedback.textContent = data.candidates.length ? (lyrics ? 'Vérifiez les paroles ; une suggestion seule ne contient pas nécessairement leur texte.' : 'Vérifiez la version et écoutez l’extrait disponible avant de choisir.') : 'Aucun résultat. Modifiez la recherche ou le fournisseur.';
      } catch (error) { feedback.textContent = error.message; }
      finally { busy = false; search.disabled = false; previous.disabled = searchPage <= 1; next.disabled = !hasMore; [provider, artist, title, pageSize].forEach(input => { input.disabled = false; }); }
    }
    form.addEventListener('submit', event => { event.preventDefault(); searchCandidates(1); });
    previous.onclick = () => searchCandidates(searchPage - 1);
    next.onclick = () => searchCandidates(searchPage + 1);
    const reject = el('button', 'Retirer la correspondance', 'text-button');
    reject.type = 'button';
    reject.addEventListener('click', () => apply({candidate_id: null}));
    const automatic = el('button', 'Relancer la recherche automatique', 'text-button');
    automatic.type = 'button';
    automatic.addEventListener('click', () => apply({automatic: true}));
    const actions = el('div', '', 'catalog-editor-actions');
    actions.append(lyricChoices, reject, automatic);
    details.append(genres, form, paging, actions, feedback, candidates);
    return details;
  }

  window.RadioAdmin.editor = editor;
  window.RadioAdmin.openEditor = async (track, options = {}) => {
    const requestedAt = location.href;
    const entry = await window.RadioAdmin.request(`/api/admin/catalog/${track.metadata?.track_id || track.track_id}`);
    if (location.href !== requestedAt || !window.RadioAdmin.authenticated() || (options.canOpen && !options.canOpen())) return false;
    const panel = el('dialog', '', 'admin-edit-dialog');
    const heading = el('h2', `Modifier · ${entry.title}`); heading.id = 'admin-edit-title';
    panel.setAttribute('aria-labelledby', heading.id);
    const close = el('button', 'Fermer', 'text-button catalog-close'); close.type = 'button';
    close.onclick = () => panel.close();
    const message = el('p', `Fiche : ${state(entry.status)} · Paroles : ${state(entry.lyrics_status)}`, 'hint');
    const component = editor(entry, entry, message, window.RadioAdmin.request); component.open = true;
    panel.append(close, heading, message, component);
    const openedAt = location.href;
    panel.addEventListener('close', () => {
      const attached = panel.isConnected; panel.remove();
      if (attached && location.href === openedAt) options.onClose?.();
    });
    window.RadioDialogs.dismissOutside(panel);
    document.body.append(panel); panel.showModal();
    return true;
  };
  window.addEventListener('radio:adminchange', () => {
    if (!window.RadioAdmin.authenticated()) document.querySelectorAll('.admin-edit-dialog').forEach(panel => { panel.close(); panel.remove(); });
  });
  window.RadioPages.adminLogin = () => {
    const root = document.querySelector('#admin-root');
    const login = root.querySelector('#admin-login');
    const status = root.querySelector('#admin-status');
    let disposed = false;
    function destination() {
      try {
        const next = new URL(new URLSearchParams(location.search).get('next') || '/admin/dashboard', location.origin);
        if (next.origin === location.origin && ['/admin/dashboard', '/admin/radios', '/admin/diffusions', '/admin/catalogue', '/admin/exclusions'].includes(next.pathname)) return next.pathname + next.search;
      } catch (_) { /* An invalid return URL falls back to the dashboard. */ }
      return '/admin/dashboard';
    }
    login.onsubmit = async event => {
      event.preventDefault();
      const secret = login.elements.token.value; login.elements.token.value = '';
      const button = login.querySelector('button'); button.disabled = true;
      status.textContent = 'Connexion en cours…';
      try {
        await window.RadioAdmin.login(secret);
        if (!disposed) await window.RadioNavigation.go(destination(), true);
      } catch (error) { if (!disposed) status.textContent = error.message; }
      finally { if (!disposed) button.disabled = false; }
    };
    if (window.RadioAdmin.authenticated()) queueMicrotask(() => {
      if (!disposed) window.RadioNavigation.go(destination(), true);
    });
    return () => { disposed = true; login.elements.token.value = ''; };
  };
  window.RadioPages.admin = () => {
    const root = document.querySelector('#admin-root');
    const view = root.dataset.adminPage;
    const workspace = root.querySelector('#admin-workspace');
    const status = root.querySelector('#admin-status');
    const controller = new AbortController();
    let page = 1; let version = 0; let timer;
    const request = (url, payload, method) => window.RadioAdmin.request(url, payload, method, controller.signal);
    function displaySession() {
      if (controller.signal.aborted || !root.isConnected) return;
      if (!window.RadioAdmin.authenticated()) {
        workspace.hidden = true; version++; playsVersion++; auditVersion++;
        window.RadioNavigation.go(`/admin?next=${encodeURIComponent(location.pathname)}`, true);
      }
    }
    async function overview() {
      const current = ++version;
      const panel = root.querySelector('.admin-overview');
      if (!panel) return;
      panel.setAttribute('aria-busy', 'true');
      status.textContent = 'Actualisation de la synthèse…';
      const refresh = root.querySelector('[data-admin-refresh]'); refresh.disabled = true;
      try {
        const params = window.MusicFilters.apply(new URLSearchParams());
        const data = await request(`/api/admin/dashboard?${params}`);
        if (controller.signal.aborted || current !== version) return;
        root.querySelectorAll('[data-admin-metric]').forEach(node => {
          node.textContent = new Intl.NumberFormat('fr-FR').format(data[node.dataset.adminMetric]);
        });
        root.querySelector('#admin-last-detection').textContent = data.last_detection
          ? `Dernière détection visible sur 24 h : ${new Date(data.last_detection).toLocaleString('fr-FR')}`
          : 'Aucune détection visible sur les dernières 24 heures.';
        status.textContent = 'Synthèse actualisée.';
      } catch (error) { if (error.name !== 'AbortError' && current === version) status.textContent = error.message; }
      finally { if (current === version) { panel.setAttribute('aria-busy', 'false'); refresh.disabled = false; } }
    }
    root.querySelector('[data-admin-refresh]')?.addEventListener('click', overview);
    async function radios() {
      const data = await request('/api/admin/stations');
      if (controller.signal.aborted) return;
      const container = root.querySelector('#admin-stations');
      const selects = [playsForm?.elements.station_id, auditForm?.elements.station_id].filter(Boolean);
      const choices = selects.map(select => select.value);
      container?.replaceChildren();
      selects.forEach(select => select.replaceChildren(new Option('Toutes les radios', '')));
      for (const station of data) {
        selects.forEach(select => select.add(new Option(station.name, String(station.id))));
        if (!container) continue;
        const row = el('article', '', 'admin-station');
        row.append(el('strong', station.name), el('span', station.enabled ? 'Collecte active' : 'Collecte arrêtée'));
        const toggle = el('button', station.enabled ? 'Désactiver' : 'Activer', 'text-button');
        const reset = el('button', 'Revenir à la configuration', 'text-button');
        toggle.disabled = !station.configured; reset.disabled = station.enabled_override == null;
        async function change(enabled) {
          toggle.disabled = reset.disabled = true;
          try { await request(`/api/admin/stations/${station.id}`, {enabled}, 'PATCH'); await radios(); status.textContent = 'Préférence enregistrée. Application au prochain cycle de collecte.'; }
          catch (error) { if (error.name !== 'AbortError') status.textContent = error.message; }
        }
        toggle.onclick = () => change(!station.enabled); reset.onclick = () => change(null);
        row.append(toggle, reset); container.append(row);
      }
      selects.forEach((select, index) => { select.value = choices[index]; });
      root.querySelector("#admin-station-search")?.dispatchEvent(new Event("input"));
    }
    async function openEntry(summary) {
      try {
        const entry = await request(`/api/admin/catalog/${summary.track_id}`);
        if (controller.signal.aborted) return;
        const target = root.querySelector('#admin-editor'); target.replaceChildren();
        const message = el('p', `Fiche : ${state(entry.status)} · Paroles : ${state(entry.lyrics_status)}`, 'hint');
        const component = editor(entry, entry, message, request); component.open = true;
        target.append(el('h3', `${entry.artist || 'Artiste inconnu'} — ${entry.title}`), message);
        for (const [provider, attempt] of Object.entries(entry.attempts || {})) target.append(el('p', `${provider} : ${state(attempt.status)}`, 'hint'));
        target.append(component); target.scrollIntoView({block: 'start'});
      } catch (error) { if (error.name !== 'AbortError') status.textContent = error.message; }
    }
    async function catalogue() {
      const current = ++version;
      const form = root.querySelector('#admin-catalog-form');
      if (!form) return;
      const params = new URLSearchParams({q: form.elements.q.value, review: String(form.elements.review.checked), page: String(page), sort: form.elements.sort.value});
      try {
        const data = await request(`/api/admin/catalog?${params}`);
        if (current !== version || controller.signal.aborted) return;
        const container = root.querySelector('#admin-catalog-results'); container.replaceChildren();
        for (const entry of data.items) {
          const button = el('button', `${entry.artist || 'Artiste inconnu'} — ${entry.title} · ${state(entry.status)} · ${entry.recent_plays} détections / 7 jours · Paroles : ${state(entry.lyrics_status)}`, 'admin-track');
          button.type = 'button'; button.onclick = () => openEntry(entry); container.append(button);
        }
        if (!data.items.length) container.append(el('p', 'Aucune fiche pour ces filtres.', 'hint'));
        root.querySelector('[data-admin-page]').textContent = `Page ${page}`;
        root.querySelector('[data-admin-previous]').disabled = page <= 1;
        root.querySelector('[data-admin-next]').disabled = !data.has_more;
      } catch (error) { if (error.name !== 'AbortError') status.textContent = error.message; }
    }
    let playsPage = 1; let playsVersion = 0; let selected = new Map();
    const playsForm = root.querySelector('#admin-plays-form');
    const auditForm = root.querySelector('#admin-audit-form');
    let auditPage = 1; let auditVersion = 0; let auditSnapshot = null; let auditHasRun = false;
    const correction = root.querySelector('#admin-play-correction');
    const previewPane = root.querySelector('#admin-play-preview');
    const playsStatus = root.querySelector('#admin-plays-status');
    let exclusions = [];
    function showExclusions() {
      const q = root.querySelector('#admin-exclusion-search').value.toLocaleLowerCase();
      const target = root.querySelector('#admin-exclusion-results'); target.replaceChildren();
      const matches = exclusions.filter(t => `${t.artist || ''} ${t.title}`.toLocaleLowerCase().includes(q));
      matches.forEach(t => target.append(el('p', `${t.artist || 'Artiste inconnu'} — ${t.title}`)));
      if (!matches.length) target.append(el('p', 'Aucune exclusion pour cette recherche.', 'hint'));
    }
    async function loadExclusions() {
      const data = await request('/api/admin/exclusions');
      if (controller.signal.aborted) return;
      exclusions = data.items; showExclusions();
    }
    root.querySelector('#admin-exclusion-search')?.addEventListener('input', showExclusions);
    root.querySelector('#admin-station-search')?.addEventListener('input', event => {
      const q = event.target.value.toLocaleLowerCase();
      root.querySelectorAll('#admin-stations .admin-station').forEach(row => { row.hidden = !row.textContent.toLocaleLowerCase().includes(q); });
    });
    function updateSelection() {
      previewPane.replaceChildren();
      correction.hidden = selected.size === 0;
      root.querySelectorAll('#admin-plays-results input[data-play-id]').forEach(check => { check.checked = selected.has(Number(check.dataset.playId)); });
      const keep = correction.elements.keep_id; const previous = keep.value;
      keep.replaceChildren(new Option('Choisir dans la sélection', ''));
      selected.forEach(row => keep.add(new Option(`#${row.id} · ${new Date(row.detected_at).toLocaleString()}`, String(row.id))));
      keep.value = previous;
      if (selected.size === 1) {
        const row = [...selected.values()][0];
        correction.elements.artist.value = row.artist || ''; correction.elements.title.value = row.title;
      }
      playsStatus.textContent = `${selected.size} diffusion(s) sélectionnée(s).`;
    }
    function describe(row) {
      return `#${row.id} · ${row.station_name || `Radio ${row.station_id}`} · ${row.artist || 'Artiste inconnu'} — ${row.title} · ${new Date(row.detected_at).toLocaleString()} · dernière observation : ${new Date(row.last_seen_at).toLocaleString()}`;
    }
    const auditReasons = {source: 'Même événement source', duration: 'Retour avant la fin du morceau', consecutive: 'Même titre consécutif', unknown: 'Passages rapprochés sans durée connue'};
    async function audit() {
      if (!auditForm) return;
      const current = ++auditVersion; auditHasRun = true;
      const message = root.querySelector('#admin-audit-status');
      const target = root.querySelector('#admin-audit-results');
      const summary = root.querySelector('#admin-audit-summary');
      const pagination = root.querySelector('#admin-audit-pagination');
      const submit = auditForm.querySelector('button'); submit.disabled = true;
      message.textContent = 'Analyse de l’historique…'; target.replaceChildren(); summary.replaceChildren(); pagination.hidden = true;
      const params = window.MusicFilters.apply(new URLSearchParams({page: String(auditPage)}));
      for (const name of ['station_id', 'margin', 'fallback', 'reason']) if (auditForm.elements[name].value) params.set(name, auditForm.elements[name].value);
      for (const name of ['since', 'until']) if (auditForm.elements[name].value) params.set(name, new Date(auditForm.elements[name].value).toISOString());
      if (auditSnapshot !== null) params.set('snapshot_id', String(auditSnapshot));
      try {
        const data = await request(`/api/admin/plays/audit?${params}`);
        if (controller.signal.aborted || current !== auditVersion) return;
        auditSnapshot = data.snapshot_id;
        const count = data.stations.reduce((total, station) => total + station.plays, 0);
        message.textContent = `${count} passage(s) contrôlé(s) · ${data.total} signalement(s). Les motifs peuvent se cumuler. Historique en base uniquement, hors archives et exclusions.`;
        for (const station of data.stations) {
          summary.append(el('p', `${station.station_name} : ${station.plays} passages · durée connue pour ${station.with_duration} · ${station.suspects} signalements, dont ${station.source} mêmes événements source.`, 'hint'));
        }
        for (const pair of data.items) {
          const article = el('article', '', 'candidate');
          article.append(el('h3', `${pair.current.station_name} · ${pair.current.artist || 'Artiste inconnu'} — ${pair.current.title}`));
          article.append(el('p', pair.reasons.map(reason => auditReasons[reason]).join(' · ')));
          article.append(el('p', `Écart : ${duration(pair.gap_seconds)} · durée : ${duration(pair.duration)} · seuil : ${duration(pair.threshold_seconds)}`, 'hint'));
          if (pair.previous_source_start !== null && pair.source_start !== null && pair.previous_source_start !== pair.source_start) article.append(el('p', 'Les horaires source sont distincts : une vraie rediffusion reste possible.', 'hint'));
          for (const row of [pair.previous, pair.current]) {
            const details = el('details'); details.append(el('summary', describe(row)), el('pre', `${row.source} · ${row.raw_metadata}`, 'lyrics')); article.append(details);
          }
          const review = el('button', 'Préparer le regroupement de cette paire', 'text-button'); review.type = 'button';
          review.onclick = () => {
            selected = new Map([[pair.previous.id, pair.previous], [pair.current.id, pair.current]]); updateSelection();
            correction.elements.action.value = 'merge'; correction.elements.keep_id.value = String(pair.previous.id); correctionFields();
            correction.scrollIntoView({block: 'center', behavior: 'smooth'});
            playsStatus.textContent = 'Paire sélectionnée. Vérifiez les passages puis prévisualisez le regroupement avant de confirmer.';
          };
          article.append(review); target.append(article);
        }
        if (!data.total) target.append(el('p', 'Aucun signalement pour ces critères.', 'hint'));
        pagination.hidden = false;
        root.querySelector('#admin-audit-page').textContent = `Page ${auditPage} · ${data.total} signalements`;
        root.querySelector('#admin-audit-previous').disabled = auditPage <= 1;
        root.querySelector('#admin-audit-next').disabled = !data.has_more;
      } catch (error) { if (error.name !== 'AbortError' && current === auditVersion) message.textContent = error.message; }
      finally { if (current === auditVersion) submit.disabled = false; }
    }
    auditForm?.addEventListener('submit', event => { event.preventDefault(); auditPage = 1; auditSnapshot = null; audit(); });
    auditForm?.addEventListener('input', () => {
      auditVersion++; auditSnapshot = null; auditHasRun = false;
      auditForm.querySelector('button').disabled = false;
      root.querySelector('#admin-audit-status').textContent = 'Critères modifiés : relancez l’analyse.';
      root.querySelector('#admin-audit-results').replaceChildren(); root.querySelector('#admin-audit-summary').replaceChildren(); root.querySelector('#admin-audit-pagination').hidden = true;
      selected.clear(); updateSelection();
    });
    root.querySelector('#admin-audit-previous')?.addEventListener('click', () => { auditPage--; audit(); });
    root.querySelector('#admin-audit-next')?.addEventListener('click', () => { auditPage++; audit(); });
    async function broadcasts() {
      const current = ++playsVersion;
      const params = new URLSearchParams({page: String(playsPage)});
      for (const name of ['q', 'station_id', 'sort']) if (playsForm.elements[name].value) params.set(name, playsForm.elements[name].value);
      for (const name of ['since', 'until']) if (playsForm.elements[name].value) params.set(name, new Date(playsForm.elements[name].value).toISOString());
      params.set('suspicious', String(playsForm.elements.suspicious.checked));
      try {
        const data = await request(`/api/admin/plays?${params}`);
        if (controller.signal.aborted || current !== playsVersion) return;
        selected.clear(); updateSelection();
        const target = root.querySelector('#admin-plays-results'); target.replaceChildren();
        data.items.forEach(row => {
          const article = el('article', '', 'candidate'); const label = el('label', '', 'checkbox');
          const check = el('input'); check.type = 'checkbox'; check.dataset.playId = String(row.id);
          check.onchange = () => { if (check.checked) selected.set(row.id, row); else selected.delete(row.id); updateSelection(); };
          label.append(check, document.createTextNode(describe(row))); article.append(label);
          if (row.suspect) article.append(el('p', `Passage rapproché · précédent à ${new Date(row.previous_at).toLocaleString()}`, 'hint'));
          const details = el('details'); details.append(el('summary', 'Données de collecte'), el('pre', `${row.source} · ${row.raw_metadata}`, 'lyrics')); article.append(details); target.append(article);
        });
        if (!data.items.length) target.append(el('p', 'Aucune diffusion pour ces filtres.', 'hint'));
        root.querySelector('#admin-plays-page').textContent = `Page ${playsPage}`;
        root.querySelector('#admin-plays-previous').disabled = playsPage <= 1;
        root.querySelector('#admin-plays-next').disabled = !data.has_more;
      } catch (error) { if (error.name !== 'AbortError') playsStatus.textContent = error.message; }
    }
    playsForm?.addEventListener('submit', event => { event.preventDefault(); playsPage = 1; broadcasts(); });
    root.querySelector('#admin-plays-previous')?.addEventListener('click', () => { playsPage--; broadcasts(); });
    root.querySelector('#admin-plays-next')?.addEventListener('click', () => { playsPage++; broadcasts(); });
    function correctionFields() {
      previewPane.replaceChildren();
      const action = correction.elements.action.value;
      for (const name of ['keep_id', 'artist', 'title', 'detected_at']) {
        correction.elements[name].closest('label').hidden = name === 'keep_id' ? action !== 'merge' : action !== 'correct';
      }
    }
    if (correction) {
      correction.elements.action.addEventListener('change', correctionFields);
      correctionFields();
    }
    correction?.addEventListener('submit', async event => {
      event.preventDefault(); previewPane.replaceChildren();
      const command = {action: correction.elements.action.value, ids: [...selected.keys()]};
      if (!command.ids.length) { playsStatus.textContent = 'Sélectionnez au moins une diffusion.'; return; }
      if (command.action === 'merge') command.keep_id = Number(correction.elements.keep_id.value) || null;
      if (command.action === 'correct') {
        command.artist = correction.elements.artist.value || null; command.title = correction.elements.title.value;
        if (correction.elements.detected_at.value) command.detected_at = new Date(correction.elements.detected_at.value).toISOString();
      }
      try {
        const data = await request('/api/admin/plays/preview', command);
        if (controller.signal.aborted) return;
        previewPane.append(el('h3', 'Vérifiez les modifications avant de confirmer'), el('p', 'Avant'));
        data.before.forEach(row => previewPane.append(el('p', describe(row))));
        previewPane.append(el('p', 'Après'));
        data.after.forEach(row => previewPane.append(el('p', describe(row))));
        if (!data.after.length) previewPane.append(el('p', 'Toutes les diffusions sélectionnées seront supprimées.'));
        previewPane.append(el('p', 'La suppression est définitive. Un regroupement conserve la date et les données source de la diffusion choisie. Les fiches musicales et exclusions sont conservées.', 'hint'));
        const apply = el('button', 'Confirmer ces modifications', 'text-button'); apply.type = 'button';
        const cancel = el('button', 'Annuler', 'text-button'); cancel.type = 'button'; cancel.onclick = () => previewPane.replaceChildren();
        apply.onclick = async () => {
          apply.disabled = true;
          try {
            await request('/api/admin/plays/apply', {token: data.token});
            window.RadioCache.clear(); window.dispatchEvent(new Event('radio:catalogchange'));
            previewPane.replaceChildren(); auditSnapshot = null; auditPage = 1;
            await Promise.all([broadcasts(), catalogue(), auditHasRun ? audit() : Promise.resolve()]); playsStatus.textContent = 'Modifications enregistrées.';
          } catch (error) { if (error.name !== 'AbortError') { playsStatus.textContent = error.message; previewPane.replaceChildren(); } }
        };
        previewPane.append(apply, cancel);
      } catch (error) { if (error.name !== 'AbortError') playsStatus.textContent = error.message; }
    });
    async function load() {
      displaySession();
      if (!window.RadioAdmin.authenticated() || controller.signal.aborted) return;
      status.textContent = '';
      const loads = {dashboard: overview, radios, catalogue, diffusions: () => Promise.all([radios(), broadcasts()]), exclusions: loadExclusions};
      try { await loads[view](); }
      catch (error) { if (error.name !== 'AbortError') status.textContent = error.message; }
    }
    const search = root.querySelector('#admin-catalog-form');
    if (search) search.elements.review.checked = new URLSearchParams(location.search).get('review') === 'true';
    search?.addEventListener('submit', event => { event.preventDefault(); page = 1; catalogue(); });
    search?.addEventListener('input', () => { clearTimeout(timer); version++; timer = setTimeout(() => { page = 1; catalogue(); }, 350); });
    root.querySelector('[data-admin-previous]')?.addEventListener('click', () => { page--; catalogue(); });
    root.querySelector('[data-admin-next]')?.addEventListener('click', () => { page++; catalogue(); });
    const refreshExclusions = () => { if (view === 'exclusions' && window.RadioAdmin.authenticated()) loadExclusions().catch(error => { if (error.name !== 'AbortError') status.textContent = error.message; }); };
    const refreshOverview = () => { if (view === 'dashboard' && window.RadioAdmin.authenticated()) overview(); };
    const refreshAudit = () => {
      if (view !== 'diffusions' || !window.RadioAdmin.authenticated()) return;
      selected.clear(); updateSelection(); auditSnapshot = null; auditPage = 1;
      if (auditHasRun) audit();
    };
    window.addEventListener('radio:filterschange', refreshAudit);
    window.addEventListener('radio:globalfilterschange', refreshAudit);
    window.addEventListener('radio:filterschange', refreshOverview);
    window.addEventListener('radio:globalfilterschange', refreshExclusions);
    window.addEventListener('radio:adminchange', displaySession);
    window.RadioAdmin.refresh().then(load);
    return () => { controller.abort(); clearTimeout(timer); window.removeEventListener('radio:filterschange', refreshOverview); window.removeEventListener('radio:adminchange', displaySession); window.removeEventListener('radio:globalfilterschange', refreshExclusions); window.removeEventListener('radio:filterschange', refreshAudit); window.removeEventListener('radio:globalfilterschange', refreshAudit); };
  };
})();
