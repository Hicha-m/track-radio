(() => {
  const storageKey = 'radio-tracker-theme';
  const systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
  let preference = null;

  try {
    const saved = localStorage.getItem(storageKey);
    if (saved === 'dark' || saved === 'light') preference = saved;
  } catch (_) { /* The switch also works when browser storage is unavailable. */ }

  function applyTheme() {
    const dark = preference ? preference === 'dark' : systemTheme.matches;
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    const button = document.querySelector('#theme-toggle');
    if (button) {
      button.hidden = false;
      button.setAttribute('aria-pressed', String(dark));
      button.title = dark ? 'Passer au mode clair' : 'Passer au mode sombre';
    }
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', dark ? '#18181a' : '#ffffff');
  }

  // Runs before the stylesheet to avoid a light flash when opening a dark page.
  applyTheme();
  systemTheme.addEventListener('change', () => { if (!preference) applyTheme(); });
  window.addEventListener('storage', (event) => {
    if (event.key !== storageKey && event.key !== null) return;
    preference = ['dark', 'light'].includes(event.newValue) ? event.newValue : null;
    applyTheme();
  });
  document.addEventListener('DOMContentLoaded', () => {
    applyTheme();
    document.querySelector('#theme-toggle')?.addEventListener('click', () => {
      preference = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      try { localStorage.setItem(storageKey, preference); } catch (_) { /* Keep the in-page choice. */ }
      applyTheme();
    });
  });
})();
