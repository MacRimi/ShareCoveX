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
    /id="password"[^>]*minlength="6"/);
  assert.match(source, /Nueva contrasena para.*6 caracteres minimo/);
  assert.match(source, /Confirma la nueva contrasena para/);
  assert.match(source, /Las contrasenas no coinciden/);
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
    mdns: {enabled: false, configured: false, running: false}}})`, context);
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

  element('#time-machine').checked = true;
  element('#nfs-enabled').checked = true;
  element('#nfs-enabled').listeners.change({ target: element('#nfs-enabled') });
  assert.equal(element('#nfs-enabled').checked, false);
  assert.match(element('#editor-message').textContent, /No se puede activar NFS mientras Time Machine/);

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
  assert.equal(element('#resource-state-hint').hidden, false);

  assert.doesNotMatch(markup, /id="cancel-edit"|id="share-form-title"/);
  assert.match(source, /save\.setAttribute\('form', 'settings-form'\)/);
  assert.doesNotMatch(source, /Pasar a subcarpetas/);
  const node = () => ({ children: [], append(...items) { this.children.push(...items); }, setAttribute() {}, addEventListener() {} });
  context.document.createElement = node;
  const published = node();
  context.published = published;
  vm.runInContext(`current.share_permissions = {'media/movies': {uid: 1000, gid: 1000, mode: '2770', writable: true, shared_uid: 1000, shared_gid: 1000}};
    current.settings.shares = [
    { id: 'media', path: 'movies', name: 'Peliculas', smb_enabled: true, nfs_enabled: false },
    { id: 'media', path: 'private/nested', name: 'Privado', enabled: false, smb_enabled: false, nfs_enabled: true },
    { id: 'backups', path: '', name: 'Backups', smb_enabled: true, nfs_enabled: false }
  ]; renderPublishedRoutes('media', published);`, context);
  assert.equal(published.children[0].children.length, 2);
  assert.equal(published.children[0].children[0].children[0].children[0].textContent, '/shares/media/movies');
  assert.equal(published.children[0].children[0].children[0].children[1].textContent, 'Nombre en red: Peliculas');
  assert.equal(published.children[0].children[0].children[0].children[2].textContent, 'UID:GID 1000:1000 · 2770 · Compatible con escritura');
  assert.equal(published.children[0].children[0].children[1].children[0].textContent, 'SMB');
  assert.equal(published.children[0].children[1].children[0].children[0].textContent, '/shares/media/private/nested');
  assert.equal(published.children[0].children[1].children[1].children[0].textContent, 'Pausado');
  vm.runInContext('renderPublishedRoutes("backups", published);', context);
  assert.equal(published.children.length, 1);
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
  vm.runInContext('renderMounts = () => {};', context);
  const beforeBrowse = sent;
  await submit();
  assert.equal(sent, beforeBrowse);
  assert.equal(vm.runInContext('activeTarget.id', context), 'backups');
  assert.match(element('#message').textContent, /Elige una subcarpeta/);

  vm.runInContext("current.settings.shares = [{ id: 'backups', path: '', name: 'backups' }]; editing = 'backups'; render = () => {}; openEditor = () => {};", context);
  context.confirm = () => false;
  await submit();
  assert.equal(sent, beforeBrowse);
  context.confirm = () => true;
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
  element('#share-all').checked = true;
  element('#resource-enabled').checked = false;
  await submit();
  const unshared = JSON.parse(sent.options.body).shares[0];
  assert.equal(unshared.enabled, false);
  assert.equal(unshared.smb_enabled, false);
  assert.equal(unshared.nfs_enabled, false);
  process.stdout.write('PASS: resource pausing, protocol panels, publication transitions and SMB guest access\n');
})().catch(error => { console.error(error); process.exitCode = 1; });
