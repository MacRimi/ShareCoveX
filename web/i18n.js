'use strict';

const SHARECOVEX_LANGUAGES = {
  en: 'English', de: 'Deutsch', es: 'Español', fr: 'Français',
  it: 'Italiano', pt: 'Português', sk: 'Slovenčina', sv: 'Svenska'
};
const LANGUAGE_STORAGE_KEY = 'sharecovex-ui-language';
let translations = {};
let translationTemplates = [];
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
  if (!compact) return value;
  const wrap = translated => value.match(/^\s*/)[0] + translated + value.match(/\s*$/)[0];
  if (Object.hasOwn(translations, compact)) return wrap(translations[compact]);
  for (const template of translationTemplates) {
    const match = value.trim().match(template.pattern);
    if (!match) continue;
    // One pass keeps captured dollars and placeholder-like text literal.
    return wrap(template.translation.replace(/\{([a-z][a-z0-9_]*)\}/gi,
      (token, name) => {
        if (!Object.hasOwn(template.groups, name)) return token;
        const captured = match[template.groups[name]];
        // Error wrappers can contain another catalogued server message. Other
        // captures (account names, paths, command output) remain literal data.
        return name === 'error' && captured !== compact ? translateText(captured) : captured;
      }));
  }
  return value;
}

function compileTranslationTemplates() {
  const escape = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  translationTemplates = Object.entries(translations).flatMap(([source, translation]) => {
    const groups = Object.create(null);
    let cursor = 0;
    let expression = '';
    let count = 0;
    let specificity = 0;
    for (const match of source.matchAll(/\{([a-z][a-z0-9_]*)\}/gi)) {
      const literal = source.slice(cursor, match.index).replace(/\s+/g, ' ');
      expression += escape(literal);
      specificity += literal.length;
      const name = match[1];
      if (Object.hasOwn(groups, name)) expression += `\\k<p${groups[name]}>`;
      else {
        groups[name] = ++count;
        expression += `(?<p${count}>.*?)`;
      }
      cursor = match.index + match[0].length;
    }
    if (!count) return [];
    const tail = source.slice(cursor).replace(/\s+/g, ' ');
    expression += escape(tail);
    specificity += tail.length;
    return [{ pattern: new RegExp(`^${expression}$`, 's'), groups, translation, specificity }];
  }).sort((a, b) => b.specificity - a.specificity);
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
    if (element.closest('[data-i18n-ignore]')) continue;
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
  compileTranslationTemplates();
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
