// Format source messages in one pass; the page translator localizes them.
function formatMessage(template, values) {
  return template.replace(/\{([a-z][a-z0-9_]*)\}/gi,
    (token, name) => Object.hasOwn(values, name) ? String(values[name]) : token);
}

const $ = selector => document.querySelector(selector);
const editorElement = $('#editor');
let current;
let editing = null;
let activeTarget = null;
let expandedPaths = new Set();
let openMounts = new Set();
let treeCache = new Map();
let treePending = new Map();
let pendingButton = null;
let messageTimer = null;
let editorMessageTimer = null;
let recoveryTimer = null;

const actionIcons = {
  edit: '<svg class="action-icon" aria-hidden="true" viewBox="0 0 24 24"><path d="M20 7h-9"/><path d="M14 17H5"/><circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/></svg>',
  save: '<svg class="action-icon" aria-hidden="true" viewBox="0 0 24 24"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z"/><path d="M17 21v-8H7v8M7 3v5h8"/></svg>',
  delete: '<svg class="action-icon" aria-hidden="true" viewBox="0 0 24 24"><path d="M3 6h18M8 6V4h8v2M19 6l-1 15H6L5 6M10 11v6M14 11v6"/></svg>',
  chevron: '<svg class="chevron" aria-hidden="true" viewBox="0 0 24 24"><path d="M9 6l6 6-6 6"/></svg>'
};

function setActionContent(button, icon, label) {
  button.innerHTML = `${actionIcons[icon]}<span>${label}</span>`;
}

for (const button of document.querySelectorAll?.('.save-action') || []) {
  setActionContent(button, 'save', button.textContent.trim());
}
const deleteResourceButton = $('#delete-resource');
setActionContent(deleteResourceButton, 'delete', deleteResourceButton.textContent?.trim() || 'Eliminar configuracion');
const overlaps = (left, right) => !left || !right || left === right || left.startsWith(right + '/') || right.startsWith(left + '/');
const treeKey = (id, path) => `${id}/${path}`;
const isAbove = (ancestor, path) => ancestor !== path && (!ancestor || path.startsWith(ancestor + '/'));

// What the shares above and below a route already publish. A folder inside a
// shared one may add the protocol that is still free or, over Samba, access
// of its own; a Time Machine destination keeps its folder to itself.
function takenProtocols(id, path) {
  const taken = { smb: false, smbAbove: false, nfs: false, exclusive: false, any: false };
  for (const share of current.settings.shares) {
    if (share.id !== id || share.path === path || !overlaps(share.path, path)) continue;
    taken.any = true;
    taken.smb = taken.smb || !!share.smb_enabled;
    taken.smbAbove = taken.smbAbove || (!!share.smb_enabled && isAbove(share.path, path));
    taken.nfs = taken.nfs || !!share.nfs_enabled;
    taken.exclusive = taken.exclusive || !!share.time_machine;
  }
  return taken;
}

// The protocols a folder is already reached by through the shares above it.
function inheritedProtocols(id, path) {
  const inherited = { smb: false, nfs: false };
  for (const share of current.settings.shares) {
    if (share.id !== id || share.enabled === false || !isAbove(share.path, path)) continue;
    inherited.smb = inherited.smb || !!share.smb_enabled;
    inherited.nfs = inherited.nfs || !!share.nfs_enabled;
  }
  return inherited;
}

function appendBadges(row, share, inherited) {
  const badges = document.createElement('div');
  badges.className = 'route-badges';
  const add = (text, className) => {
    const badge = document.createElement('span');
    if (className) badge.className = className;
    badge.textContent = text;
    badges.append(badge);
  };
  if (share && share.enabled === false) add('Pausado');
  for (const [key, label] of [['smb', 'SMB'], ['nfs', 'NFS']]) {
    // A protocol reached through the share above looks the same as one of its own.
    if ((share && share.enabled !== false && share[`${key}_enabled`]) || inherited[key]) add(label, `protocol-${key}`);
  }
  if (badges.children.length) row.append(badges);
}
const routePath = (id, path) => `/shares/${id}${path ? '/' + path : ''}`;
const permissionsText = access => access.writable ? formatMessage('UID:GID {uid}:{gid} · {mode} · Compatible con escritura', access) : formatMessage('UID:GID {uid}:{gid} · {mode} · Sin escritura para {shared_uid}:{shared_gid}', access);
// A fixed sentence in its own element, so the language catalogue can match it.
// Where a mount comes from on the server. A ZFS dataset is named with the
// folder inside it; anything else is the path inside its disk or volume.
function mountOrigin(source) {
  const inside = source.path && source.path !== '/' ? source.path : '';
  if (source.fstype === 'zfs') return source.device + inside;
  return inside ? `${inside} · ${source.device}` : source.device;
}

function appendPermissions(parent, access) {
  const permissions = document.createElement('small');
  permissions.className = `route-permissions ${access.writable ? 'writable' : 'not-writable'}`;
  permissions.textContent = permissionsText(access);
  parent.append(permissions);
  if (access.xattr === false) {
    const note = document.createElement('small');
    note.className = 'route-permissions not-writable';
    note.textContent = 'Sin atributos extendidos: macOS y Time Machine los necesitan.';
    parent.append(note);
  }
}

['password', 'admin-password'].forEach(id => {
  const button = $(`[data-password-target="${id}"]`);
  const input = $(`#${id}`);
  button.addEventListener('click', () => {
    const visible = input.type === 'password';
    input.type = visible ? 'text' : 'password';
    button.setAttribute('aria-pressed', String(visible));
    button.setAttribute('aria-label', visible ? 'Ocultar contrasena' : 'Mostrar contrasena');
  });
});

function startButtonFeedback(button) {
  if (!button || button.disabled) return;
  pendingButton = button;
  button.dataset.originalContent = button.innerHTML;
  button.textContent = 'Guardando...';
  button.disabled = true;
  button.classList.add('is-busy');
  button.setAttribute('aria-busy', 'true');
}

function finishButtonFeedback(success) {
  const button = pendingButton;
  pendingButton = null;
  if (!button) return;
  button.classList.remove('is-busy');
  button.classList.add(success ? 'is-saved' : 'is-failed');
  button.textContent = success ? 'Guardado' : 'Reintentar';
  button.removeAttribute('aria-busy');
  setTimeout(() => {
    if (button.dataset.originalContent) button.innerHTML = button.dataset.originalContent;
    else button.textContent = 'Guardar';
    button.disabled = false;
    button.classList.remove('is-saved', 'is-failed');
  }, success ? 900 : 1400);
}

document.addEventListener?.('submit', event => {
  const button = event.submitter || event.target.querySelector?.('button[type="submit"]');
  startButtonFeedback(button);
}, true);

async function loadTree(id, path = '') {
  const key = treeKey(id, path);
  if (treeCache.has(key)) return treeCache.get(key);
  if (!treePending.has(key)) {
    const request = api(`/api/tree?mount=${encodeURIComponent(id)}&path=${encodeURIComponent(path)}`)
      .then(tree => { treeCache.set(key, tree); return tree; })
      .finally(() => treePending.delete(key));
    treePending.set(key, request);
  }
  return treePending.get(key);
}

const isNetworkError = error => ['Load failed', 'Failed to fetch', 'NetworkError when attempting to fetch resource.'].includes(error?.message);

async function api(path, method = 'GET', body, retry = true) {
  try {
    const response = await fetch(path, {
      method,
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: 'no-store'
    });
    const result = await response.json();
    if (response.status === 401) {
      showLogin();
      throw new Error('Inicia sesion para continuar.');
    }
    if (!response.ok) throw new Error(result.error || 'Error del servidor');
    if (method !== 'GET') finishButtonFeedback(true);
    if (result.signed_out) showLogin();
    return result;
  } catch (error) {
    if (method !== 'GET') finishButtonFeedback(false);
    if (isNetworkError(error) && method === 'GET' && retry) {
      await new Promise(resolve => setTimeout(resolve, 450));
      return api(path, method, body, false);
    }
    if (isNetworkError(error)) {
      if (method !== 'GET') scheduleStateRecovery();
      throw new Error(method === 'GET'
        ? 'No se pudo comunicar con ShareCoveX. Comprueba la conexión y vuelve a intentarlo.'
        : 'La conexión se interrumpió al guardar. Comprobando si el cambio se aplicó…');
    }
    throw error;
  }
}

let signedIn = false;

function showLogin(text = '') {
  signedIn = false;
  $('#panel').hidden = true;
  $('#logout').hidden = true;
  $('#login-view').hidden = false;
  const notice = $('#login-message');
  notice.textContent = text;
  notice.hidden = !text;
}

function showPanel() {
  signedIn = true;
  $('#login-view').hidden = true;
  $('#panel').hidden = false;
  $('#logout').hidden = false;
}

function scheduleStateRecovery() {
  clearTimeout(recoveryTimer);
  recoveryTimer = setTimeout(async () => {
    try {
      render(await api('/api/state'));
      message('Conexión recuperada. El estado mostrado está actualizado.');
    } catch (_) {}
  }, 1200);
}

function message(value, error = false) {
  const element = $('#message');
  clearTimeout(messageTimer);
  messageTimer = null;
  element.textContent = value;
  element.hidden = !value;
  element.classList.toggle('error', error);
  if (value && !error) messageTimer = setTimeout(() => {
    if (element.textContent !== value) return;
    element.textContent = '';
    element.hidden = true;
  }, 5000);
}

let invalidField = null;
const lockedProtocols = { smb: false, nfs: false };

// `field` is the control the message is about: the message is placed right
// above it and the control is marked, so the reason is read where it is fixed.
function editorMessage(value, error = false, field = null) {
  const element = $('#editor-message');
  clearTimeout(editorMessageTimer);
  editorMessageTimer = null;
  invalidField?.classList.toggle('invalid', false);
  invalidField = value ? field : null;
  invalidField?.classList.toggle('invalid', true);
  if (field && value) field.before?.(element);
  else if (element.parentElement !== editorElement) editorElement.prepend?.(element);
  element.textContent = value;
  element.hidden = !value;
  element.classList.toggle('error', error);
  // A save that was refused here never reaches the server: free its button.
  if (value && error) finishButtonFeedback(false);
  if (value) element.scrollIntoView({ behavior: 'smooth', block: 'center' });
  if (value && !error) editorMessageTimer = setTimeout(() => {
    if (element.textContent !== value) return;
    element.textContent = '';
    element.hidden = true;
  }, 5000);
}

function serviceStatus(state) {
  for (const service of ['smb', 'nfs']) {
    const item = state.services[service];
    const label = $(`#${service}-status`);
    const legacy = service === 'nfs' && state.settings.nfs_backend === 'legacy-v4' && item.enabled;
    const nfsUnverified = service === 'nfs' && item.enabled && item.running;
    const switchedOn = state.settings[`${service}_service_enabled`];
    const statusText = !switchedOn ? 'Desactivado' : !item.configured ? 'Activo, sin recursos' : legacy ? 'Migracion pendiente' : nfsUnverified ? 'Escuchando' : item.running ? 'En marcha' : 'Error';
    const statusKind = !switchedOn ? 'inactive' : !item.configured ? 'running' : legacy ? 'pending' : item.running ? 'running' : 'failed';
    label.textContent = '';
    label.className = `status service-indicator ${statusKind}`;
    label.ariaLabel = statusText;
    const detail = switchedOn ? item.error || (nfsUnverified ? 'Comprueba el montaje y los permisos desde otro cliente.' : '') : '';
    const statusTemplates = {
      'Desactivado': 'Desactivado: {error}',
      'Activo, sin recursos': 'Activo, sin recursos: {error}',
      'Migracion pendiente': 'Migracion pendiente: {error}',
      'Escuchando': 'Escuchando: {error}',
      'En marcha': 'En marcha: {error}',
      'Error': 'Error: {error}'
    };
    label.title = detail ? formatMessage(statusTemplates[statusText], { error: detail }) : statusText;
    $(`#${service}-service`).checked = switchedOn;
  }
  const discovery = state.services.mdns;
  const label = $('#mdns-status');
  label.textContent = !state.settings.mdns_enabled ? 'Desactivado' :
    !discovery.configured ? 'Sin recursos SMB' :
    !state.settings.smb_service_enabled ? 'SMB desactivado' :
    discovery.running ? 'Anunciando' :
    !state.services.smb.running ? 'Esperando SMB' : 'Error';
  label.className = `status ${discovery.running ? 'running' : discovery.enabled && state.services.smb.running ? 'failed' : ''}`;
  label.title = discovery.error || '';
  const protocols = state.settings.nfs_protocols || ['3'];
  $('#nfs-endpoint').textContent = protocols.length === 2 ? 'NFSv3 + NFSv4' : formatMessage('NFSv{version}', { version: protocols[0] });
}

function showNfsMapping() {
  const collaborative = current?.settings?.collaborative_mode !== false;
  $('#nfs-identity-field').hidden = collaborative;
  $('#nfs-collaborative-hint').hidden = !collaborative;
  const custom = !collaborative && $('#nfs-enabled').checked && $('#nfs-mapping').value !== 'root-squash';
  for (const id of ['uid', 'gid']) {
    $(`#nfs-${id}-field`).hidden = !custom;
    $(`#nfs-${id}`).required = custom && $('#resource-enabled').checked;
  }
}

function showSmbAccess() {
  const guest = $('#smb-guest').checked;
  const selectedUsers = $('#smb-users').querySelectorAll('input:checked').length;
  const offered = addingSmbAccess || $('#smb-enabled').checked;
  $('#smb-guest').disabled = !offered || inheritedSmb.guest;
  $('#smb-user-field').hidden = !offered;
  $('#smb-guest-hint').hidden = !guest;
  $('#read-only').disabled = guest && !selectedUsers;
  $('#tm-share-limit-field').hidden = !$('#time-machine').checked;
  if (guest) {
    $('#time-machine').checked = false;
    if (!selectedUsers) $('#read-only').checked = true;
  }
}

// A Time Machine destination is a Samba-only folder: while it is selected the
// NFS section is dimmed and its switch cannot be turned on.
function showTimeMachineLock() {
  const timeMachine = $('#time-machine').checked;
  if (timeMachine) $('#nfs-enabled').checked = false;
  $('#nfs-enabled').disabled = timeMachine || lockedProtocols.nfs;
  $('#nfs-section').classList.toggle('section-locked', timeMachine);
  $('#nfs-section').title = timeMachine
    ? 'No se puede activar NFS mientras Time Machine esta activo. Time Machine necesita un recurso exclusivo de Samba, con escritura, usuario autenticado y sin NFS.'
    : '';
}

function showProtocolOptions() {
  showTimeMachineLock();
  $('#smb-options').hidden = !addingSmbAccess && !$('#smb-enabled').checked;
  $('#nfs-options').hidden = !$('#nfs-enabled').checked;
  showResourceMode();
  showNfsMapping();
  showSmbAccess();
}

// What a share above or below this route already gives is not offered again:
// the editor shows what is left to add.
// A folder inside a Samba share is already shared: there is no switch to
// share it. The access it has through the shares above is shown as it is and
// cannot be taken away; adding some is what makes it a resource of its own.
const noSmbAccess = () => ({ guest: false, users: new Set(), writers: new Set() });
let addingSmbAccess = false;
let inheritedSmb = noSmbAccess();
function inheritedSmbAccess(id, path) {
  const access = noSmbAccess();
  for (const share of current.settings.shares) {
    if (share.id !== id || !share.smb_enabled || !isAbove(share.path, path)) continue;
    const users = share.smb_users ?? current.users ?? [];
    access.guest = access.guest || share.smb_guest === true;
    for (const user of users) access.users.add(user);
    if (!share.read_only) for (const user of users) access.writers.add(user);
  }
  return access;
}
function syncAddedSmbAccess() {
  if (!addingSmbAccess) return;
  const chosen = [...$('#smb-users').querySelectorAll('input:checked')].map(input => input.value);
  $('#smb-enabled').checked = ($('#smb-guest').checked && !inheritedSmb.guest)
    || chosen.some(user => !inheritedSmb.users.has(user))
    || (!$('#read-only').checked && chosen.some(user => !inheritedSmb.writers.has(user)));
  syncResourceState();
  showResourceMode();
}

function showInheritance() {
  const taken = activeTarget ? takenProtocols(activeTarget.id, activeTarget.path)
    : { smb: false, smbAbove: false, nfs: false, exclusive: false, any: false };
  for (const key of ['smb', 'nfs']) {
    // Over Samba a folder inside a shared one can still add access of its
    // own; an NFS export cannot be repeated inside another one.
    const locked = taken.exclusive || (key === 'nfs' && taken.nfs);
    lockedProtocols[key] = locked;
    const input = $(`#${key}-enabled`);
    if (locked) input.checked = false;
    input.disabled = locked;
    $(`#${key}-section`).hidden = locked;
  }
  addingSmbAccess = taken.smbAbove && !taken.exclusive;
  $('#smb-added-hint').hidden = !addingSmbAccess;
  for (const id of ['#smb-enabled-switch', '#smb-scope-hint', '#time-machine-switch', '#time-machine-hint']) $(id).hidden = addingSmbAccess;
  inheritedSmb = addingSmbAccess ? inheritedSmbAccess(activeTarget.id, activeTarget.path) : noSmbAccess();
  // A resource that already adds access here keeps what it chose on top.
  const own = $('#smb-enabled').checked;
  if (addingSmbAccess) {
    if (inheritedSmb.guest) $('#smb-guest').checked = true;
    else if (!own) $('#smb-guest').checked = false;
    if (!own) {
      const inheritedUsers = [...inheritedSmb.users];
      $('#read-only').checked = !inheritedUsers.length
        || inheritedUsers.some(user => !inheritedSmb.writers.has(user));
    }
  }
  for (const input of $('#smb-users').querySelectorAll('input')) {
    const inherited = inheritedSmb.users.has(input.value);
    if (inherited) input.checked = true;
    else if (addingSmbAccess && !own) input.checked = false;
    input.disabled = inherited;
  }
  syncAddedSmbAccess();
  if (taken.any) $('#time-machine').checked = false;
  $('#time-machine').disabled = taken.any;
}

// With no protocol nothing is shared; a route that is not created yet is
// shared as soon as one is switched on, since it has no pause of its own.
function syncResourceState() {
  const any = $('#smb-enabled').checked || $('#nfs-enabled').checked;
  if (!any) $('#resource-enabled').checked = false;
  else if (!editing) $('#resource-enabled').checked = true;
}

function showResourceMode() {
  // Only a resource that exists can be paused; a new one is simply not shared yet.
  const paused = !!editing && !$('#resource-enabled').checked;
  $('#resource-state-hint').hidden = !paused;
  $('.protocol-sections').classList.toggle('resource-paused', paused);
}

function resetForm() {
  editing = null;
  activeTarget = null;
  $('#settings-form').reset();
  $('#delete-resource').hidden = true;
  editorMessage('');
  showInheritance();
  showProtocolOptions();
  editorElement.hidden = true;
  renderMounts();
}

function showRootMode() {
  const isRoot = activeTarget && !activeTarget.path;
  const browse = isRoot && !$('#share-all').checked;
  $('#root-mode').hidden = !isRoot;
  $('#subfolder-hint').hidden = !browse;
  $('#route-config').hidden = browse;
  $('#name').required = !browse;
  if (browse) {
    const rootShare = current.settings.shares.some(share => share.id === activeTarget.id && !share.path);
    $('#subfolder-hint').textContent = rootShare
      ? 'Guardar dejara de publicar el montaje entero, sin borrar sus datos. Despues podras publicar subcarpetas con permisos distintos.'
      : 'Elige una subcarpeta del arbol y pulsa Editar para configurar sus protocolos y permisos propios.';
  }
}

function openEditor(id, path, share) {
  $('#settings-form').reset();
  editorMessage('');
  activeTarget = { id, path };
  editing = share?.name || null;
  $('#delete-resource').hidden = !share;
  // Pausing is for a resource that exists; a new one starts with nothing shared.
  $('.resource-state').hidden = !share;
  $('#resource-enabled').checked = false;
  $('#smb-enabled').checked = false;
  $('#nfs-enabled').checked = false;
  if (path) {
    openMounts.add(id);
    const parts = path.split('/');
    for (let i = 1; i < parts.length; i++) expandedPaths.add(treeKey(id, parts.slice(0, i).join('/')));
  }
  if (!path) {
    $('#share-all').checked = !!share;
  }
  const basename = (path.split('/').pop() || id).replace(/[^A-Za-z0-9_-]/g, '_');
  $('#name').value = share?.name || (/^[A-Za-z]/.test(basename) ? basename : `Share_${basename}`).slice(0, 32);
  if (share) {
    $('#resource-enabled').checked = share.enabled !== false;
    $('#smb-enabled').checked = share.smb_enabled;
    $('#nfs-enabled').checked = share.nfs_enabled;
    $('#smb-browseable').checked = share.smb_browseable;
    $('#smb-encryption').value = share.smb_encryption;
    $('#smb-clients').value = share.smb_clients.join('\n');
    $('#read-only').checked = share.read_only;
    $('#nfs-read-only').checked = share.nfs_read_only;
    $('#clients').value = share.nfs_clients.join('\n');
    $('#nfs-mapping').value = share.nfs_mapping;
    $('#nfs-uid').value = share.nfs_uid ?? '';
    $('#nfs-gid').value = share.nfs_gid ?? '';
    $('#nfs-insecure').checked = share.nfs_insecure;
    $('#time-machine').checked = share.time_machine;
    $('#tm-share-limit').value = share.time_machine_max_size_gb || '';
    $('#smb-guest').checked = share.smb_guest === true;
  }
  for (const input of $('#smb-users').querySelectorAll('input')) {
    input.checked = !!share && (share.smb_users === null || share.smb_users.includes(input.value));
  }
  showInheritance();
  showProtocolOptions();
  showRootMode();
  editorElement.hidden = false;
  renderMounts();
  editorElement.previousElementSibling?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function toggleRoute(id, path, root) {
  const key = treeKey(id, path);
  if (root) {
    // A mount with its editor open stays as it is until the edit is saved or cancelled.
    if (activeTarget?.id === id) return;
    if (openMounts.has(id)) openMounts.delete(id);
    else openMounts.add(id);
  } else if (expandedPaths.has(key)) {
    expandedPaths.delete(key);
    if (activeTarget?.id === id && activeTarget.path.startsWith(path + '/')) {
      resetForm();
      return;
    }
  } else {
    try {
      const tree = await loadTree(id, path);
      if (tree.children.length || tree.truncated) expandedPaths.add(key);
    } catch (error) { message(error.message, true); return; }
  }
  renderMounts();
}

function renderRoute(id, path, parent, root = false, browsingRoot = false, open = false) {
  const share = current.settings.shares.find(item => item.id === id && item.path === path);
  const taken = takenProtocols(id, path);
  const inherited = inheritedProtocols(id, path);
  const row = document.createElement('div');
  const selected = activeTarget?.id === id && activeTarget.path === path;
  const key = treeKey(id, path);
  const knownTree = !root && treeCache.get(key);
  const leaf = knownTree && !knownTree.children.length && !knownTree.truncated;
  const parentPath = path.split('/').slice(0, -1).join('/');
  const parentTree = !root && treeCache.get(treeKey(id, parentPath));
  const mayExpand = root || (!leaf && parentTree?.expandable?.[path.split('/').pop()] !== false);
  if (!root) open = expandedPaths.has(key);
  row.className = `route-row${root ? ' mount-root' : ''}${selected ? ' selected' : ''}${mayExpand ? ' expandable' : ''}${open ? ' open' : ''}`;
  if (mayExpand) {
    const expander = document.createElement('button');
    expander.type = 'button';
    expander.className = 'tree-expand';
    expander.innerHTML = actionIcons.chevron;
    expander.setAttribute('aria-expanded', String(open));
    expander.setAttribute('aria-label', open ? formatMessage('Contraer {path}', { path: routePath(id, path) }) : formatMessage('Abrir {path}', { path: routePath(id, path) }));
    row.append(expander);
    // The whole row opens and closes the folder; its own buttons keep their job.
    row.addEventListener('click', event => {
      if (event.target.closest?.('.route-action')) return;
      toggleRoute(id, path, root);
    });
  } else {
    const spacer = document.createElement('span');
    spacer.className = 'tree-spacer';
    row.append(spacer);
  }
  const label = document.createElement('div');
  label.className = 'route-label';
  const title = document.createElement('strong');
  title.setAttribute('data-i18n-ignore', '');
  title.textContent = routePath(id, path);
  const detail = document.createElement('small');
  const below = current.settings.shares.filter(item => item.id === id && isAbove(path, item.path)).length;
  const protocols = share ? [share.smb_enabled ? 'SMB' : '', share.nfs_enabled ? 'NFS' : ''].filter(Boolean).join(' + ') : '';
  if (share && root) detail.textContent = share.enabled === false ? (protocols ? formatMessage('{name} · Pausado ({protocols})', { name: share.name, protocols }) : formatMessage('{name} · Pausado', { name: share.name })) : formatMessage('{name} · {protocols}', { name: share.name, protocols });
  else if (share) detail.textContent = formatMessage('Nombre en red: {name}', { name: share.name });
  else if (inherited.smb || inherited.nfs) detail.textContent = 'Incluida en el recurso superior';
  else if (below) detail.textContent = below === 1 ? formatMessage('{count} subcarpeta configurada', { count: below }) : formatMessage('{count} subcarpetas configuradas', { count: below });
  else detail.textContent = 'Directorio disponible · No compartido en red';
  if (leaf) {
    const suffix = document.createElement('span');
    suffix.textContent = ' · Sin subcarpetas';
    detail.append(suffix);
  }
  label.append(title, detail);
  const source = root && current.mount_sources?.[id];
  if (source) {
    const origin = document.createElement('small');
    origin.className = 'route-origin';
    origin.textContent = formatMessage('Origen en el servidor: {origin}', { origin: mountOrigin(source) });
    label.append(origin);
  }
  const access = root ? current.mount_permissions?.[id] : current.share_permissions?.[key];
  if (share && access) appendPermissions(label, access);
  row.append(label);
  appendBadges(row, share, inherited);
  if (selected) {
    const save = document.createElement('button');
    save.type = 'submit';
    save.setAttribute('form', 'settings-form');
    save.className = 'route-action save';
    setActionContent(save, 'save', 'Guardar');
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'route-action';
    cancel.textContent = root && browsingRoot && !share ? 'Cerrar' : 'Cancelar';
    cancel.addEventListener('click', resetForm);
    if (root && browsingRoot && !share) row.append(cancel);
    else row.append(save, cancel);
  } else {
    const edit = document.createElement('button');
    edit.type = 'button';
    edit.className = 'route-action edit';
    setActionContent(edit, 'edit', 'Editar');
    // Nothing can be added inside a Time Machine destination.
    const full = !share && !root && taken.exclusive;
    edit.disabled = full;
    edit.title = full ? 'Un destino Time Machine necesita su carpeta en exclusiva.' : 'Configurar esta ruta';
    edit.addEventListener('click', () => openEditor(id, path, share));
    row.append(edit);
  }
  parent.append(row);
  if (selected) parent.append(editorElement);
}

function renderFolderTree(id, path, parent) {
  const key = treeKey(id, path);
  const tree = treeCache.get(key);
  if (!tree) {
    const loading = document.createElement('p');
    loading.className = 'tree-info';
    loading.textContent = 'Leyendo subcarpetas...';
    parent.append(loading);
    loadTree(id, path).then(() => {
      if (current.mounted_folders.includes(id)) renderMounts();
    }).catch(error => { loading.textContent = formatMessage('No se pudo leer: {error}', { error: error.message }); });
    return;
  }
  if (!tree.children.length) {
    const empty = document.createElement('p');
    empty.className = 'tree-info';
    empty.textContent = 'No hay subcarpetas en esta ruta.';
    parent.append(empty);
  }
  for (const child of tree.children) {
    const childPath = [path, child].filter(Boolean).join('/');
    const branch = document.createElement('div');
    branch.className = `route-branch${activeTarget?.id === id && activeTarget.path === childPath ? ' editing' : ''}`;
    renderRoute(id, childPath, branch);
    if (expandedPaths.has(treeKey(id, childPath))) {
      const children = document.createElement('div');
      children.className = 'route-children';
      renderFolderTree(id, childPath, children);
      branch.append(children);
    }
    parent.append(branch);
  }
  if (tree.truncated) {
    const warning = document.createElement('p');
    warning.className = 'tree-info';
    warning.textContent = 'Hay mas de 300 subcarpetas; no se muestran todas.';
    parent.append(warning);
  }
}

function renderMounts() {
  const list = $('#shares');
  const browsingRoot = activeTarget && !activeTarget.path && !$('#share-all').checked;
  editorElement.remove();
  list.replaceChildren();
  if (!current.mounted_folders.length) {
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.textContent = 'Todavia no hay directorios montados en /shares.';
    list.append(empty);
  }
  for (const id of current.mounted_folders) {
    const mount = document.createElement('article');
    const editingRoot = activeTarget?.id === id && !activeTarget.path;
    // Folded by default; a mount opens when it is clicked or while one of its folders is being edited.
    const open = openMounts.has(id) || (editingRoot && browsingRoot) || (activeTarget?.id === id && !!activeTarget.path);
    mount.className = `mount-card${editingRoot ? ' editing' : ''}${open ? ' open' : ''}`;
    renderRoute(id, '', mount, true, editingRoot && browsingRoot, open);
    if (open) {
      const tree = document.createElement('div');
      tree.className = 'mount-tree';
      renderFolderTree(id, '', tree);
      mount.append(tree);
    }
    list.append(mount);
  }
  if (!editorElement.isConnected) $('#resources-view').append(editorElement);
}

// The account whose password is being changed or whose removal is being
// confirmed, in its own row: { kind: 'admins' | 'users', name, action }.
let accountAction = null;

const ACCOUNT_TEXTS = {
  admins: { removal: 'Este administrador dejara de poder entrar en el panel.', removed: 'Administrador eliminado.' },
  users: { removal: 'Este usuario dejara de poder entrar por SMB. Sus archivos no se borraran.', removed: 'Usuario eliminado.' }
};

function accountButton(text, className, run) {
  const button = document.createElement('button');
  button.type = 'button';
  if (className) button.className = className;
  button.textContent = text;
  button.addEventListener('click', run);
  return button;
}

function setAccountAction(value) {
  accountAction = value;
  renderAdmins(current.admins || []);
  renderUsers(current);
}

function passwordInput(text) {
  const label = document.createElement('label');
  const caption = document.createElement('span');
  caption.textContent = text;
  const input = document.createElement('input');
  input.type = 'password';
  input.autocomplete = 'new-password';
  input.minLength = 8;
  input.maxLength = 256;
  input.required = true;
  label.append(caption, input);
  return [label, input];
}

// What the buttons of an account open, below its row: a form for the new
// password, typed twice and never shown, or the confirmation of its removal.
function accountPanel(kind, name) {
  if (!accountAction || accountAction.kind !== kind || accountAction.name !== name) return null;
  const close = () => setAccountAction(null);
  const notice = document.createElement('p');
  notice.className = 'message error';
  notice.hidden = true;
  const fail = text => {
    notice.textContent = text;
    notice.hidden = false;
    finishButtonFeedback(false);
  };
  if (accountAction.action === 'password') {
    const form = document.createElement('form');
    form.className = 'account-action';
    const [first, password] = passwordInput('Nueva contrasena');
    const [second, repeated] = passwordInput('Repite la contrasena');
    const hint = document.createElement('small');
    hint.textContent = 'Minimo 8 caracteres.';
    const save = document.createElement('button');
    save.type = 'submit';
    save.textContent = 'Guardar';
    const actions = document.createElement('div');
    actions.className = 'account-buttons';
    actions.append(save, accountButton('Cancelar', 'secondary', close));
    form.append(first, second, hint, notice, actions);
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (password.value !== repeated.value) {
        fail('Las contrasenas no coinciden. No se ha realizado ningun cambio.');
        return;
      }
      try {
        const state = await api(`/api/${kind}/${name}/password`, 'POST', { password: password.value });
        accountAction = null;
        render(state);
        message('Contrasena actualizada.');
      } catch (error) { fail(error.message); }
    });
    return form;
  }
  const panel = document.createElement('div');
  panel.className = 'account-action';
  const warning = document.createElement('p');
  warning.textContent = ACCOUNT_TEXTS[kind].removal;
  const actions = document.createElement('div');
  actions.className = 'account-buttons';
  actions.append(accountButton('Eliminar', 'danger', async () => {
    try {
      const state = await api(`/api/${kind}/${name}`, 'DELETE');
      accountAction = null;
      render(state);
      message(ACCOUNT_TEXTS[kind].removed);
    } catch (error) { fail(error.message); }
  }), accountButton('Cancelar', 'secondary', close));
  panel.append(warning, notice, actions);
  return panel;
}

function accountRow(kind, name, details, passwordLabel) {
  const row = document.createElement('li');
  const open = action => () => setAccountAction({ kind, name, action });
  row.append(details, accountButton(passwordLabel, '', open('password')), accountButton('Eliminar', 'danger', open('delete')));
  const panel = accountPanel(kind, name);
  if (panel) row.append(panel);
  return row;
}

function renderAdmins(names) {
  const list = $('#admins');
  list.replaceChildren();
  $('#admins-empty').hidden = names.length > 0;
  for (const name of names) {
    const title = document.createElement('strong');
    title.setAttribute('data-i18n-ignore', '');
    title.textContent = name;
    list.append(accountRow('admins', name, title, 'Cambiar contrasena'));
  }
}

function renderUsers(state) {
  const users = $('#users');
  users.replaceChildren();
  $('#users-empty').hidden = state.users.length > 0;
  for (const name of state.users) {
    const details = document.createElement('div');
    details.className = 'user-details';
    const title = document.createElement('strong');
    title.setAttribute('data-i18n-ignore', '');
    title.textContent = name;
    details.append(title);
    const permitted = state.settings.shares.filter(share => share.enabled !== false && share.smb_enabled &&
      (share.smb_users === null || share.smb_users.includes(name)));
    if (!permitted.length) {
      const empty = document.createElement('small');
      empty.textContent = 'Sin recursos autorizados';
      details.append(empty);
    }
    for (const share of permitted) {
      const access = document.createElement('button');
      access.type = 'button';
      access.className = 'user-access';
      access.textContent = share.read_only ? formatMessage('{name} · lectura', { name: share.name }) : formatMessage('{name} · lectura/escritura', { name: share.name });
      access.title = formatMessage('Editar permisos de {name}', { name: share.name });
      access.addEventListener('click', () => {
        view('resources');
        openEditor(share.id, share.path, share);
      });
      details.append(access);
    }
    users.append(accountRow('users', name, details, 'Nueva clave'));
  }
}

const SMB_VERSIONS = { SMB2_02: 'SMB 2.0.2', SMB2_10: 'SMB 2.1', SMB3_00: 'SMB 3.0', SMB3_02: 'SMB 3.0.2', SMB3_11: 'SMB 3.1.1' };

// One line per folded card with what it holds now. Numbers and names go in
// their own element so the fixed words next to them can be translated.
function renderSummaries(state) {
  const settings = state.settings;
  const count = (id, total, one, many, none) => {
    $(`#${id}-summary-count`).textContent = total ? String(total) : '';
    $(`#${id}-summary-label`).textContent = !total ? none : total === 1 ? one : many;
  };
  count('users', state.users.length, 'usuario', 'usuarios', 'Sin usuarios');
  count('admins', (state.admins || []).length, 'administrador', 'administradores', '');
  $('#discovery-summary-name').setAttribute?.('data-i18n-ignore', '');
  $('#discovery-summary-name').textContent = settings.server_name;
  $('#discovery-summary-state').textContent = settings.mdns_enabled ? 'Bonjour activado' : 'Bonjour desactivado';
  const limit = settings.time_machine_max_size_gb || 0;
  $('#tm-summary').textContent = limit ? formatMessage('{limit} GB', { limit }) : 'Sin limite';
  $('#permissions-summary-mode').textContent = settings.collaborative_mode !== false ? 'Modo colaborativo' : 'Sin modo colaborativo';
  $('#permissions-summary-ids').textContent = formatMessage('{uid}:{gid}', { uid: settings.shared_uid ?? 1000, gid: settings.shared_gid ?? 1000 });
  const low = SMB_VERSIONS[settings.smb_min_protocol], high = SMB_VERSIONS[settings.smb_max_protocol];
  $('#smb-protocol-summary').textContent = low === high ? low : formatMessage('{low} – {high}', { low, high });
  $('#nfs-protocol-summary').textContent = (settings.nfs_protocols || ['3']).map(item => formatMessage('NFSv{version}', { version: item })).join(' + ');
  $('#version').textContent = state.version || '';
}

function render(state) {
  if (state.signed_out) return;
  current = state;
  showPanel();
  serviceStatus(state);
  $('#environment-badge').textContent = state.environment?.type === 'lxc' ? 'OCI container' : 'Docker container';
  $('#server-name').value = state.settings.server_name;
  $('#mdns-enabled').checked = state.settings.mdns_enabled;
  $('#nfs-migration').hidden = state.settings.nfs_backend !== 'legacy-v4';
  $('#smb-min-protocol').value = state.settings.smb_min_protocol;
  $('#smb-max-protocol').value = state.settings.smb_max_protocol;
  $('#collaborative-mode').checked = state.settings.collaborative_mode !== false;
  $('#shared-uid').value = state.settings.shared_uid ?? 1000;
  $('#shared-gid').value = state.settings.shared_gid ?? 1000;
  const tmLimit = state.settings.time_machine_max_size_gb || 0;
  $('#tm-limit-enabled').checked = tmLimit > 0;
  $('#tm-limit-gb').value = tmLimit || 500;
  $('#tm-limit-field').hidden = !tmLimit;
  const nfsProtocols = state.settings.nfs_protocols || ['3'];
  const unprivilegedLxc = state.environment?.type === 'lxc' && state.environment.unprivileged;
  $('#nfs-protocol-panel').hidden = unprivilegedLxc;
  $('#nfs-unprivileged-info').hidden = !unprivilegedLxc;
  $('#nfs-v3').checked = nfsProtocols.includes('3');
  $('#nfs-v4').checked = nfsProtocols.includes('4');
  $('#nfs-capability').textContent = state.nfs_v4_available
    ? 'Este entorno permite NFSv4 con Ganesha.'
    : 'NFSv4 no esta disponible con los permisos actuales. Usa el perfil avanzado; NFSv3 continua disponible.';
  renderAdmins(state.admins || []);
  const ignored = state.ignored_mounts || [];
  $('#ignored-mounts').hidden = !ignored.length;
  $('#ignored-mount-names').textContent = ignored.join(', ');
  for (const key of treeCache.keys()) {
    if (!state.mounted_folders.includes(key.split('/')[0])) treeCache.delete(key);
  }
  for (const id of openMounts) {
    if (!state.mounted_folders.includes(id)) openMounts.delete(id);
  }
  for (const share of state.settings.shares) {
    if (!share.path) continue;
    const parts = share.path.split('/');
    for (let i = 1; i < parts.length; i++) expandedPaths.add(treeKey(share.id, parts.slice(0, i).join('/')));
  }
  const smbUsers = $('#smb-users');
  const priorUsers = new Set([...smbUsers.querySelectorAll('input:checked')].map(input => input.value));
  smbUsers.replaceChildren();
  for (const user of state.users) {
    const label = document.createElement('label');
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.value = user;
    input.checked = priorUsers.has(user);
    input.disabled = inheritedSmb.users.has(user);
    label.append(input, document.createTextNode(user));
    smbUsers.append(label);
  }
  renderMounts();
  renderUsers(state);
  renderSummaries(state);
}

// Asks again in place: the first click arms the button for a few seconds,
// the second one goes ahead.
function confirmInPlace(button, run) {
  let armed = null;
  let label = '';
  button.addEventListener('click', event => {
    if (armed) {
      clearTimeout(armed);
      armed = null;
      button.innerHTML = label;
      button.classList.toggle('armed', false);
      run(event);
      return;
    }
    label = button.innerHTML;
    button.textContent = 'Pulsa otra vez para confirmar';
    button.classList.toggle('armed', true);
    armed = setTimeout(() => {
      armed = null;
      button.innerHTML = label;
      button.classList.toggle('armed', false);
    }, 5000);
  });
}

// The editor says what saving will do before this is reached.
async function unpublishShare(share, browseAfter = false) {
  try {
    const shares = current.settings.shares.filter(item => item !== share);
    const state = await api('/api/settings', 'PUT', { ...current.settings, shares });
    render(state);
    if (browseAfter) {
      openEditor(share.id, '', null);
      $('#share-all').checked = false;
      showRootMode();
      renderMounts();
    } else resetForm();
    message(browseAfter ? 'El montaje ya no se comparte entero. Ahora puedes publicar sus subcarpetas.' : 'Configuracion eliminada. La carpeta y sus archivos se conservan.');
  } catch (error) { editorMessage(error.message, true); }
}

$('#settings-form').addEventListener('submit', async event => {
  event.preventDefault();
  editorMessage('');
  if (!activeTarget) { editorMessage('Selecciona una ruta del listado.', true); return; }
  const { id, path } = activeTarget;
  const existing = current.settings.shares.find(share => share.id === id && share.path === path);
  if (!path && !$('#share-all').checked) {
    if (existing) { await unpublishShare(existing, true); return; }
    message('Elige una subcarpeta del arbol y pulsa Editar para publicarla. No se ha modificado ningun recurso.');
    return;
  }
  const taken = takenProtocols(id, path);
  if (taken.exclusive || ($('#nfs-enabled').checked && taken.nfs) || ($('#time-machine').checked && taken.any)) {
    editorMessage('Esta ruta se solapa con un recurso existente. Edita o retira el recurso padre o hijo antes de publicarla.', true);
    return;
  }
  syncAddedSmbAccess();
  const noProtocols = !$('#smb-enabled').checked && !$('#nfs-enabled').checked;
  if (noProtocols && !existing) {
    editorMessage(addingSmbAccess ? 'Marca el acceso que quieres anadir a esta carpeta.'
      : 'Activa el protocolo que quieres anadir a esta carpeta.', true);
    return;
  }
  if (noProtocols) $('#resource-enabled').checked = false;
  const enabled = $('#resource-enabled').checked;
  const smbUsers = [...$('#smb-users').querySelectorAll('input:checked')].map(input => input.value);
  if (enabled && $('#smb-enabled').checked && !$('#smb-guest').checked && !smbUsers.length) {
    editorMessage('Selecciona un usuario Samba o activa el acceso para invitados sin contrasena. Tambien puedes desactivar Samba si solo quieres NFS.', true, $('#smb-user-field'));
    return;
  }
  if (enabled && $('#nfs-enabled').checked && !$('#clients').value.trim()) {
    editorMessage('Para publicar por NFS, indica al menos una IP o red CIDR en Clientes NFS permitidos (por ejemplo, 192.168.0.0/24).', true, $('#clients'));
    return;
  }
  const base = {
    id, path,
    name: $('#name').value,
    enabled,
    smb_enabled: $('#smb-enabled').checked,
    smb_browseable: $('#smb-browseable').checked,
    smb_encryption: $('#smb-encryption').value,
    smb_clients: $('#smb-clients').value.split(/[\n,]+/).map(value => value.trim()).filter(Boolean),
    nfs_enabled: $('#nfs-enabled').checked,
    read_only: $('#read-only').checked,
    nfs_read_only: $('#nfs-read-only').checked,
    nfs_clients: $('#clients').value.split(/[\n,]+/).map(value => value.trim()).filter(Boolean),
    nfs_mapping: $('#nfs-enabled').checked ? $('#nfs-mapping').value : 'root-squash',
    nfs_uid: !$('#nfs-enabled').checked || $('#nfs-mapping').value === 'root-squash' || !$('#nfs-uid').value ? null : Number($('#nfs-uid').value),
    nfs_gid: !$('#nfs-enabled').checked || $('#nfs-mapping').value === 'root-squash' || !$('#nfs-gid').value ? null : Number($('#nfs-gid').value),
    nfs_insecure: $('#nfs-insecure').checked,
    smb_users: addingSmbAccess && !$('#smb-enabled').checked ? [] : smbUsers,
    smb_guest: $('#smb-enabled').checked && $('#smb-guest').checked,
    time_machine: $('#time-machine').checked,
    time_machine_max_size_gb: $('#time-machine').checked ? Number($('#tm-share-limit').value) || 0 : 0
  };
  if (noProtocols && existing) Object.assign(base, existing, { name: $('#name').value, enabled: false });
  const previous = current.settings.shares.find(item => item.name === editing);
  if (previous?.nfs_enabled && base.nfs_enabled && previous.id === id &&
      previous.path === path && previous.export_id) base.export_id = previous.export_id;
  const others = current.settings.shares.filter(item => item.name !== editing);
  try {
    const state = await api('/api/settings', 'PUT', { ...current.settings, shares: [...others, base] });
    resetForm();
    render(state);
    message(base.enabled ? 'Recurso guardado.' : 'Recurso pausado. Su configuracion se conserva.');
  } catch (error) { editorMessage(error.message, true); }
});
$('#smb-protocol-form').addEventListener('submit', async event => {
  event.preventDefault();
  try {
    render(await api('/api/settings', 'PUT', {
      ...current.settings,
      smb_min_protocol: $('#smb-min-protocol').value,
      smb_max_protocol: $('#smb-max-protocol').value
    }));
    message('Compatibilidad SMB actualizada. Las conexiones existentes pueden necesitar reconectarse.');
  } catch (error) { message(error.message, true); }
});
$('#nfs-protocol-form').addEventListener('submit', async event => {
  event.preventDefault();
  const protocols = [$('#nfs-v3').checked ? '3' : '', $('#nfs-v4').checked ? '4' : ''].filter(Boolean);
  if (!protocols.length) { message('Selecciona al menos un protocolo NFS.', true); return; }
  if (protocols.includes('4') && !current.nfs_v4_available) {
    message('NFSv4 necesita el perfil avanzado y CAP_DAC_READ_SEARCH. No se ha cambiado la configuracion.', true);
    return;
  }
  try {
    render(await api('/api/settings', 'PUT', { ...current.settings, nfs_protocols: protocols }));
    const protocolLabels = protocols.map(item => formatMessage('NFSv{version}', { version: item })).join(' + ');
    message(formatMessage('Protocolos NFS actualizados: {protocols}.', { protocols: protocolLabels }));
  } catch (error) { message(error.message, true); }
});
$('#discovery-form').addEventListener('submit', async event => {
  event.preventDefault();
  try {
    render(await api('/api/settings', 'PUT', {
      ...current.settings,
      server_name: $('#server-name').value,
      mdns_enabled: $('#mdns-enabled').checked
    }));
    message('Nombre y anuncio de red actualizados. Finder puede tardar unos segundos en refrescar.');
  } catch (error) { message(error.message, true); }
});
$('#permissions-form').addEventListener('submit', async event => {
  event.preventDefault();
  try {
    render(await api('/api/settings', 'PUT', {
      ...current.settings,
      collaborative_mode: $('#collaborative-mode').checked,
      shared_uid: Number($('#shared-uid').value),
      shared_gid: Number($('#shared-gid').value)
    }));
    message('Identidad colaborativa actualizada. Revisa la compatibilidad de cada montaje en Recursos.');
  } catch (error) { message(error.message, true); }
});
$('#time-machine-form').addEventListener('submit', async event => {
  event.preventDefault();
  const limit = $('#tm-limit-enabled').checked ? Number($('#tm-limit-gb').value) : 0;
  try {
    render(await api('/api/settings', 'PUT', { ...current.settings, time_machine_max_size_gb: limit }));
    message(limit ? formatMessage('Limite Time Machine actualizado a {limit} GB.', { limit }) : 'Los destinos Time Machine quedan sin limite anunciado.');
  } catch (error) { message(error.message, true); }
});
$('#tm-limit-enabled').addEventListener('change', event => {
  $('#tm-limit-field').hidden = !event.target.checked;
  $('#tm-limit-gb').required = event.target.checked;
});
$('#share-all').addEventListener('change', event => {
  if (!activeTarget || activeTarget.path) return;
  const id = activeTarget.id;
  const taken = takenProtocols(id, '');
  if (event.target.checked && taken.exclusive) {
    event.target.checked = false;
    message('Retira primero los recursos de las subcarpetas antes de compartir todo el montaje.', true);
    return;
  }
  showInheritance();
  showProtocolOptions();
  showRootMode();
  renderMounts();
});
$('#nfs-mapping').addEventListener('change', showNfsMapping);
$('#smb-guest').addEventListener('change', () => {
  syncAddedSmbAccess();
  showSmbAccess();
});
$('#smb-users').addEventListener('change', () => {
  syncAddedSmbAccess();
  showSmbAccess();
});
$('#read-only').addEventListener('change', syncAddedSmbAccess);
// What was asked for is being fixed: the note next to the field goes away.
$('#settings-form').addEventListener('input', () => { if (invalidField) editorMessage(''); });
$('#smb-enabled').addEventListener('change', event => {
  if (!event.target.checked) {
    $('#smb-guest').checked = false;
    $('#time-machine').checked = false;
  }
  syncResourceState();
  showProtocolOptions();
});
$('#nfs-enabled').addEventListener('change', () => {
  syncResourceState();
  showProtocolOptions();
});
$('#time-machine').addEventListener('change', () => {
  if ($('#time-machine').checked && $('#nfs-enabled').checked) {
    $('#time-machine').checked = false;
    editorMessage('No se puede activar Time Machine mientras NFS esta activo. Usa una carpeta exclusiva de Samba, con escritura, usuario autenticado y sin NFS.', true);
  }
  syncResourceState();
  showProtocolOptions();
});
$('#resource-enabled').addEventListener('change', () => {
  if ($('#resource-enabled').checked && !$('#smb-enabled').checked && !$('#nfs-enabled').checked) {
    $('#resource-enabled').checked = false;
    editorMessage('Activa SMB o NFS antes de reactivar el recurso.', true);
  } else editorMessage('');
  showResourceMode();
  showNfsMapping();
});
confirmInPlace($('#delete-resource'), async event => {
  const share = current.settings.shares.find(item => item.name === editing);
  if (!share) return;
  startButtonFeedback(event.currentTarget);
  try {
    const shares = current.settings.shares.filter(item => item.name !== share.name);
    const state = await api('/api/settings', 'PUT', { ...current.settings, shares });
    resetForm();
    render(state);
    // Inside a shared folder only what this one added goes away.
    const inherited = inheritedProtocols(share.id, share.path);
    message(inherited.smb || inherited.nfs
      ? 'Configuracion eliminada. La carpeta vuelve a compartirse solo con los ajustes del recurso superior.'
      : 'Configuracion eliminada. La carpeta y sus archivos se conservan.');
  } catch (error) { message(error.message, true); }
});
showProtocolOptions();
confirmInPlace($('#migrate-nfs'), async () => {
  try {
    render(await api('/api/settings', 'PUT', { ...current.settings, nfs_backend: 'unfs3-v3' }));
    message('NFSv3 activado. Comprueba cada recurso desde un cliente antes de confiarle datos.');
  } catch (error) { message(error.message, true); }
});
for (const service of ['smb', 'nfs']) {
  $(`#${service}-service`).addEventListener('change', async event => {
    const enabled = event.target.checked;
    try {
      render(await api('/api/settings', 'PUT', {
        ...current.settings, [`${service}_service_enabled`]: enabled
      }));
      message(enabled ? formatMessage('{service} activado sin eliminar sus recursos.', { service: service.toUpperCase() }) : formatMessage('{service} desactivado sin eliminar sus recursos.', { service: service.toUpperCase() }));
    } catch (error) { event.target.checked = !enabled; message(error.message, true); }
  });
}
function view(name) {
  $('#resources-view').hidden = name !== 'resources';
  $('#settings-view').hidden = name !== 'settings';
  for (const item of ['resources', 'settings']) {
    $(`#tab-${item}`).classList.toggle('active', item === name);
    if (item === name) $(`#tab-${item}`).setAttribute('aria-current', 'page');
    else $(`#tab-${item}`).removeAttribute('aria-current');
  }
}
// Settings cards are folded until clicked, like the resources.
for (const panel of document.querySelectorAll?.('.fold') || []) {
  const body = panel.querySelector('.fold-body');
  const toggle = panel.querySelector('.tree-expand');
  panel.querySelector('.fold-head').addEventListener('click', () => {
    const open = body.hidden;
    body.hidden = !open;
    panel.classList.toggle('open', open);
    toggle.setAttribute('aria-expanded', String(open));
  });
}
$('#tab-resources').addEventListener('click', () => view('resources'));
$('#tab-settings').addEventListener('click', () => view('settings'));

$('#user-form').addEventListener('submit', async event => {
  event.preventDefault();
  try {
    const name = $('#username').value.trim();
    render(await api('/api/users', 'POST', { name, password: $('#password').value }));
    $('#username').value = '';
    $('#password').value = '';
    message(formatMessage('Usuario SMB {name} creado. Asignale un recurso desde Recursos > Editar.', { name }));
  } catch (error) { message(error.message, true); }
});

$('#admin-form').addEventListener('submit', async event => {
  event.preventDefault();
  try {
    const name = $('#admin-username').value.trim().toLowerCase();
    render(await api('/api/admins', 'POST', { name, password: $('#admin-password').value }));
    $('#admin-username').value = '';
    $('#admin-password').value = '';
    message(formatMessage('Administrador {name} creado.', { name }));
  } catch (error) { message(error.message, true); }
});

// The sign-in form is sent by the browser, not from here: that is what lets
// it offer to remember the password. The answer comes back in the address.
const loginNotice = search => ({
  failed: 'Usuario o contrasena incorrectos',
  wait: 'Demasiados intentos. Espera unos minutos antes de volver a probar.'
})[new URLSearchParams(search).get('login')] || '';
const pendingLoginNotice = loginNotice(location.search);
if (location.search) history.replaceState(null, '', location.pathname);
$('#logout').addEventListener('click', async () => {
  try { await api('/api/logout', 'POST', {}); } catch (_) {}
  showLogin();
});

api('/api/state').then(render).catch(error => {
  if (signedIn) message(error.message, true);
  else if (pendingLoginNotice) showLogin(pendingLoginNotice);
});
setInterval(() => { if (signedIn) api('/api/state').then(serviceStatus).catch(() => {}); }, 10000);
