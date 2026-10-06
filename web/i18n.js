'use strict';

const SHARECOVEX_LANGUAGES = {
  en: 'English', de: 'Deutsch', es: 'Español', fr: 'Français',
  it: 'Italiano', pt: 'Português', sk: 'Slovenčina', sv: 'Svenska'
};
const LANGUAGE_STORAGE_KEY = 'sharecovex-ui-language';
let translations = {};
let observer;
let translating = false;
const originalText = new WeakMap();
const originalAttributes = new WeakMap();

function supportedLanguage(value) {
  const code = (value || '').split('-')[0].toLowerCase();
  return SHARECOVEX_LANGUAGES[code] ? code : null;
}

function translateText(value) {
  const compact = value.trim().replace(/\s+/g, ' ');
  if (!compact || !translations[compact]) return value;
  return value.replace(compact, translations[compact]);
}

function translatePage() {
  if (translating) return;
  translating = true;
  observer?.disconnect();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    if (node.parentElement?.closest('[data-i18n-ignore], script, style')) continue;
    if (!originalText.has(node)) originalText.set(node, node.nodeValue);
    node.nodeValue = translateText(originalText.get(node));
  }
  for (const element of document.querySelectorAll('[aria-label], [title], [placeholder]')) {
    let values = originalAttributes.get(element);
    if (!values) {
      values = {};
      for (const name of ['aria-label', 'title', 'placeholder']) {
        if (element.hasAttribute(name)) values[name] = element.getAttribute(name);
      }
      originalAttributes.set(element, values);
    }
    for (const [name, value] of Object.entries(values)) element.setAttribute(name, translateText(value));
  }
  translating = false;
  observer?.observe(document.body, { childList: true, subtree: true, characterData: true,
    attributes: true, attributeFilter: ['aria-label', 'title', 'placeholder'] });
}

async function setLanguage(locale) {
  locale = supportedLanguage(locale) || 'en';
  translations = await fetch(`/locales/${locale}.json`).then(response => {
    if (!response.ok) throw new Error(`Locale ${locale} unavailable`);
    return response.json();
  }).catch(() => ({}));
  try { localStorage.setItem(LANGUAGE_STORAGE_KEY, locale); } catch (_) {}
  document.documentElement.lang = locale;
  document.querySelector('#language-select').value = locale;
  translatePage();
}

document.addEventListener('DOMContentLoaded', () => {
  const select = document.querySelector('#language-select');
  for (const [code, name] of Object.entries(SHARECOVEX_LANGUAGES)) {
    const option = document.createElement('option');
    option.value = code;
    option.textContent = name;
    select.append(option);
  }
  select.addEventListener('change', event => setLanguage(event.target.value));
  observer = new MutationObserver(mutations => {
    for (const mutation of mutations) {
      if (mutation.type === 'characterData') originalText.set(mutation.target, mutation.target.nodeValue);
      if (mutation.type === 'attributes') {
        const values = originalAttributes.get(mutation.target) || {};
        values[mutation.attributeName] = mutation.target.getAttribute(mutation.attributeName);
        originalAttributes.set(mutation.target, values);
      }
    }
    queueMicrotask(translatePage);
  });
  let stored;
  try { stored = localStorage.getItem(LANGUAGE_STORAGE_KEY); } catch (_) {}
  setLanguage(supportedLanguage(stored) || supportedLanguage(navigator.language) || 'en');
});
