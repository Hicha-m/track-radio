window.RadioExport = (() => {
  let selection = new URLSearchParams();
  let dialog;
  const dates = ['since', 'until', 'seen_since', 'seen_until'];
  function localDate(value) {
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return '';
    return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, -1);
  }
  async function open() {
    dialog?.close(); dialog?.remove();
    dialog = document.querySelector('#export-template').content.firstElementChild.cloneNode(true);
    const panel = dialog;
    const form = panel.querySelector('form');
    const status = panel.querySelector('[data-export-status]');
    const submit = form.querySelector('[type=submit]');
    const fields = panel.querySelector('.export-fields');
    const captured = new URLSearchParams(selection);
    for (const [key, value] of captured) {
      const input = form.elements.namedItem(key);
      if (!input) continue;
      if (input.type === 'checkbox') input.checked = value === 'true';
      else input.value = dates.includes(key) ? localDate(value) : value;
    }
    const view = form.elements.view;
    let schema;
    function renderFields() {
      const previous = fields.children.length ? new Set([...fields.querySelectorAll('input:checked')].map(i => i.value)) : null;
      fields.replaceChildren();
      for (const [key, label] of Object.entries(schema[view.value])) {
        const wrapper = document.createElement('label'); wrapper.className = 'checkbox';
        const input = document.createElement('input'); input.type = 'checkbox'; input.name = 'field'; input.value = key;
        input.checked = previous ? previous.has(key) : ['artist', 'title'].includes(key);
        wrapper.append(input, document.createTextNode(label)); fields.append(wrapper);
      }
      for (const option of form.elements.sort.options) {
        option.disabled = (option.value === 'frequency' && view.value === 'plays') || (option.value === 'station' && view.value === 'tracks');
      }
      if (form.elements.sort.selectedOptions[0].disabled) form.elements.sort.value = view.value === 'tracks' ? 'frequency' : 'newest';
      form.elements.min_plays.disabled = view.value !== 'tracks';
    }
    panel.querySelector('[data-export-close]').onclick = () => panel.close();
    panel.querySelectorAll('[data-fields]').forEach(button => {
      button.onclick = () => fields.querySelectorAll('input').forEach(input => {
        input.checked = button.dataset.fields === 'all' || ['artist', 'title'].includes(input.value);
      });
    });
    view.onchange = () => { if (schema) renderFields(); };
    let controller;
    panel.addEventListener('close', () => controller?.abort(), {once: true});
    form.onsubmit = async event => {
      event.preventDefault();
      const chosen = [...fields.querySelectorAll('input:checked')].map(input => input.value);
      if (!chosen.length) { status.textContent = 'Choisissez au moins un champ.'; return; }
      const params = new URLSearchParams();
      for (const [key, value] of new FormData(form)) {
        if (key !== 'field' && value) params.set(key, dates.includes(key) ? new Date(value).toISOString() : value);
      }
      params.set('fields', chosen.join(','));
      window.MusicFilters.apply(params);
      const filterKey = window.MusicFilters.key();
      submit.disabled = true; status.textContent = 'Préparation de l’export…';
      controller = new AbortController();
      try {
        const response = await fetch(`/api/export?${params}`, {signal: controller.signal, cache: 'no-store'});
        if (!response.ok) {
          const error = await response.json();
          throw new Error(typeof error.detail === 'string' ? error.detail : 'Vérifiez les filtres et les dates de l’export.');
        }
        const blob = await response.blob();
        if (!panel.open) return;
        if (filterKey !== window.MusicFilters.key()) throw new Error('Les exclusions ont changé. Relancez l’export.');
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a'); link.href = url;
        link.download = `radio-${params.get('view')}.${params.get('format')}`;
        document.body.append(link); link.click(); link.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 60000);
        status.textContent = 'Export téléchargé.';
      } catch (error) { if (error.name !== 'AbortError') status.textContent = error.message; }
      finally { submit.disabled = false; }
    };
    window.RadioDialogs.dismissOutside(panel);
    document.body.append(panel); panel.showModal(); submit.disabled = true;
    try {
      const response = await fetch('/api/export/fields');
      if (!response.ok) throw new Error('Les champs sont indisponibles. Fermez puis réessayez.');
      schema = await response.json(); renderFields(); submit.disabled = false;
    } catch (error) { status.textContent = error.message; }
  }
  document.addEventListener('click', event => { if (event.target.closest('[data-open-export]')) open(); });
  window.addEventListener('radio:pagechange', () => { dialog?.close(); });
  return {configure: params => { selection = new URLSearchParams(params); }};
})();
