window.RadioPages = window.RadioPages || {};
(() => {
  const el = window.RadioMetrics.el;
  const states = {pending: window.RadioI18n.t("ui.2f6c8477e92b"), matched: window.RadioI18n.t("ui.fe4fd0e2ab0e"), not_found: window.RadioI18n.t("ui.a178c6e0a32b"), ambiguous: window.RadioI18n.t("ui.03a088312dad"), error: window.RadioI18n.t("ui.e2fd6cfcd307"), rejected: window.RadioI18n.t("ui.81d2b90fc979")};
  const state = value => states[value] || value;
  function duration(seconds) { return Number.isFinite(seconds) ? `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}` : window.RadioI18n.t("ui.a6a463d2a49f"); }
  function artwork(url, title) {
    const img = el('img', '', 'catalog-art'); img.src = url; img.alt = window.RadioI18n.t("ui.2c63fa996b93", {p0: (title)});
    img.onerror = () => img.remove(); return img;
  }
  function preview(url, track) { return window.TrackCatalog.playButton(track, url); }
  function editor(track, metadata, message, request) {
    const details = el('details', '', 'catalog-editor');
    details.append(el('summary', window.RadioI18n.t("ui.388d6b8eacd8")));
    details.append(window.YouTubeAdmin.editor(metadata.track_id, request));
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
    const provider = field(window.RadioI18n.t("ui.08e6d9c4d8fa"), el('select'));
    [['deezer', window.RadioI18n.t("ui.4997d093758b")], ['itunes', window.RadioI18n.t("ui.bff90805435c")], ['spotify', window.RadioI18n.t("ui.7005c0064bda")], ['musicbrainz', 'MusicBrainz'], ['lastfm', 'Last.fm'], ['lrclib', window.RadioI18n.t("ui.a5c4bdba21d6")], ['lyricsovh', window.RadioI18n.t("ui.00912b1c694c")]].forEach(([value, label]) => {
      const option = el('option', label);
      option.value = value;
      provider.append(option);
    });
    const artist = field(window.RadioI18n.t("ui.461662879546"), el('input'));
    artist.value = track.artist || '';
    artist.maxLength = 300;
    const title = field(window.RadioI18n.t("ui.634007d25068"), el('input'));
    title.value = track.title;
    title.maxLength = 500;
    title.required = true;
    const search = el('button', window.RadioI18n.t("ui.4b16261a5c23"), 'hero-button');
    search.type = 'submit';
    const pageSize = field(window.RadioI18n.t("ui.6cab2baa56e8"), el('select'));
    [10, 15, 25].forEach(value => { const option = el('option', String(value)); option.value = String(value); pageSize.append(option); });
    pageSize.value = '15'; pageSize.parentElement.hidden = true;
    form.append(search);
    const paging = el('nav', '', 'pagination'); paging.setAttribute('aria-label', window.RadioI18n.t("ui.8413d236008c")); paging.hidden = true;
    const previous = el('button', window.RadioI18n.t("ui.9a910fc71e8f"), 'text-button'); previous.type = 'button';
    const pageLabel = el('span', window.RadioI18n.t("ui.e8b8355262d0"));
    const next = el('button', window.RadioI18n.t("ui.e284bde10808"), 'text-button'); next.type = 'button';
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
    const fieldLabels = {genres: 'genres', album: 'album', release_date: window.RadioI18n.t("ui.439849062051"), featured_artists: window.RadioI18n.t("ui.62962ec239c9"), duration: window.RadioI18n.t("ui.506853e6d373"), cover_url: 'pochette', preview_url: 'extrait', isrc: 'ISRC', url: window.RadioI18n.t("ui.412b4a1361c4"), additional_sources: window.RadioI18n.t("ui.412b4a1361c4")};
    function updateMetadata(data) {
      metadata = {...metadata, ...data, genres: data.genres || []}; renderGenres(); refreshCompleters();
    }
    function refreshCompleters() {
      for (const {candidate, container} of completers) {
        container.replaceChildren();
        const id = `${candidate.provider}:${candidate.provider_id}`;
        const fields = metadata.candidate_completions?.[id] || [];
        if (!fields.length) continue;
        const complete = el('button', window.RadioI18n.t("ui.3b61fbaf967a"), 'text-button'); complete.type = 'button';
        complete.title = window.RadioI18n.t("ui.9f0fe53c551e", {p0: (fields.map(f => fieldLabels[f] || f).join(', '))});
        complete.onclick = async () => {
          if (busy) return;
          busy = true; complete.disabled = true;
          feedback.textContent = window.RadioI18n.t("ui.23ca1e76dc59");
          try {
            const data = await call('complete-candidate', {candidate_id: id, revision: metadata.revision});
            updateMetadata(data);
            const labels = data.completion.filled.map(f => fieldLabels[f] || f);
            feedback.textContent = labels.length ? window.RadioI18n.t("ui.04b497453d01", {p0: (labels.join(', '))}) : window.RadioI18n.t("ui.56e1450a9147");
            message.textContent = window.RadioI18n.t("ui.b21bedef9837");
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
            ? window.RadioI18n.t("ui.43c4459c9bc6")
            : window.RadioI18n.t("ui.8082a12cc5d5")
          : window.RadioI18n.t("ui.975162757d94");
        message.textContent = payload.automatic ? window.RadioI18n.t("ui.ab0361874053") : window.RadioI18n.t("ui.2113ff602464");
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
          const absent = c.lyrics_status === 'not_found' ? window.RadioI18n.t("ui.1044a8d94b09") : c.lyrics_status === 'error' ? window.RadioI18n.t("ui.2a59c340c327") : window.RadioI18n.t("ui.57ced0eef5f5");
          presentation.append(el('pre', c.plain || c.synced || (c.instrumental ? window.RadioI18n.t("ui.05082dc1436b") : absent), 'lyrics'));
          if (c.provider === 'lyricsovh' && !c.plain && c.lyrics_status !== 'not_found') {
            const load = el('button', c.lyrics_status === 'error' ? window.RadioI18n.t("ui.3eeeb8cc00a0") : window.RadioI18n.t("ui.d0e79366abfa"), 'text-button'); load.type = 'button';
            load.onclick = () => enrichCandidate('candidate-lyrics', c.id, true);
            presentation.append(load);
          }
        }
        if (!lyrics && c.provider === 'musicbrainz' && c.release_id && !c.cover_url) {
          if (c.cover_status === 'not_found') presentation.append(el('p', window.RadioI18n.t("ui.c3d88971305a"), 'hint'));
          else {
            const load = el('button', c.cover_status === 'error' ? window.RadioI18n.t("ui.7b02c15bb2fb") : window.RadioI18n.t("ui.9427d015afef"), 'text-button'); load.type = 'button';
            load.onclick = () => enrichCandidate('candidate-artwork', `${c.provider}:${c.provider_id}`, false);
            presentation.append(load);
          }
        }
        const response = el('section', '', 'candidate-response');
        const responsePane = el('pre', JSON.stringify(c, null, 2));
        response.append(el('h4', window.RadioI18n.t("ui.2d99a489572f")), responsePane);
        const choose = el('button', window.RadioI18n.t("ui.0212f6526f72"), 'text-button');
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
    const lyricChoices = el('button', window.RadioI18n.t("ui.596c3cb7cb56"), 'text-button');
    lyricChoices.type = 'button';
    lyricChoices.onclick = () => { invalidatePaging(); choices(metadata.lyrics_candidates || [], true); };

    async function enrichCandidate(action, candidateId, lyrics) {
      if (busy) return;
      busy = true;
      feedback.textContent = lyrics ? window.RadioI18n.t("ui.efe0f6e0189b") : window.RadioI18n.t("ui.7d0cf3197623");
      try {
        const data = await call(action, {candidate_id: candidateId, revision: metadata.revision});
        metadata = {...metadata, revision: data.revision, candidate_completions: data.candidate_completions,
          [lyrics ? 'lyrics_candidates' : 'candidates']: data.candidates};
        choices(data.candidates, lyrics);
        const status = lyrics ? data.lyrics_status : data.artwork_status;
        feedback.textContent = status === 'matched' ? (lyrics ? window.RadioI18n.t("ui.f339435624f8") : window.RadioI18n.t("ui.0fff4656ee3a")) : status === 'error' ? window.RadioI18n.t("ui.3caf9b3f3e3c") : (lyrics ? window.RadioI18n.t("ui.23329c8712eb") : window.RadioI18n.t("ui.b8fe0d8c6d87"));
      } catch (error) { feedback.textContent = error.message; }
      finally { busy = false; }
    }
    async function searchCandidates(page = 1) {
      if (busy) return;
      const snapshot = page === 1 ? {provider: provider.value, artist: artist.value, title: title.value, limit: Number(pageSize.value)} : searchSnapshot;
      if (!snapshot) return;
      busy = true; search.disabled = previous.disabled = next.disabled = true;
      [provider, artist, title, pageSize].forEach(input => { input.disabled = true; });
      feedback.textContent = window.RadioI18n.t("ui.ee560029d8a5");
      try {
        const data = await call('search', {...snapshot, page});
        searchSnapshot = snapshot; searchPage = data.page || 1; hasMore = !!data.has_more;
        const lyrics = lyricsProvider(snapshot.provider);
        metadata = {...metadata, revision: data.revision, candidate_completions: data.candidate_completions,
          [lyrics ? 'lyrics_candidates' : 'candidates']: data.candidates};
        choices(data.candidates, lyrics);
        paging.hidden = !['lastfm', 'musicbrainz'].includes(snapshot.provider);
        pageLabel.textContent = window.RadioI18n.t("ui.dfeb8707a5c4", {p0: (searchPage)});
        feedback.textContent = data.candidates.length ? (lyrics ? 'Vérifiez les paroles ; une suggestion seule ne contient pas nécessairement leur texte.' : window.RadioI18n.t("ui.05ac6617680b")) : window.RadioI18n.t("ui.78ee61bfb968");
      } catch (error) { feedback.textContent = error.message; }
      finally { busy = false; search.disabled = false; previous.disabled = searchPage <= 1; next.disabled = !hasMore; [provider, artist, title, pageSize].forEach(input => { input.disabled = false; }); }
    }
    form.addEventListener('submit', event => { event.preventDefault(); searchCandidates(1); });
    previous.onclick = () => searchCandidates(searchPage - 1);
    next.onclick = () => searchCandidates(searchPage + 1);
    const reject = el('button', window.RadioI18n.t("ui.cd048e437013"), 'text-button');
    reject.type = 'button';
    reject.addEventListener('click', () => apply({candidate_id: null}));
    const automatic = el('button', window.RadioI18n.t("ui.6bcfd5632c76"), 'text-button');
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
    const heading = el('h2', window.RadioI18n.t("ui.2b4f460f514d", {p0: (entry.title)})); heading.id = 'admin-edit-title';
    panel.setAttribute('aria-labelledby', heading.id);
    const close = el('button', window.RadioI18n.t("ui.711e5f2e198d"), 'text-button catalog-close'); close.type = 'button';
    close.onclick = () => panel.close();
    const message = el('p', window.RadioI18n.t("ui.c94cce6cabd6", {p0: (state(entry.status)), p1: (state(entry.lyrics_status))}), 'hint');
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
      status.textContent = window.RadioI18n.t("ui.8fb11751520a");
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
      status.textContent = window.RadioI18n.t("ui.38449d842c36");
      const refresh = root.querySelector('[data-admin-refresh]'); refresh.disabled = true;
      try {
        const params = window.MusicFilters.apply(new URLSearchParams());
        const data = await request(`/api/admin/dashboard?${params}`);
        if (controller.signal.aborted || current !== version) return;
        root.querySelectorAll('[data-admin-metric]').forEach(node => {
          node.textContent = new Intl.NumberFormat((window.RadioI18n?.locale || 'fr-FR')).format(data[node.dataset.adminMetric]);
        });
        root.querySelector('#admin-last-detection').textContent = data.last_detection
          ? window.RadioI18n.t("ui.6b405da30fdc", {p0: (new Date(data.last_detection).toLocaleString((window.RadioI18n?.locale || 'fr-FR')))})
          : window.RadioI18n.t("ui.c3e1e18dcdd1");
        status.textContent = window.RadioI18n.t("ui.7b9cc2b5b6b5");
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
      selects.forEach(select => select.replaceChildren(new Option(window.RadioI18n.t("ui.3d7e6d53866b"), '')));
      for (const station of data) {
        selects.forEach(select => select.add(new Option(station.name, String(station.id))));
        if (!container) continue;
        const row = el('article', '', 'admin-station');
        row.append(el('strong', station.name), el('span', station.enabled ? window.RadioI18n.t("ui.623f8c716c16") : window.RadioI18n.t("ui.eada9f1201cf")));
        const toggle = el('button', station.enabled ? window.RadioI18n.t("ui.c07050ca8933") : window.RadioI18n.t("ui.77bf4e88dbc8"), 'text-button');
        const reset = el('button', window.RadioI18n.t("ui.ad55132715c9"), 'text-button');
        toggle.disabled = !station.configured; reset.disabled = station.enabled_override == null;
        async function change(enabled) {
          toggle.disabled = reset.disabled = true;
          try { await request(`/api/admin/stations/${station.id}`, {enabled}, 'PATCH'); await radios(); status.textContent = window.RadioI18n.t("ui.e540be627de2"); }
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
        const message = el('p', window.RadioI18n.t("ui.c94cce6cabd6", {p0: (state(entry.status)), p1: (state(entry.lyrics_status))}), 'hint');
        const component = editor(entry, entry, message, request); component.open = true;
        target.append(el('h3', `${entry.artist || window.RadioI18n.t("ui.133df509234a")} — ${entry.title}`), message);
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
        const data = await request(`/api/admin/catalog?${window.MusicFilters.apply(params)}`);
        if (current !== version || controller.signal.aborted) return;
        const container = root.querySelector('#admin-catalog-results'); container.replaceChildren();
        for (const entry of data.items) {
          const button = el('button', window.RadioI18n.t("ui.10f23e4b6e45", {p0: (entry.artist || window.RadioI18n.t("ui.133df509234a")), p1: (entry.title), p2: (state(entry.status)), p3: (entry.recent_plays), p4: (state(entry.lyrics_status))}), 'admin-track');
          button.type = 'button'; button.onclick = () => openEntry(entry); container.append(button);
        }
        if (!data.items.length) container.append(el('p', window.RadioI18n.t("ui.26d70fda0f48"), 'hint'));
        root.querySelector('[data-admin-page]').textContent = window.RadioI18n.t("ui.dfeb8707a5c4", {p0: (page)});
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
      matches.forEach(t => target.append(el('p', `${t.artist || window.RadioI18n.t("ui.133df509234a")} — ${t.title}`)));
      if (!matches.length) target.append(el('p', window.RadioI18n.t("ui.e9da93b7a037"), 'hint'));
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
      keep.replaceChildren(new Option(window.RadioI18n.t("ui.c657decec207"), ''));
      selected.forEach(row => keep.add(new Option(`#${row.id} · ${new Date(row.detected_at).toLocaleString(window.RadioI18n.locale)}`, String(row.id))));
      keep.value = previous;
      if (selected.size === 1) {
        const row = [...selected.values()][0];
        correction.elements.artist.value = row.artist || ''; correction.elements.title.value = row.title;
      }
      playsStatus.textContent = window.RadioI18n.t("ui.4c4b471b5092", {p0: (selected.size)});
    }
    function describe(row) {
      return window.RadioI18n.t("ui.42047c94bb63", {p0: (row.id), p1: (row.station_name || window.RadioI18n.t("ui.ce5579031bb3", {p0: (row.station_id)})), p2: (row.artist || window.RadioI18n.t("ui.133df509234a")), p3: (row.title), p4: (new Date(row.detected_at).toLocaleString(window.RadioI18n.locale)), p5: (new Date(row.last_seen_at).toLocaleString(window.RadioI18n.locale))});
    }
    const auditReasons = {source: window.RadioI18n.t("ui.e52ba9ce7cb1"), duration: window.RadioI18n.t("ui.397426b85f47"), consecutive: window.RadioI18n.t("ui.bd1f15479450"), unknown: window.RadioI18n.t("ui.128d9ba34edd")};
    async function audit() {
      if (!auditForm) return;
      const current = ++auditVersion; auditHasRun = true;
      const message = root.querySelector('#admin-audit-status');
      const target = root.querySelector('#admin-audit-results');
      const summary = root.querySelector('#admin-audit-summary');
      const pagination = root.querySelector('#admin-audit-pagination');
      const submit = auditForm.querySelector('button'); submit.disabled = true;
      message.textContent = window.RadioI18n.t("ui.37f5779cba49"); target.replaceChildren(); summary.replaceChildren(); pagination.hidden = true;
      const params = window.MusicFilters.apply(new URLSearchParams({page: String(auditPage)}));
      for (const name of ['station_id', 'margin', 'fallback', 'reason']) if (auditForm.elements[name].value) params.set(name, auditForm.elements[name].value);
      for (const name of ['since', 'until']) if (auditForm.elements[name].value) params.set(name, new Date(auditForm.elements[name].value).toISOString());
      if (auditSnapshot !== null) params.set('snapshot_id', String(auditSnapshot));
      try {
        const data = await request(`/api/admin/plays/audit?${params}`);
        if (controller.signal.aborted || current !== auditVersion) return;
        auditSnapshot = data.snapshot_id;
        const count = data.stations.reduce((total, station) => total + station.plays, 0);
        message.textContent = window.RadioI18n.t("ui.53b80bc0bb4d", {p0: (count), p1: (data.total)});
        for (const station of data.stations) {
          summary.append(el('p', window.RadioI18n.t("ui.dcf47967cb68", {p0: (station.station_name), p1: (station.plays), p2: (station.with_duration), p3: (station.suspects), p4: (station.source)}), 'hint'));
        }
        for (const pair of data.items) {
          const article = el('article', '', 'candidate');
          article.append(el('h3', `${pair.current.station_name} · ${pair.current.artist || window.RadioI18n.t("ui.133df509234a")} — ${pair.current.title}`));
          article.append(el('p', pair.reasons.map(reason => auditReasons[reason]).join(' · ')));
          article.append(el('p', window.RadioI18n.t("ui.52078573f767", {p0: (duration(pair.gap_seconds)), p1: (duration(pair.duration)), p2: (duration(pair.threshold_seconds))}), 'hint'));
          if (pair.previous_source_start !== null && pair.source_start !== null && pair.previous_source_start !== pair.source_start) article.append(el('p', window.RadioI18n.t("ui.5ed119d8ca93"), 'hint'));
          for (const row of [pair.previous, pair.current]) {
            const details = el('details'); details.append(el('summary', describe(row)), el('pre', `${row.source} · ${row.raw_metadata}`, 'lyrics')); article.append(details);
          }
          const review = el('button', window.RadioI18n.t("ui.5abf8bdcea81"), 'text-button'); review.type = 'button';
          review.onclick = () => {
            selected = new Map([[pair.previous.id, pair.previous], [pair.current.id, pair.current]]); updateSelection();
            correction.elements.action.value = 'merge'; correction.elements.keep_id.value = String(pair.previous.id); correctionFields();
            correction.scrollIntoView({block: 'center', behavior: 'smooth'});
            playsStatus.textContent = window.RadioI18n.t("ui.11942e2b883b");
          };
          article.append(review); target.append(article);
        }
        if (!data.total) target.append(el('p', window.RadioI18n.t("ui.11bbe68a085c"), 'hint'));
        pagination.hidden = false;
        root.querySelector('#admin-audit-page').textContent = window.RadioI18n.t("ui.5d66f600616f", {p0: (auditPage), p1: (data.total)});
        root.querySelector('#admin-audit-previous').disabled = auditPage <= 1;
        root.querySelector('#admin-audit-next').disabled = !data.has_more;
      } catch (error) { if (error.name !== 'AbortError' && current === auditVersion) message.textContent = error.message; }
      finally { if (current === auditVersion) submit.disabled = false; }
    }
    auditForm?.addEventListener('submit', event => { event.preventDefault(); auditPage = 1; auditSnapshot = null; audit(); });
    auditForm?.addEventListener('input', () => {
      auditVersion++; auditSnapshot = null; auditHasRun = false;
      auditForm.querySelector('button').disabled = false;
      root.querySelector('#admin-audit-status').textContent = window.RadioI18n.t("ui.a24700818ae9");
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
          if (row.suspect) article.append(el('p', window.RadioI18n.t("ui.8bf4edbf4bbf", {p0: (new Date(row.previous_at).toLocaleString(window.RadioI18n.locale))}), 'hint'));
          const details = el('details'); details.append(el('summary', window.RadioI18n.t("ui.a4cc68847ff6")), el('pre', `${row.source} · ${row.raw_metadata}`, 'lyrics')); article.append(details); target.append(article);
        });
        if (!data.items.length) target.append(el('p', window.RadioI18n.t("ui.553009532b95"), 'hint'));
        root.querySelector('#admin-plays-page').textContent = window.RadioI18n.t("ui.dfeb8707a5c4", {p0: (playsPage)});
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
      if (!command.ids.length) { playsStatus.textContent = window.RadioI18n.t("ui.71c9877348d5"); return; }
      if (command.action === 'merge') command.keep_id = Number(correction.elements.keep_id.value) || null;
      if (command.action === 'correct') {
        command.artist = correction.elements.artist.value || null; command.title = correction.elements.title.value;
        if (correction.elements.detected_at.value) command.detected_at = new Date(correction.elements.detected_at.value).toISOString();
      }
      try {
        const data = await request('/api/admin/plays/preview', command);
        if (controller.signal.aborted) return;
        previewPane.append(el('h3', window.RadioI18n.t("ui.1efe198c1d02")), el('p', window.RadioI18n.t("ui.a6594f80458a")));
        data.before.forEach(row => previewPane.append(el('p', describe(row))));
        previewPane.append(el('p', window.RadioI18n.t("ui.6f0f4bbdb64a")));
        data.after.forEach(row => previewPane.append(el('p', describe(row))));
        if (!data.after.length) previewPane.append(el('p', window.RadioI18n.t("ui.438af20f919e")));
        previewPane.append(el('p', window.RadioI18n.t("ui.6ed4087506e9"), 'hint'));
        const apply = el('button', window.RadioI18n.t("ui.8d0ec488115d"), 'text-button'); apply.type = 'button';
        const cancel = el('button', window.RadioI18n.t("ui.46ad3916f6a0"), 'text-button'); cancel.type = 'button'; cancel.onclick = () => previewPane.replaceChildren();
        apply.onclick = async () => {
          apply.disabled = true;
          try {
            await request('/api/admin/plays/apply', {token: data.token});
            window.RadioCache.clear(); window.dispatchEvent(new Event('radio:catalogchange'));
            previewPane.replaceChildren(); auditSnapshot = null; auditPage = 1;
            await Promise.all([broadcasts(), catalogue(), auditHasRun ? audit() : Promise.resolve()]); playsStatus.textContent = window.RadioI18n.t("ui.5df5130dfacf");
          } catch (error) { if (error.name !== 'AbortError') { playsStatus.textContent = error.message; previewPane.replaceChildren(); } }
        };
        previewPane.append(apply, cancel);
      } catch (error) { if (error.name !== 'AbortError') playsStatus.textContent = error.message; }
    });
    async function workers() {
      const data = await request(`/api/admin/workers?${window.MusicFilters.apply(new URLSearchParams())}`);
      if (controller.signal.aborted) return;
      const container = root.querySelector('#worker-states'); container.replaceChildren();
      const labels = {collector: window.RadioI18n.t("ui.d7864a680b41"), enrichment: window.RadioI18n.t("ui.32febdfc98c8"), youtube: window.RadioI18n.t("ui.cb77991bb033")};
      const states = {unknown: window.RadioI18n.t("ui.813ba490985e"), stale: window.RadioI18n.t("ui.2bf64327ef93"), idle: window.RadioI18n.t("ui.2f6c8477e92b"), running: window.RadioI18n.t("ui.797f5dcd0217"), error: window.RadioI18n.t("ui.e2fd6cfcd307")};
      for (const worker of data.items) {
        const row = el('article', '', 'worker-state');
        const at = worker.heartbeat_at ? new Date(worker.heartbeat_at).toLocaleString(window.RadioI18n?.locale || (window.RadioI18n?.locale || 'fr-FR')) : window.RadioI18n.t("ui.a3f6f2fe545c");
        row.append(el('h3', labels[worker.name]), el('p', states[worker.state]), el('p', window.RadioI18n.t("ui.263ea92680f0", {p0: (worker.completed), p1: (worker.total), p2: (worker.failed)})), el('p', at, 'hint'));
        const progress = el('progress'); progress.max = Math.max(1, worker.total); progress.value = worker.completed + worker.failed; progress.setAttribute('aria-label', labels[worker.name]); row.append(progress); container.append(row);
      }
      const quota = data.youtube;
      root.querySelector('#worker-quota').textContent = window.RadioI18n.t("ui.aadd5f78e696", {p0: (quota.configured ? window.RadioI18n.t("ui.54d4d88287b9") : window.RadioI18n.t("ui.73c1defc7778")), p1: (quota.used), p2: (quota.limit), p3: (quota.pending), p4: (quota.retry_at ? window.RadioI18n.t("ui.e62dc5ff32b5") + new Date(quota.retry_at).toLocaleString(window.RadioI18n.locale) : '')});
      const events = root.querySelector('#worker-events'); events.replaceChildren();
      const codes = {started: window.RadioI18n.t("ui.e316a0a4628c"), completed: window.RadioI18n.t("ui.1ca783ebdf5c"), error: window.RadioI18n.t("ui.e2fd6cfcd307")};
      for (const item of data.events) events.append(el('li', `${new Date(item.at).toLocaleString(window.RadioI18n.locale)} · ${labels[item.worker]} · ${codes[item.code] || item.code}`));
      if (!data.events.length) events.append(el('li', window.RadioI18n.t("ui.2781a89dfbbb")));
    }
    const workerTimer = view === 'workers' ? setInterval(() => workers().catch(error => { if (error.name !== 'AbortError') status.textContent = error.message; }), 10000) : null;
    async function load() {
      displaySession();
      if (!window.RadioAdmin.authenticated() || controller.signal.aborted) return;
      status.textContent = '';
      const loads = {workers, dashboard: overview, radios, catalogue, diffusions: () => Promise.all([radios(), broadcasts()]), exclusions: loadExclusions};
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
    return () => { clearInterval(workerTimer); controller.abort(); clearTimeout(timer); window.removeEventListener('radio:filterschange', refreshOverview); window.removeEventListener('radio:adminchange', displaySession); window.removeEventListener('radio:globalfilterschange', refreshExclusions); window.removeEventListener('radio:filterschange', refreshAudit); window.removeEventListener('radio:globalfilterschange', refreshAudit); };
  };
})();
