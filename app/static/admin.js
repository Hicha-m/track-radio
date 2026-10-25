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
        feedback.textContent = 'Correction enregistrée. Rouvrez les détails pour la consulter.';
        message.textContent = 'Informations mises à jour · actualisez le classement pour les afficher.';
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
        if (lyrics) presentation.append(el('pre', c.plain || c.synced || 'Instrumental', 'lyrics'));
        const response = el('section', '', 'candidate-response');
        const responsePane = el('pre', JSON.stringify(c, null, 2));
        response.append(el('h4', 'Réponse de l’API'), responsePane);
        const choose = el('button', 'Choisir cette version', 'text-button');
        choose.type = 'button';
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
    lyricChoices.onclick = () => choices(metadata.lyrics_candidates || [], true);

    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (busy) return;
      busy = true;
      search.disabled = true;
      feedback.textContent = 'Recherche chez le fournisseur…';
      const selectedProvider = provider.value;
      try {
        const data = await call('search', {provider: selectedProvider, artist: artist.value, title: title.value});
        metadata = {...metadata, revision: data.revision, candidate_completions: data.candidate_completions,
          [selectedProvider === 'lrclib' ? 'lyrics_candidates' : 'candidates']: data.candidates};
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
    const actions = el('div', '', 'catalog-editor-actions');
    actions.append(lyricChoices, reject, automatic);
    details.append(genres, form, actions, feedback, candidates);
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
  window.RadioPages.admin = () => {
    const root = document.querySelector('#admin-root');
    const login = root.querySelector('#admin-login');
    const workspace = root.querySelector('#admin-workspace');
    const status = root.querySelector('#admin-status');
    const tokenInput = login.elements.token;
    const controller = new AbortController();
    let page = 1; let version = 0; let timer;
    const request = (url, payload, method) => window.RadioAdmin.request(url, payload, method, controller.signal);
    function displaySession() {
      if (controller.signal.aborted) return;
      const active = window.RadioAdmin.authenticated();
      login.hidden = active; workspace.hidden = !active; if (active) tokenInput.value = '';
      if (!active) {
        root.querySelector('#admin-stations').replaceChildren();
        root.querySelector('#admin-catalog-results').replaceChildren();
        root.querySelector('#admin-editor').replaceChildren();
        selected.clear(); exclusions = []; playsVersion++;
        root.querySelector('#admin-plays-results').replaceChildren();
        root.querySelector('#admin-exclusion-results').replaceChildren(); previewPane.replaceChildren();
        correction.reset(); playsStatus.textContent = '';
        version++; status.textContent = 'Connectez-vous pour corriger les informations.';
      }
    }
    async function radios() {
      const data = await request('/api/admin/stations');
      if (controller.signal.aborted) return;
      const container = root.querySelector('#admin-stations'); container.replaceChildren();
      const stationSelect = playsForm.elements.station_id; const chosen = stationSelect.value;
      stationSelect.replaceChildren(new Option('Toutes', ''));
      for (const station of data) {
        stationSelect.add(new Option(station.name, String(station.id)));
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
      stationSelect.value = chosen;
      root.querySelector("#admin-station-search").dispatchEvent(new Event("input"));
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
    root.querySelector('#admin-exclusion-search').oninput = showExclusions;
    root.querySelector('#admin-station-search').oninput = event => {
      const q = event.target.value.toLocaleLowerCase();
      root.querySelectorAll('#admin-stations .admin-station').forEach(row => { row.hidden = !row.textContent.toLocaleLowerCase().includes(q); });
    };
    function updateSelection() {
      previewPane.replaceChildren();
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
          const check = el('input'); check.type = 'checkbox';
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
    playsForm.onsubmit = event => { event.preventDefault(); playsPage = 1; broadcasts(); };
    root.querySelector('#admin-plays-previous').onclick = () => { playsPage--; broadcasts(); };
    root.querySelector('#admin-plays-next').onclick = () => { playsPage++; broadcasts(); };
    correction.onsubmit = async event => {
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
            previewPane.replaceChildren(); await Promise.all([broadcasts(), catalogue()]); playsStatus.textContent = 'Modifications enregistrées.';
          } catch (error) { if (error.name !== 'AbortError') { playsStatus.textContent = error.message; previewPane.replaceChildren(); } }
        };
        previewPane.append(apply, cancel);
      } catch (error) { if (error.name !== 'AbortError') playsStatus.textContent = error.message; }
    };
    async function load() {
      displaySession();
      if (!window.RadioAdmin.authenticated() || controller.signal.aborted) return;
      status.textContent = 'Session active · retour au classement sans ressaisir la clé.';
      try { await Promise.all([radios(), catalogue(), broadcasts(), loadExclusions()]); }
      catch (error) { if (error.name !== 'AbortError') status.textContent = error.message; }
    }
    login.onsubmit = async event => {
      event.preventDefault(); const secret = tokenInput.value; tokenInput.value = '';
      const button = login.querySelector('button'); button.disabled = true;
      try { await window.RadioAdmin.login(secret); await load(); }
      catch (error) { status.textContent = error.message; }
      finally { button.disabled = false; }
    };
    const search = root.querySelector('#admin-catalog-form');
    search.onsubmit = event => { event.preventDefault(); page = 1; catalogue(); };
    search.oninput = () => { clearTimeout(timer); version++; timer = setTimeout(() => { page = 1; catalogue(); }, 350); };
    root.querySelector('[data-admin-previous]').onclick = () => { page--; catalogue(); };
    root.querySelector('[data-admin-next]').onclick = () => { page++; catalogue(); };
    const refreshExclusions = () => { if (window.RadioAdmin.authenticated()) loadExclusions().catch(error => { if (error.name !== 'AbortError') status.textContent = error.message; }); };
    window.addEventListener('radio:globalfilterschange', refreshExclusions);
    window.addEventListener('radio:adminchange', displaySession);
    window.RadioAdmin.refresh().then(load);
    return () => { controller.abort(); clearTimeout(timer); tokenInput.value = ''; window.removeEventListener('radio:adminchange', displaySession); window.removeEventListener('radio:globalfilterschange', refreshExclusions); };
  };
})();
