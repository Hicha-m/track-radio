window.YouTubeAdmin = {editor(key, request) {
  const el = (tag, text = '') => { const node = document.createElement(tag); node.textContent = text; return node; };
  const videoRequest = request;
  request = (url, payload) => videoRequest(`${url}?${window.MusicFilters.apply(new URLSearchParams())}`, payload);
  const section = el('section'); section.className = 'youtube-admin';
  const heading = el('h3', window.RadioI18n.t("ui.46aa38caf70b")); const hint = el('p', window.RadioI18n.t("ui.4392a1f66269")); hint.className = 'hint';
  const status = el('p'); status.setAttribute('role', 'status');
  const choices = el('div'); choices.className = 'youtube-choices';
  const search = el('button', window.RadioI18n.t("ui.e948f7f5c8ed")); search.type = 'button'; search.className = 'text-button';
  const link = el('input'); link.type = 'url'; link.placeholder = 'https://www.youtube.com/watch?v=…';
  const label = el('label', window.RadioI18n.t('youtube.link_label')); label.append(link);
  const verify = el('button', window.RadioI18n.t('youtube.verify_link')); verify.type = 'button';
  verify.onclick = () => action(() => request(`/api/catalog/${key}/youtube/link`, {url: link.value, revision: data?.revision || 0}));
  section.append(heading, hint, status, search, label, verify, choices);
  let data, busy = false;
  async function action(run) {
    if (busy) return; busy = true; search.disabled = verify.disabled = true;
    try { render(await run()); }
    catch (error) { if (error.name !== 'AbortError') status.textContent = error.message; }
    finally { busy = false; search.disabled = verify.disabled = false; }
  }
  function choose(candidate_id, automatic = false) {
    action(() => request(`/api/catalog/${key}/youtube/choice`, {candidate_id, automatic, revision: data.revision}));
  }
  function render(next) {
    data = next; choices.replaceChildren();
    status.textContent = `${data.manual ? window.RadioI18n.t("ui.083e618923f6") : window.RadioI18n.t("ui.b4cc6db0143d")} · ${data.selected?.title || window.RadioI18n.t("ui.8a3d9492df1e")}${data.checked_at ? ' · ' + new Date(data.checked_at).toLocaleString(window.RadioI18n.locale) : ''}`;
    for (const candidate of data.candidates) {
      const button = el('button', `${candidate.title} · ${candidate.channel}${candidate.id === data.selected?.id ? ' ✓' : ''}`); button.type = 'button'; button.className = 'text-button';
      button.onclick = () => choose(candidate.id); choices.append(button);
    }
    if (data.candidates.length || data.selected) {
      const remove = el('button', window.RadioI18n.t("ui.d10ea8f0d777")); remove.type = 'button'; remove.onclick = () => choose(null); choices.append(remove);
      const auto = el('button', window.RadioI18n.t("ui.3396f05d9489")); auto.type = 'button'; auto.onclick = () => choose(null, true); choices.append(auto);
    }
  }
  search.onclick = () => action(() => request(`/api/catalog/${key}/youtube/search`, {}));
  action(() => request(`/api/catalog/${key}/youtube/state`));
  return section;
}};
