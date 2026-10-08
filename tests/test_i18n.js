const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const context = vm.createContext({ document: { addEventListener() {} } });
vm.runInContext(fs.readFileSync(path.join(__dirname, '../web/i18n.js'), 'utf8'), context);
function load(catalog) {
  context.catalog = catalog;
  vm.runInContext('translations = catalog; compileTranslationTemplates();', context);
}
function translate(value) {
  context.value = value;
  return vm.runInContext('translateText(value)', context);
}
load({
  'Repeat {item}/{item}': '{item} and {item}',
  'Value ({item}).': 'Literal: {item}',
  'No se pudo leer: {error}': 'Could not read: {error}',
  'Unknown user': 'No such user',
  'Name: {name}': 'Account: {name}',
});
assert.equal(translate('Repeat a/a'), 'a and a');
assert.equal(translate('Repeat a/b'), 'Repeat a/b');
assert.equal(translate('Value ($&${item}\\path).'), 'Literal: $&${item}\\path');
assert.equal(translate('Value ().'), 'Literal: ');
assert.equal(translate('No se pudo leer: Unknown user'), 'Could not read: No such user');
assert.equal(translate('Name: Unknown user'), 'Account: Unknown user');
assert.equal(translate('Uncatalogued message'), 'Uncatalogued message');
for (const language of ['es', 'en']) {
  load(JSON.parse(fs.readFileSync(path.join(__dirname, `../web/locales/${language}.json`), 'utf8')));
  assert.equal(translate('No se pudo leer: Unknown user'), language === 'es'
    ? 'No se pudo leer: Usuario desconocido' : 'Could not read: Unknown user');
  assert.equal(translate('Administrador $&{name} creado.'), language === 'es'
    ? 'Administrador $&{name} creado.' : 'Administrator $&{name} created.');
  // Switching catalogs must recompile patterns, leaving no stale templates.
  assert.equal(translate('Repeat a/a'), 'Repeat a/a');
}
process.stdout.write('PASS: literal captures, nested errors and locale switching\n');

const app = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
const formatter = app.match(/function formatMessage\(template, values\) \{[\s\S]*?\n\}/)[0];
vm.runInContext(formatter, context);
context.parameters = { name: '$&{count}', count: 2 };
assert.equal(vm.runInContext("formatMessage('{count}: {name} / {name}', parameters)", context),
  '2: $&{count} / $&{count}');
assert.equal(vm.runInContext("formatMessage('{missing}', {})", context), '{missing}');

// Exercise every actual template in every supported locale. Values remain data,
// including whitespace, dollars, braces, paths and technical identifiers.
for (const language of ['en', 'de', 'es', 'fr', 'it', 'pt', 'sk', 'sv']) {
  const catalog = JSON.parse(fs.readFileSync(path.join(__dirname, `../web/locales/${language}.json`), 'utf8'));
  load(catalog);
  for (const [source, target] of Object.entries(catalog)) {
    if (!/\{[a-z][a-z0-9_]*\}/i.test(source)) continue;
    const values = {};
    for (const [, name] of source.matchAll(/\{([a-z][a-z0-9_]*)\}/gi)) {
      values[name] = `VALUE_${name}_$&{literal}/two  spaces`;
    }
    const fill = text => text.replace(/\{([a-z][a-z0-9_]*)\}/gi, (_, name) => values[name]);
    assert.equal(translate(fill(source)), fill(target), `${language}: ${source}`);
  }
  assert.equal(translate('Error: Unknown user'), `${catalog.Error}: ${catalog['Unknown user']}`);
  assert.equal(translate('No se pudo leer: Unknown user'),
    catalog['No se pudo leer: {error}'].replace('{error}', catalog['Unknown user']));
}
process.stdout.write('PASS: every template in all eight locales, preserved values and nested service errors\n');

const textNode = { nodeValue: 'Nombre en red: Backup  $&', parentElement: { closest: () => null } };
const ignoredNode = { nodeValue: 'Error', parentElement: { closest: () => ({}) } };
const attributes = { title: 'Abrir /shares/two  spaces' };
const attributeNode = {
  closest: () => null,
  hasAttribute: name => Object.hasOwn(attributes, name),
  getAttribute: name => attributes[name],
  setAttribute: (name, value) => { attributes[name] = value; },
};
context.NodeFilter = { SHOW_TEXT: 4 };
context.document.body = {};
context.document.createTreeWalker = () => {
  const nodes = [textNode, ignoredNode];
  return { nextNode: () => nodes.shift() };
};
context.document.querySelectorAll = () => [attributeNode];
for (const language of ['de', 'pt', 'en', 'es']) {
  const catalog = JSON.parse(fs.readFileSync(path.join(__dirname, `../web/locales/${language}.json`), 'utf8'));
  load(catalog);
  vm.runInContext('translatePage()', context);
  assert.equal(textNode.nodeValue, catalog['Nombre en red: {name}'].replace('{name}', () => 'Backup  $&'));
  assert.equal(attributes.title, catalog['Abrir {path}'].replace('{path}', '/shares/two  spaces'));
  assert.equal(ignoredNode.nodeValue, 'Error');
}
process.stdout.write('PASS: DOM language switching, attributes and ignored identifiers\n');
