// The native modal keeps keyboard focus inside the mobile navigation drawer.
(() => {
  const sidebar = document.querySelector('.sidebar');
  const drawer = document.querySelector('#mobile-navigation');
  const toggle = document.querySelector('#mobile-nav-toggle');
  const marker = document.createComment('desktop sidebar position');
  sidebar.before(marker);
  const mobile = window.matchMedia('(max-width: 760px)');
  function layout() {
    drawer.close();
    if (mobile.matches) drawer.append(sidebar);
    else marker.after(sidebar);
  }
  toggle.addEventListener('click', () => drawer.showModal());
  document.querySelector('#mobile-nav-close').addEventListener('click', () => drawer.close());
  drawer.addEventListener('click', event => { if (event.target.closest('a[href], [data-open-search]')) drawer.close(); });
  window.RadioDialogs.dismissOutside(drawer);
  mobile.addEventListener('change', layout);
  window.addEventListener('radio:pagechange', () => drawer.close());
  layout();
})();
