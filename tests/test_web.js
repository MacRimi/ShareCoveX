const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const elements = new Map();
function element(selector) {
  if (!elements.has(selector)) {
    elements.set(selector, {
      checked: false,
      value: '',
      listeners: {},
      classList: { toggle() {} },
      querySelectorAll() { return []; },
      addEventListener(type, handler) { this.listeners[type] = handler; },
      append() {},
      replaceChildren() {},
      reset() {},
      scrollIntoView() {},
    });
  }
  return elements.get(selector);
}

element('#nfs-mapping').value = 'root-squash';
element('#smb-enabled').checked = true;
element('#resource-enabled').checked = true;
element('#share-all').checked = true;
element('#name').value = 'backups';

let sent;
const context = vm.createContext({
  document: { querySelector: element },
  fetch(url, options) {
    if (url === '/api/state') return new Promise(() => {});
    sent = { url, options };
    return Promise.resolve({ ok: false, json: async () => ({ error: 'simulated rejection' }) });
  },
  setInterval() {},
  URLSearchParams,
  location: { search: '', pathname: '/' },
  history: { replaceState() {} },
  setTimeout() { return 1; },
  clearTimeout() {},
});
const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
vm.runInContext(source, context);
vm.runInContext('current = {settings: {shares: []}}; activeTarget = {id: "backups", path: ""};', context);

async function submit() {
  await element('#settings-form').listeners.submit({ preventDefault() {} });
}

(async () => {
  await submit();
  assert.match(element('#editor-message').textContent, /Selecciona un usuario Samba/);
  assert.equal(sent, undefined);

  element('#resource-enabled').checked = false;
  await submit();
  assert.equal(JSON.parse(sent.options.body).shares[0].enabled, false);
  assert.equal(JSON.parse(sent.options.body).shares[0].smb_enabled, true);
  element('#resource-enabled').checked = true;

  element('#smb-guest').checked = true;
  element('#read-only').checked = true;
  await submit();
  assert.equal(sent.url, '/api/settings');
  const share = JSON.parse(sent.options.body).shares[0];
  assert.equal(share.smb_guest, true);
  assert.equal(share.read_only, true);
  assert.deepEqual(share.smb_users, []);
  assert.equal(element('#editor-message').textContent, 'simulated rejection');
  vm.runInContext('showSmbAccess()', context);
  assert.equal(element('#read-only').checked, true);
  assert.equal(element('#read-only').disabled, true);

  element('#username').value = 'Pedro';
  element('#password').value = 'a-long-test-password';
  await element('#user-form').listeners.submit({ preventDefault() {} });
  assert.equal(sent.url, '/api/users');
  assert.equal(JSON.parse(sent.options.body).name, 'Pedro');
  assert.match(fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8'),
    /id="username"[^>]*pattern="\[A-Za-z\]/);
  assert.match(fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8'),
    /id="password"[^>]*minlength="8"/);
  // Passwords and confirmations are asked for in the page, never in a browser dialog.
  assert.doesNotMatch(source, /\b(prompt|confirm|alert)\(/);
  element('#server-name').value = 'Cove NAS';
  element('#mdns-enabled').checked = true;
  await element('#discovery-form').listeners.submit({ preventDefault() {} });
  assert.equal(sent.url, '/api/settings');
  assert.equal(JSON.parse(sent.options.body).server_name, 'Cove NAS');
  assert.equal(JSON.parse(sent.options.body).mdns_enabled, true);
  element('#collaborative-mode').checked = true;
  element('#shared-uid').value = '1000';
  element('#shared-gid').value = '1000';
  await element('#permissions-form').listeners.submit({ preventDefault() {} });
  assert.equal(JSON.parse(sent.options.body).shared_uid, 1000);
  assert.equal(JSON.parse(sent.options.body).shared_gid, 1000);
  element('#tm-limit-enabled').checked = true;
  element('#tm-limit-gb').value = '750';
  await element('#time-machine-form').listeners.submit({ preventDefault() {} });
  assert.equal(JSON.parse(sent.options.body).time_machine_max_size_gb, 750);
  const markup = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8');
  vm.runInContext(`savedRenderAdmins = renderAdmins; savedRenderMounts = renderMounts;
    renderAdmins = () => {}; renderMounts = () => {}; render({settings: {server_name: 'ShareCoveX', mdns_enabled: false,
    smb_min_protocol: 'SMB2_02', smb_max_protocol: 'SMB3_11', nfs_protocols: ['3'], shares: []},
    environment: {type: 'lxc', unprivileged: true}, nfs_v4_available: false, users: [], admins: [],
    mounted_folders: [], services: {smb: {enabled: false, configured: false, running: false},
    nfs: {enabled: false, configured: false, running: false},
    mdns: {enabled: false, configured: false, running: false}}, version: 'v0.1.0'})`, context);
  // Each folded Settings card says what it holds.
  assert.equal(element('#users-summary-count').textContent, '');
  assert.equal(element('#users-summary-label').textContent, 'Sin usuarios');
  assert.equal(element('#discovery-summary-name').textContent, 'ShareCoveX');
  assert.equal(element('#discovery-summary-state').textContent, 'Bonjour desactivado');
  assert.equal(element('#tm-summary').textContent, 'Sin limite');
  assert.equal(element('#permissions-summary-mode').textContent, 'Modo colaborativo');
  assert.equal(element('#permissions-summary-ids').textContent, '1000:1000');
  assert.equal(element('#smb-protocol-summary').textContent, 'SMB 2.0.2 – SMB 3.1.1');
  assert.equal(element('#nfs-protocol-summary').textContent, 'NFSv3');
  assert.equal(element('#version').textContent, 'v0.1.0');
  vm.runInContext(`renderSummaries({ settings: { server_name: 'Cove', mdns_enabled: true, time_machine_max_size_gb: 750,
    collaborative_mode: false, shared_uid: 1500, shared_gid: 1600, smb_min_protocol: 'SMB3_11', smb_max_protocol: 'SMB3_11',
    nfs_protocols: ['3', '4'] }, users: ['a', 'b'], admins: ['admin'] })`, context);
  assert.equal(element('#users-summary-count').textContent, '2');
  assert.equal(element('#users-summary-label').textContent, 'usuarios');
  assert.equal(element('#admins-summary-count').textContent, '1');
  assert.equal(element('#admins-summary-label').textContent, 'administrador');
  assert.equal(element('#discovery-summary-state').textContent, 'Bonjour activado');
  assert.equal(element('#tm-summary').textContent, '750 GB');
  assert.equal(element('#permissions-summary-mode').textContent, 'Sin modo colaborativo');
  assert.equal(element('#permissions-summary-ids').textContent, '1500:1600');
  assert.equal(element('#smb-protocol-summary').textContent, 'SMB 3.1.1');
  assert.equal(element('#nfs-protocol-summary').textContent, 'NFSv3 + NFSv4');
  assert.equal(element('#version').textContent, '');
  // Settings: what is needed first comes first, and every card starts folded.
  const order = ['Usuarios Samba', 'Administradores', 'Nombre y descubrimiento', 'Time Machine', 'Identidad y permisos',
    'Compatibilidad de protocolo', 'Protocolos NFS'].map(title => markup.indexOf(`<h2>${title}</h2>`));
  assert.ok(order.every((position, index) => position > 0 && (!index || position > order[index - 1])), order.join());
  assert.equal(markup.match(/class="fold-body" hidden/g).length, 8);
  assert.equal(markup.match(/<h3 class="settings-group">/g).length, 4);
  assert.equal(element('#nfs-protocol-panel').hidden, true);
  assert.equal(element('#nfs-unprivileged-info').hidden, false);
  assert.equal(element('#environment-badge').textContent, 'OCI container');
  vm.runInContext(`current.environment = {type: 'docker', unprivileged: false}; render(current)`, context);
  assert.equal(element('#nfs-protocol-panel').hidden, false);
  assert.equal(element('#nfs-unprivileged-info').hidden, true);
  assert.equal(element('#environment-badge').textContent, 'Docker container');
  vm.runInContext('renderAdmins = savedRenderAdmins; renderMounts = savedRenderMounts;', context);
  assert.match(markup, /id="resource-enabled"/);
  assert.ok(markup.indexOf('id="resource-enabled"') < markup.indexOf('id="share-all"'));
  assert.doesNotMatch(markup, /id="collapse-all"/);
  assert.ok(markup.indexOf('id="smb-options"') < markup.indexOf('id="nfs-options"'));
  assert.ok(markup.indexOf('id="read-only"') < markup.indexOf('id="nfs-options"'));
  assert.equal(element('#smb-options').hidden, false);
  assert.equal(element('#nfs-options').hidden, true);

  element('#nfs-mapping').value = 'mapall';
  vm.runInContext('current.settings.collaborative_mode = false;', context);
  element('#nfs-enabled').checked = true;
  element('#nfs-enabled').listeners.change();
  assert.equal(element('#nfs-options').hidden, false);
  assert.equal(element('#nfs-uid').required, true);
  assert.equal(element('#nfs-gid').required, true);

  element('#time-machine').checked = true;
  element('#smb-enabled').checked = false;
  element('#smb-enabled').listeners.change({ target: element('#smb-enabled') });
  assert.equal(element('#smb-options').hidden, true);
  assert.equal(element('#nfs-options').hidden, false);
  assert.equal(element('#time-machine').checked, false);

  // While Time Machine is selected the NFS section is locked and cannot be switched on.
  let nfsLock;
  element('#nfs-section').classList = { toggle(name, on) { nfsLock = [name, on]; } };
  element('#smb-enabled').checked = true;
  element('#nfs-enabled').checked = false;
  element('#time-machine').checked = true;
  element('#time-machine').listeners.change({ target: element('#time-machine') });
  assert.equal(element('#time-machine').checked, true);
  assert.equal(element('#nfs-enabled').checked, false);
  assert.equal(element('#nfs-enabled').disabled, true);
  assert.equal(element('#nfs-options').hidden, true);
  assert.deepEqual(nfsLock, ['section-locked', true]);
  assert.match(element('#nfs-section').title, /No se puede activar NFS mientras Time Machine/);
  element('#time-machine').checked = false;
  element('#time-machine').listeners.change({ target: element('#time-machine') });
  assert.equal(element('#nfs-enabled').disabled, false);
  assert.deepEqual(nfsLock, ['section-locked', false]);
  assert.equal(element('#nfs-section').title, '');
  element('#smb-enabled').checked = false;

  element('#nfs-enabled').checked = true;
  element('#time-machine').checked = true;
  element('#time-machine').listeners.change({ target: element('#time-machine') });
  assert.equal(element('#time-machine').checked, false);
  assert.match(element('#editor-message').textContent, /No se puede activar Time Machine mientras NFS/);
  assert.equal(element('#smb-guest').checked, false);

  element('#nfs-enabled').checked = false;
  element('#nfs-enabled').listeners.change();
  assert.equal(element('#nfs-options').hidden, true);
  assert.equal(element('#nfs-uid').required, false);
  assert.equal(element('#nfs-gid').required, false);
  assert.equal(element('#resource-enabled').checked, false);
  // A folder that is not shared yet is not shown as paused; a resource that exists is.
  assert.equal(element('#resource-state-hint').hidden, true);
  vm.runInContext("editing = 'backups'; showResourceMode();", context);
  assert.equal(element('#resource-state-hint').hidden, false);
  vm.runInContext("editing = null; showResourceMode();", context);

  assert.doesNotMatch(markup, /id="cancel-edit"|id="share-form-title"/);
  assert.match(source, /save\.setAttribute\('form', 'settings-form'\)/);
  assert.doesNotMatch(source, /Pasar a subcarpetas/);
  const node = () => ({ children: [], listeners: {}, append(...items) { this.children.push(...items); },
    setAttribute(name, value) { this[name] = value; }, addEventListener(type, handler) { this.listeners[type] = handler; } });
  context.document.createElement = node;
  const evaluate = code => JSON.parse(vm.runInContext(`JSON.stringify(${code})`, context));
  vm.runInContext('originalOpenEditor = openEditor;', context);

  // A folder inside a shared one may only add the protocol that is still free.
  vm.runInContext(`current.share_permissions = {'media/movies': {uid: 1000, gid: 1000, mode: '2770', writable: true, shared_uid: 1000, shared_gid: 1000}};
    current.mount_permissions = {};
    current.settings.shares = [
    { id: 'media', path: '', name: 'Media', smb_enabled: true, nfs_enabled: false },
    { id: 'media', path: 'movies', name: 'MoviesNfs', smb_enabled: false, nfs_enabled: true },
    { id: 'backups', path: '', name: 'Backups', smb_enabled: true, nfs_enabled: true },
    { id: 'mac', path: '', name: 'Mac', smb_enabled: true, nfs_enabled: false, time_machine: true },
    { id: 'docs', path: 'a/b', name: 'Deep', enabled: false, smb_enabled: true, nfs_enabled: false }
  ];`, context);
  assert.deepEqual(evaluate("takenProtocols('media', 'movies')"), { smb: true, nfs: false, exclusive: false, any: true });
  assert.deepEqual(evaluate("takenProtocols('media', '')"), { smb: false, nfs: true, exclusive: false, any: true });
  assert.deepEqual(evaluate("takenProtocols('media', 'movies/classics')"), { smb: true, nfs: true, exclusive: false, any: true });
  assert.deepEqual(evaluate("takenProtocols('mac', 'bundle')"), { smb: true, nfs: false, exclusive: true, any: true });
  assert.deepEqual(evaluate("takenProtocols('docs', 'a')"), { smb: true, nfs: false, exclusive: false, any: true });
  assert.deepEqual(evaluate("takenProtocols('docs', 'other')"), { smb: false, nfs: false, exclusive: false, any: false });
  assert.deepEqual(evaluate("inheritedProtocols('media', 'movies')"), { smb: true, nfs: false });
  assert.deepEqual(evaluate("inheritedProtocols('media', '')"), { smb: false, nfs: false });
  assert.deepEqual(evaluate("inheritedProtocols('docs', 'a/b/c')"), { smb: false, nfs: false });

  // Opened, a shared mount shows every subfolder with the protocols that reach it.
  const tree = node();
  context.tree = tree;
  vm.runInContext(`treeCache.set('media/', { children: ['movies', 'shows'], expandable: { movies: true, shows: false }, truncated: false });
    renderFolderTree('media', '', tree);`, context);
  assert.equal(tree.children.length, 2);
  const [movies, shows] = tree.children.map(branch => branch.children[0]);
  assert.match(movies.className, /expandable/);
  assert.equal(movies.children[0].className, 'tree-expand');
  assert.equal(movies.children[0]['aria-expanded'], 'false');
  assert.equal(movies.children[1].children[0].textContent, '/shares/media/movies');
  assert.equal(movies.children[1].children[1].textContent, 'Nombre en red: MoviesNfs');
  assert.equal(movies.children[1].children[2].textContent, 'UID:GID 1000:1000 · 2770 · Compatible con escritura');
  assert.deepEqual(movies.children[2].children.map(badge => [badge.textContent, badge.className]),
    [['SMB', 'protocol-smb'], ['NFS', 'protocol-nfs']]);
  assert.equal(movies.children[3].disabled, false);
  assert.doesNotMatch(shows.className, /expandable/);
  assert.equal(shows.children[0].className, 'tree-spacer');
  assert.equal(shows.children[1].children[1].textContent, 'Incluida en el recurso superior');
  assert.deepEqual(shows.children[2].children.map(badge => [badge.textContent, badge.className]), [['SMB', 'protocol-smb']]);
  assert.equal(shows.children[3].disabled, false);
  assert.equal(shows.children[3].title, 'Configurar esta ruta');

  // Nothing is left to add under a mount shared over both protocols, nor inside a Time Machine destination.
  const full = node();
  context.full = full;
  vm.runInContext(`treeCache.set('backups/', { children: ['x'], expandable: { x: false }, truncated: false });
    treeCache.set('mac/', { children: ['bundle'], expandable: { bundle: false }, truncated: false });
    renderFolderTree('backups', '', full); renderFolderTree('mac', '', full);`, context);
  const [both, exclusive] = full.children.map(branch => branch.children[0]);
  assert.deepEqual(both.children[2].children.map(badge => badge.className), ['protocol-smb', 'protocol-nfs']);
  assert.equal(both.children[3].disabled, true);
  assert.match(both.children[3].title, /no queda ningun protocolo/);
  assert.equal(exclusive.children[3].disabled, true);
  assert.match(exclusive.children[3].title, /Time Machine necesita su carpeta en exclusiva/);
  vm.runInContext("treeCache.clear();", context);
  vm.runInContext('current.settings.shares = [];', context);
  let editorAttached = true;
  context.document.querySelector = selector => selector === '#share-all' && !editorAttached ? null : element(selector);
  context.document.createElement = node;
  element('#editor').remove = () => { editorAttached = false; };
  element('#shares').replaceChildren = () => {};
  element('#shares').append = child => { element('#shares').lastChild = child; };
  element('#resources-view').append = () => {};
  element('#share-all').checked = false;
  vm.runInContext('renderFolderTree = () => {}; current.mounted_folders = ["backups"]; renderMounts();', context);
  const rootRow = element('#shares').lastChild.children[0];
  assert.equal(rootRow.children.find(child => child.textContent === 'Cerrar').textContent, 'Cerrar');
  assert.equal(rootRow.children.some(child => child.textContent === 'Ver subcarpetas' || child.textContent === 'Guardar'), false);
  context.document.querySelector = element;

  // Mounts are folded until clicked; the buttons inside a row do not fold it.
  vm.runInContext('savedTarget = activeTarget; activeTarget = null; renderMounts();', context);
  let card = element('#shares').lastChild;
  assert.equal(card.children.length, 1);
  assert.match(card.children[0].className, /mount-root expandable$/);
  assert.equal(card.children[0].children[0]['aria-expanded'], 'false');
  card.children[0].listeners.click({ target: {} });
  card = element('#shares').lastChild;
  assert.equal(card.children.length, 2);
  assert.equal(card.className, 'mount-card open');
  assert.equal(card.children[1].className, 'mount-tree');
  assert.equal(card.children[0].children[0]['aria-expanded'], 'true');
  card.children[0].listeners.click({ target: { closest: selector => selector === '.route-action' ? {} : null } });
  assert.equal(vm.runInContext('openMounts.has("backups")', context), true);
  card.children[0].listeners.click({ target: {} });
  assert.equal(element('#shares').lastChild.children.length, 1);
  vm.runInContext('activeTarget = savedTarget;', context);
  vm.runInContext('renderMounts = () => {};', context);
  const beforeBrowse = sent;
  await submit();
  assert.equal(sent, beforeBrowse);
  assert.equal(vm.runInContext('activeTarget.id', context), 'backups');
  assert.match(element('#message').textContent, /Elige una subcarpeta/);

  vm.runInContext("current.settings.shares = [{ id: 'backups', path: '', name: 'backups' }]; editing = 'backups'; render = () => {}; openEditor = () => {};", context);
  context.fetch = (url, options) => {
    sent = { url, options };
    return Promise.resolve({ ok: true, json: async () => ({ settings: { shares: [] } }) });
  };
  await submit();
  assert.equal(sent.url, '/api/settings');
  assert.deepEqual(JSON.parse(sent.options.body).shares, []);
  assert.match(element('#message').textContent, /subcarpetas/);

  vm.runInContext(`current.settings.shares = [{ id: 'backups', path: '', name: 'backups', enabled: true,
    smb_enabled: true, nfs_enabled: false, smb_guest: true, read_only: true }]; editing = 'backups';`, context);
  element('#share-all').checked = true;
  element('#smb-enabled').checked = false;
  element('#nfs-enabled').checked = false;
  const beforeRemoval = sent;
  await submit();
  assert.notEqual(sent, beforeRemoval);
  const paused = JSON.parse(sent.options.body).shares[0];
  assert.equal(paused.enabled, false);
  assert.equal(paused.smb_enabled, true);
  assert.equal(paused.smb_guest, true);
  assert.match(element('#message').textContent, /pausado/);

  vm.runInContext('current.settings.shares = []; editing = null; activeTarget = {id: "backups", path: ""};', context);
  // A folder that is not shared yet and has no protocol switched on creates nothing.
  element('#share-all').checked = true;
  element('#resource-enabled').checked = false;
  sent = undefined;
  await submit();
  assert.equal(sent, undefined);
  assert.match(element('#editor-message').textContent, /Activa el protocolo que quieres anadir/);
  // Per-destination Time Machine size travels with the resource and only with Time Machine.
  vm.runInContext('current.settings.shares = []; editing = null; activeTarget = {id: "backups", path: ""};', context);
  element('#resource-enabled').checked = true;
  element('#smb-enabled').checked = true;
  element('#nfs-enabled').checked = false;
  element('#smb-guest').checked = false;
  element('#read-only').checked = false;
  element('#time-machine').checked = true;
  element('#tm-share-limit').value = '300';
  element('#share-all').checked = true;
  element('#smb-users').querySelectorAll = () => [{ value: 'pedro' }];
  sent = undefined;
  await submit();
  const destination = JSON.parse(sent.options.body).shares[0];
  assert.equal(destination.time_machine, true);
  assert.equal(destination.time_machine_max_size_gb, 300);
  vm.runInContext('current.settings.shares = []; editing = null; activeTarget = {id: "backups", path: ""};', context);
  element('#resource-enabled').checked = true;
  element('#smb-enabled').checked = true;
  element('#share-all').checked = true;
  element('#time-machine').checked = false;
  element('#tm-share-limit').value = '300';
  sent = undefined;
  await submit();
  assert.equal(JSON.parse(sent.options.body).shares[0].time_machine, false);
  assert.equal(JSON.parse(sent.options.body).shares[0].time_machine_max_size_gb, 0);

  // The editor of a folder that is not shared opens with nothing switched on.
  element('#smb-users').querySelectorAll = () => [];
  vm.runInContext("current.settings.shares = []; originalOpenEditor('media', 'plain', undefined);", context);
  assert.equal(element('.resource-state').hidden, true);
  assert.equal(element('#smb-enabled').checked, false);
  assert.equal(element('#nfs-enabled').checked, false);
  assert.equal(element('#smb-section').hidden, false);
  assert.equal(element('#nfs-section').hidden, false);
  // Switching a protocol on is what shares it; without a user the save says where the problem is.
  element('#smb-enabled').checked = true;
  element('#smb-enabled').listeners.change({ target: element('#smb-enabled') });
  assert.equal(element('#resource-enabled').checked, true);
  assert.equal(element('#smb-options').hidden, false);
  let marked;
  element('#smb-user-field').classList = { toggle(name, on) { marked = [name, on]; } };
  let moved = false;
  element('#smb-user-field').before = item => { moved = item === element('#editor-message'); };
  sent = undefined;
  await submit();
  assert.equal(sent, undefined);
  assert.match(element('#editor-message').textContent, /Selecciona un usuario Samba/);
  assert.deepEqual(marked, ['invalid', true]);
  assert.equal(moved, true);
  element('#smb-enabled').checked = false;
  element('#smb-enabled').listeners.change({ target: element('#smb-enabled') });
  assert.equal(element('#resource-enabled').checked, false);
  assert.deepEqual(marked, ['invalid', true]);
  vm.runInContext('resetForm();', context);
  assert.deepEqual(marked, ['invalid', false]);
  // An existing resource keeps its pause switch and opens with what it has.
  vm.runInContext(`current.settings.shares = [{ id: 'media', path: 'plain', name: 'Plain', enabled: false, smb_enabled: true,
    nfs_enabled: false, smb_browseable: true, smb_encryption: 'default', smb_clients: [], read_only: false, nfs_read_only: false,
    nfs_clients: [], nfs_mapping: 'root-squash', nfs_insecure: false, time_machine: false, smb_users: ['pedro'] }];
    originalOpenEditor('media', 'plain', current.settings.shares[0]);`, context);
  assert.equal(element('.resource-state').hidden, false);
  assert.equal(element('#resource-enabled').checked, false);
  assert.equal(element('#smb-enabled').checked, true);
  vm.runInContext('resetForm(); current.settings.shares = [];', context);
  element('#smb-users').querySelectorAll = () => [{ value: 'pedro' }];

  // Editing a folder inside an SMB share offers only NFS, switched off, and sends just that.
  vm.runInContext(`current.settings.shares = [{ id: 'media', path: '', name: 'Media', smb_enabled: true, nfs_enabled: false }];
    originalOpenEditor('media', 'shows', undefined);`, context);
  assert.equal(element('#smb-section').hidden, true);
  assert.equal(element('#smb-enabled').disabled, true);
  assert.equal(element('#smb-enabled').checked, false);
  assert.equal(element('#nfs-section').hidden, false);
  assert.equal(element('#nfs-enabled').disabled, false);
  assert.equal(element('#nfs-enabled').checked, false);
  assert.equal(element('#time-machine').disabled, true);
  assert.equal(vm.runInContext('openMounts.has("media")', context), true);
  // Saved as it opens, with nothing switched on, it adds nothing.
  sent = undefined;
  await submit();
  assert.equal(sent, undefined);
  assert.match(element('#editor-message').textContent, /Activa el protocolo que quieres anadir/);
  element('#nfs-enabled').checked = true;
  element('#name').value = 'ShowsNfs';
  element('#resource-enabled').checked = true;
  element('#clients').value = '192.168.0.0/24';
  sent = undefined;
  await submit();
  const added = JSON.parse(sent.options.body).shares;
  assert.deepEqual(added.map(item => [item.path, item.smb_enabled, item.nfs_enabled]), [['', true, false], ['shows', false, true]]);
  assert.equal(element('#smb-enabled').disabled, false);
  assert.equal(element('#smb-section').hidden, false);
  assert.equal(element('#time-machine').disabled, false);
  // The protocol the outer share already publishes is refused if it is sent anyway.
  vm.runInContext(`current.settings.shares = [{ id: 'media', path: '', name: 'Media', smb_enabled: true, nfs_enabled: false }];
    originalOpenEditor('media', 'shows', undefined);`, context);
  element('#smb-enabled').checked = true;
  element('#smb-guest').checked = true;
  element('#resource-enabled').checked = true;
  sent = undefined;
  await submit();
  assert.equal(sent, undefined);
  assert.match(element('#editor-message').textContent, /se solapa con un recurso existente/);
  element('#smb-guest').checked = false;
  element('#clients').value = '';
  vm.runInContext('resetForm(); current.settings.shares = [];', context);

  // A folder without extended attributes says so in a sentence of its own.
  const details = node();
  vm.runInContext('appendPermissions', context)(details, { uid: 1000, gid: 1000, mode: '2770', writable: true, xattr: true });
  assert.equal(details.children.length, 1);
  vm.runInContext('appendPermissions', context)(details, { uid: 1000, gid: 1000, mode: '2770', writable: true, xattr: false });
  assert.equal(details.children[2].textContent, 'Sin atributos extendidos: macOS y Time Machine los necesitan.');

  // A new password is typed twice in the account's own row and is never shown.
  const made = [];
  context.document.createElement = () => { const item = node(); made.push(item); return item; };
  vm.runInContext(`current = { settings: { shares: [] }, users: ['pedro'], admins: ['admin'] };
    accountAction = { kind: 'users', name: 'pedro', action: 'password' };`, context);
  let row = vm.runInContext("accountRow('users', 'pedro', {}, 'Nueva clave')", context);
  assert.deepEqual(row.children.slice(1, 3).map(item => item.textContent), ['Nueva clave', 'Eliminar']);
  const form = row.children[3];
  assert.equal(form.className, 'account-action');
  const [first, second] = form.children.slice(0, 2).map(label => label.children[1]);
  assert.deepEqual([first.type, second.type, first.autocomplete, first.minLength], ['password', 'password', 'new-password', 8]);
  first.value = 'eight888';
  second.value = 'eight889';
  sent = undefined;
  await form.listeners.submit({ preventDefault() {} });
  assert.equal(sent, undefined);
  assert.equal(form.children[3].textContent, 'Las contrasenas no coinciden. No se ha realizado ningun cambio.');
  assert.equal(form.children[3].hidden, false);
  second.value = 'eight888';
  context.fetch = (url, options) => { sent = { url, options }; return Promise.resolve({ ok: true, status: 200, json: async () => ({ settings: { shares: [] }, users: ['pedro'] }) }); };
  vm.runInContext('render = state => { shown = state; };', context);
  await form.listeners.submit({ preventDefault() {} });
  assert.equal(sent.url, '/api/users/pedro/password');
  assert.deepEqual(JSON.parse(sent.options.body), { password: 'eight888' });
  assert.equal(vm.runInContext('accountAction', context), null);
  assert.equal(element('#message').textContent, 'Contrasena actualizada.');
  // Removing an account is confirmed in the same place, and an account with nothing open shows only its buttons.
  vm.runInContext("accountAction = { kind: 'admins', name: 'pedro', action: 'delete' };", context);
  row = vm.runInContext("accountRow('admins', 'pedro', {}, 'Cambiar contrasena')", context);
  assert.equal(row.children[3].children[0].textContent, 'Este administrador dejara de poder entrar en el panel.');
  assert.equal(vm.runInContext("accountRow('admins', 'admin', {}, 'Cambiar contrasena')", context).children.length, 3);
  sent = undefined;
  await row.children[3].children[2].children[0].listeners.click();
  assert.equal(sent.url, '/api/admins/pedro');
  assert.equal(sent.options.method, 'DELETE');
  assert.equal(element('#message').textContent, 'Administrador eliminado.');
  await vm.runInContext("accountRow('admins', 'admin', {}, 'Cambiar contrasena')", context).children[2].listeners.click();
  assert.deepEqual(evaluate('accountAction'), { kind: 'admins', name: 'admin', action: 'delete' });
  vm.runInContext('accountAction = null;', context);
  context.document.createElement = node;

  // A button that asks again in place runs on the second click only.
  const armed = node();
  armed.innerHTML = '<span>Eliminar configuracion</span>';
  armed.classList = { toggle() {} };
  let runs = 0;
  context.armed = armed;
  context.count = () => { runs += 1; };
  vm.runInContext('confirmInPlace(armed, count);', context);
  armed.listeners.click({});
  assert.equal(runs, 0);
  assert.equal(armed.textContent, 'Pulsa otra vez para confirmar');
  armed.listeners.click({});
  assert.equal(runs, 1);
  assert.equal(armed.innerHTML, '<span>Eliminar configuracion</span>');

  // Sign-in: the panel stays hidden until the server accepts a session.
  const answer = (status, body) => (url, options) => {
    sent = { url, options };
    return Promise.resolve({ ok: status < 400, status, json: async () => body });
  };
  vm.runInContext('showPanel()', context);
  assert.equal(element('#panel').hidden, false);
  assert.equal(element('#login-view').hidden, true);
  assert.equal(element('#logout').hidden, false);
  context.fetch = answer(401, { error: 'Sesion no iniciada' });
  await assert.rejects(vm.runInContext("api('/api/state')", context), /Inicia sesion para continuar/);
  assert.equal(element('#panel').hidden, true);
  assert.equal(element('#login-view').hidden, false);
  assert.equal(element('#logout').hidden, true);
  assert.equal(vm.runInContext('signedIn', context), false);

  // The browser sends the form itself, so it can offer to remember the password.
  assert.equal(element('#login-form').listeners.submit, undefined);
  assert.match(markup, /<form id="login-form"[^>]*method="post" action="\/login"/);
  assert.match(markup, /id="login-name" name="username" autocomplete="username"/);
  assert.match(markup, /id="login-password" name="password" type="password" autocomplete="current-password"/);
  const notice = vm.runInContext('loginNotice', context);
  assert.equal(notice('?login=failed'), 'Usuario o contrasena incorrectos');
  assert.match(notice('?login=wait'), /Demasiados intentos/);
  assert.equal(notice(''), '');
  assert.equal(notice('?login=anything'), '');
  vm.runInContext('showLogin(loginNotice("?login=failed"))', context);
  assert.equal(element('#login-message').textContent, 'Usuario o contrasena incorrectos');
  assert.equal(element('#login-message').hidden, false);
  assert.equal(element('#panel').hidden, true);
  vm.runInContext('showPanel()', context);

  context.fetch = answer(200, { signed_out: true });
  await element('#logout').listeners.click();
  assert.equal(sent.url, '/api/logout');
  assert.equal(element('#panel').hidden, true);
  assert.equal(element('#login-view').hidden, false);
  assert.match(markup, /<main id="panel" hidden>/);
  // The logo is drawn, not typed: it must not depend on a font of the visitor's system.
  assert.match(markup, /<div class="mark" aria-label="ShareCoveX"><svg class="mark-letters" aria-hidden="true" viewBox="[\d. ]+"><path /);
  assert.doesNotMatch(markup, />SCX</);
  assert.doesNotMatch(source, /Authorization|btoa\(/);
  process.stdout.write('PASS: resource pausing, protocol panels, publication transitions, SMB guest access, Time Machine sizes and sign-in\n');
})().catch(error => { console.error(error); process.exitCode = 1; });

// Exercise the actual translator independently of the application DOM.
const i18nContext = vm.createContext({ document: { addEventListener() {} } });
vm.runInContext(fs.readFileSync(path.join(__dirname, '../web/i18n.js'), 'utf8'), i18nContext);
const translate = value => vm.runInContext(`translateText(${JSON.stringify(value)})`, i18nContext);
const loadCatalog = catalog => {
  i18nContext.catalog = catalog;
  vm.runInContext('translations = catalog; compileTranslationTemplates();', i18nContext);
};
loadCatalog({
  'Hello {name}, {count}!': '{count}: welcome, {name} / {name}!',
  'Same {name}/{name}': 'Repeated {name}',
  'Error ({detail}) [x].': 'Failure: {detail}',
  '{name} · {state}': '{name}: {state}',
  '{name} · Pausado ({protocols})': '{name}: paused ({protocols})',
  'Empty: {value}.': 'Value: {value}.',
  'Fixed text': 'Exact translation',
});
assert.equal(translate('  Hello $&{count}, 2!\n'), '  2: welcome, $&{count} / $&{count}!\n');
assert.equal(translate('Same Alice/Alice'), 'Repeated Alice');
assert.equal(translate('Same Alice/Bob'), 'Same Alice/Bob');
assert.equal(translate('Error (a+b?) [x].'), 'Failure: a+b?');
assert.equal(translate('Cove · Pausado (SMB + NFS)'), 'Cove: paused (SMB + NFS)');
assert.equal(translate('Empty: .'), 'Value: .');
assert.equal(translate('  Fixed \n text  '), '  Exact translation  ');
assert.equal(translate('Unknown text'), 'Unknown text');
for (const locale of ['en', 'es']) {
  loadCatalog(JSON.parse(fs.readFileSync(path.join(__dirname, `../web/locales/${locale}.json`), 'utf8')));
  assert.equal(translate('Nombre en red: Backup$&'), locale === 'en' ? 'Network name: Backup$&' : 'Nombre en red: Backup$&');
  assert.equal(translate('Unknown SMB users: Alice, Bob'), locale === 'en' ? 'Unknown SMB users: Alice, Bob' : 'Usuarios SMB desconocidos: Alice, Bob');
  const permissionTemplate = 'UID:GID {uid}:{gid} · {mode} · Compatible con escritura';
  const permissionCatalog = JSON.parse(fs.readFileSync(path.join(__dirname, `../web/locales/${locale}.json`), 'utf8'));
  const permissionValues = { uid: '1000', gid: '1001', mode: '2770' };
  assert.equal(translate('UID:GID 1000:1001 · 2770 · Compatible con escritura'),
    permissionCatalog[permissionTemplate].replace(/\{(uid|gid|mode)\}/g, (_, name) => permissionValues[name]));
}
process.stdout.write('PASS: named translation templates and English/Spanish catalogs\n');
