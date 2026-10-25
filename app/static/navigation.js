// Keep the document shell and its audio element alive across internal navigation.
(() => {
  let cleanup;
  let pending;
  let generation = 0;
  const notice = document.querySelector('#navigation-status');
  const adminPaths = ['/admin', '/admin/dashboard', '/admin/radios', '/admin/diffusions', '/admin/catalogue', '/admin/exclusions'];
  const paths = ['/', '/recherche', '/stats', ...adminPaths];
  function mount() {
    const pages = {'/': 'charts', '/recherche': 'search', '/stats': 'stats'};
    const page = location.pathname === '/admin' ? 'adminLogin' : adminPaths.includes(location.pathname) ? 'admin' : pages[location.pathname] || 'charts';
    cleanup = window.RadioPages[page]();
    window.dispatchEvent(new Event('radio:pagechange'));
  }
  function scrollTo(url) {
    let target;
    try { target = url.hash && document.getElementById(decodeURIComponent(url.hash.slice(1))); } catch (_) { /* Invalid hash. */ }
    if (target) target.scrollIntoView({block: 'start'});
    else window.scrollTo({top: 0, behavior: 'instant'});
  }
  async function navigate(url, push = true, replace = false) {
    pending?.abort();
    pending = new AbortController();
    const current = ++generation;
    const main = document.querySelector('#page-content');
    main.setAttribute('aria-busy', 'true');
    notice.hidden = true;
    try {
      const response = await fetch(url.pathname + url.search, {signal: pending.signal});
      if (!response.ok) throw new Error('Page indisponible. Réessayez dans un instant.');
      // A protected page can redirect to login, including during client navigation.
      if (response.redirected) { url = new URL(response.url); replace = true; }
      const html = new DOMParser().parseFromString(await response.text(), 'text/html');
      if (current !== generation) return;
      const next = html.querySelector('#page-content');
      if (!next) throw new Error('Impossible de charger cette page.');
      if (html.body.dataset.assetVersion !== document.body.dataset.assetVersion) {
        throw new Error('Une nouvelle version est disponible. Rechargez la page après votre écoute.');
      }
      await window.MusicFilters.refresh();
      if (current !== generation) return;
      cleanup?.();
      document.querySelectorAll('.catalog-dialog, .admin-edit-dialog').forEach(dialog => { dialog.close(); dialog.remove(); });
      main.replaceWith(document.importNode(next, true));
      const navigation = html.querySelector('.sidebar nav');
      document.querySelector('.sidebar nav').replaceWith(document.importNode(navigation, true));
      document.title = html.title;
      if (replace) history.replaceState(null, '', url.pathname + url.search + url.hash);
      else if (push) history.pushState(null, '', url.pathname + url.search + url.hash);
      mount();
      scrollTo(url);
      const heading = document.querySelector('h1');
      heading.setAttribute('tabindex', '-1');
      heading.focus({preventScroll: true});
    } catch (error) {
      if (current === generation && error.name !== 'AbortError') {
        notice.textContent = error.message;
        notice.hidden = false;
      }
    } finally {
      if (current === generation) document.querySelector('#page-content').removeAttribute('aria-busy');
    }
  }
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href]');
    if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || link.hasAttribute('download') || (link.target && link.target !== '_self')) return;
    const url = new URL(link.href, location.href);
    if (url.origin !== location.origin || !paths.includes(url.pathname)) return;
    if (url.pathname === location.pathname && url.search === location.search && url.hash) return;
    event.preventDefault();
    navigate(url);
  });
  window.RadioNavigation = {go(path, replace = false) {
    const url = new URL(path, location.href);
    if (url.origin === location.origin && paths.includes(url.pathname)) return navigate(url, true, replace);
  }};
  window.addEventListener('popstate', () => navigate(new URL(location.href), false));
  Promise.all([window.RadioAdmin.refresh(), window.MusicFilters.refresh()])
    .catch(error => { notice.textContent = error.message; notice.hidden = false; })
    .finally(mount);
})();
