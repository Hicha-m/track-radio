// Catalogues contain interface text only. Metadata and source payloads are never translated.
window.RadioI18n = (() => {
  const catalog = JSON.parse(document.querySelector('#i18n-catalog').textContent);
  const fallback = JSON.parse(document.querySelector('#i18n-fallback').textContent);
  const source = new Map(Object.entries(fallback).map(([key, value]) => [value, key]));
  const language = document.documentElement.lang;
  function t(key, params = {}) {
    key = source.get(key) || key;
    return (catalog[key] ?? fallback[key] ?? key).replace(/\{(\w+)\}/g, (match, name) => String(params[name] ?? match));
  }
  // Persist URL-selected language so subsequent internal links retain it.
  document.cookie = `radio_language=${language};path=/;max-age=31536000;SameSite=Lax${location.protocol === 'https:' ? ';Secure' : ''}`;
  document.addEventListener('DOMContentLoaded', () => {
    const select = document.querySelector('#language-select'); select.value = language;
    select.addEventListener('change', () => {
      document.cookie = `radio_language=${select.value};path=/;max-age=31536000;SameSite=Lax${location.protocol === 'https:' ? ';Secure' : ''}`;
      const url = new URL(location.href); url.searchParams.set('lang', select.value); location.assign(url);
    });
  });
  return {t, language, locale: language === 'en' ? 'en-GB' : 'fr-FR', plural: (one, other, count, params = {}) => t(new Intl.PluralRules(language).select(count) === 'one' ? one : other, {...params, count})};
})();
