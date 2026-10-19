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
        feedback.textContent = 'Correction enregistrée. Rouvrez les détails pour la consulter.';
        message.textContent = 'Informations mises à jour · actualisez le classement pour les afficher.';
      } catch (error) { feedback.textContent = error.message; }
      finally { busy = false; }
    }
    function choices(items, lyrics = false) {
      candidates.replaceChildren();
      items.forEach(c => {
        const row = el('article', '', 'candidate candidate-layout');
        const presentation = el('div', '', 'candidate-presentation');
        if (c.cover_url) presentation.append(artwork(c.cover_url, c.title));
        presentation.append(el('p', `${c.artist} — ${c.title} · ${c.album || ''} · ${duration(c.duration)}`));
        if (c.preview_url) presentation.append(preview(c.preview_url, c));
        if (lyrics) presentation.append(el('pre', c.plain || c.synced || 'Instrumental', 'lyrics'));
        const response = el('section', '', 'candidate-response');
        const responsePane = el('pre', JSON.stringify(c, null, 2));
        response.append(el('h4', 'Réponse de l’API'), responsePane);
        const choose = el('button', 'Choisir cette version', 'text-button');
        choose.type = 'button';
        choose.addEventListener('click', () => apply({target: lyrics ? 'lyrics' : 'recording', candidate_id: lyrics ? c.id : `${c.provider}:${c.provider_id}`}, responsePane));
        presentation.append(choose);
        row.append(presentation, response);
        candidates.append(row);
      });
    }
    choices(metadata.candidates || []);
    const lyricChoices = el('button', 'Voir les propositions de paroles enregistrées', 'text-button');
    lyricChoices.type = 'button';
    lyricChoices.onclick = () => choices(metadata.lyrics_candidates || [], true);
    form.append(lyricChoices);
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
    const complete = el('button', 'Compléter les informations manquantes', 'text-button');
    complete.type = 'button';
    complete.disabled = metadata.status !== 'matched';
    complete.onclick = async () => {
      if (busy) return;
      busy = true; complete.disabled = true;
      feedback.textContent = 'Vérification dans le second catalogue…';
      try {
        const data = await call('complete', {});
        const labels = {genres: 'genres', album: 'album', release_date: 'date de sortie', featured_artists: 'artistes invités'};
        const fields = (data.completion.filled || []).map(field => labels[field] || field);
        feedback.textContent = fields.length ? `Informations complétées : ${fields.join(', ')}. Rouvrez la fiche pour les consulter.` : data.completion.status === 'error' ? 'Second catalogue temporairement indisponible. Vous pouvez réessayer.' : data.completion.status === 'ambiguous' ? 'Plusieurs versions possibles. Aucun champ modifié.' : 'Aucune information supplémentaire validée.';
      } catch (error) { feedback.textContent = error.message; }
      finally { busy = false; complete.disabled = false; }
    };
    details.append(form, feedback, candidates, complete, reject, automatic);
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
        version++; status.textContent = 'Connectez-vous pour corriger les informations.';
      }
    }
    async function radios() {
      const data = await request('/api/admin/stations');
      if (controller.signal.aborted) return;
      const container = root.querySelector('#admin-stations'); container.replaceChildren();
      for (const station of data) {
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
      const params = new URLSearchParams({q: form.elements.q.value, review: String(form.elements.review.checked), page: String(page)});
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
    async function load() {
      displaySession();
      if (!window.RadioAdmin.authenticated() || controller.signal.aborted) return;
      status.textContent = 'Session active · retour au classement sans ressaisir la clé.';
      try { await Promise.all([radios(), catalogue()]); }
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
    window.addEventListener('radio:adminchange', displaySession);
    window.RadioAdmin.refresh().then(load);
    return () => { controller.abort(); clearTimeout(timer); tokenInput.value = ''; window.removeEventListener('radio:adminchange', displaySession); };
  };
})();
