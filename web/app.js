const $ = selector => document.querySelector(selector);
const editorElement = $('#editor');
let current;
let editing = null;
let activeTarget = null;
let expandedPaths = new Set();
let treeCache = new Map();
let treePending = new Map();
let pendingButton = null;
let messageTimer = null;
let editorMessageTimer = null;
let recoveryTimer = null;

const actionIcons = {
  edit: '<svg class="action-icon" aria-hidden="true" viewBox="0 0 24 24"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L8 18l-4 1 1-4Z"/></svg>',
  save: '<svg class="action-icon" aria-hidden="true" viewBox="0 0 24 24"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z"/><path d="M17 21v-8H7v8M7 3v5h8"/></svg>',
  delete: '<svg class="action-icon" aria-hidden="true" viewBox="0 0 24 24"><path d="M3 6h18M8 6V4h8v2M19 6l-1 15H6L5 6M10 11v6M14 11v6"/></svg>'
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
const routePath = (id, path) => `/shares/${id}${path ? '/' + path : ''}`;

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
  button.dataset.originalLabel = button.textContent;
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
    button.textContent = button.dataset.originalLabel || 'Guardar';
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
    if (!response.ok) throw new Error(result.error || 'Error del servidor');
    if (method !== 'GET') finishButtonFeedback(true);
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

function editorMessage(value, error = false) {
  const element = $('#editor-message');
  clearTimeout(editorMessageTimer);
  editorMessageTimer = null;
  element.textContent = value;
  element.hidden = !value;
  element.classList.toggle('error', error);
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
    label.title = [statusText, switchedOn ? item.error || (nfsUnverified ? 'Comprueba el montaje y los permisos desde otro cliente.' : '') : ''].filter(Boolean).join(': ');
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
  $('#nfs-endpoint').textContent = protocols.length === 2 ? 'NFSv3 + NFSv4' : `NFSv${protocols[0]}`;
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
  $('#smb-guest').disabled = !$('#smb-enabled').checked;
  $('#smb-user-field').hidden = !$('#smb-enabled').checked;
  $('#smb-guest-hint').hidden = !guest;
  $('#read-only').disabled = guest && !selectedUsers;
  if (guest) {
    $('#time-machine').checked = false;
    if (!selectedUsers) $('#read-only').checked = true;
  }
}

function showProtocolOptions() {
  $('#smb-options').hidden = !$('#smb-enabled').checked;
  $('#nfs-options').hidden = !$('#nfs-enabled').checked;
  showResourceMode();
  showNfsMapping();
  showSmbAccess();
}

function showResourceMode() {
  const paused = !$('#resource-enabled').checked;
  $('#resource-state-hint').hidden = !paused;
  $('.protocol-sections').classList.toggle('resource-paused', paused);
}

function resetForm() {
  editing = null;
  activeTarget = null;
  expandedPaths.clear();
  $('#settings-form').reset();
  $('#delete-resource').hidden = true;
  editorMessage('');
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
  $('#resource-enabled').checked = true;
  editorMessage('');
  activeTarget = { id, path };
  editing = share?.name || null;
  $('#delete-resource').hidden = !share;
  if (path) {
    const parts = path.split('/');
    for (let i = 1; i < parts.length; i++) expandedPaths.add(treeKey(id, parts.slice(0, i).join('/')));
  }
  if (!path) {
    const hasChildShares = current.settings.shares.some(item => item.id === id && !!item.path);
    $('#share-all').checked = !!share || !hasChildShares;
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
    $('#smb-guest').checked = share.smb_guest === true;
  }
  for (const input of $('#smb-users').querySelectorAll('input')) {
    input.checked = !!share && (share.smb_users === null || share.smb_users.includes(input.value));
  }
  showProtocolOptions();
  showRootMode();
  editorElement.hidden = false;
  renderMounts();
  editorElement.previousElementSibling?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function renderRoute(id, path, parent, root = false, browsingRoot = false) {
  const share = current.settings.shares.find(item => item.id === id && item.path === path);
  const overlapping = current.settings.shares.some(item => item.id === id && item !== share && overlaps(item.path, path));
  const row = document.createElement('div');
  const selected = activeTarget?.id === id && activeTarget.path === path;
  row.className = `route-row${root ? ' mount-root' : ''}${selected ? ' selected' : ''}`;
  const label = document.createElement('div');
  label.className = 'route-label';
  const title = document.createElement('strong');
  title.textContent = root ? routePath(id, '') : path.split('/').pop();
  const detail = document.createElement('small');
  const childCount = root ? current.settings.shares.filter(item => item.id === id && !!item.path).length : 0;
  const knownTree = !root && treeCache.get(treeKey(id, path));
  const leaf = knownTree && !knownTree.children.length && !knownTree.truncated;
  const protocols = share ? [share.smb_enabled ? 'SMB' : '', share.nfs_enabled ? 'NFS' : ''].filter(Boolean).join(' + ') : '';
  detail.textContent = share ? `${share.name} · ${share.enabled === false ? `Pausado${protocols ? ` (${protocols})` : ''}` : protocols}` : childCount ? `${childCount} subcarpeta${childCount === 1 ? '' : 's'} configurada${childCount === 1 ? '' : 's'}` : overlapping ? 'Cubierta por otro recurso' : 'Directorio disponible · No compartido en red';
  if (leaf) detail.textContent += ' · Sin subcarpetas';
  label.append(title, detail);
  if (root && share && current.mount_permissions?.[id]) {
    const access = current.mount_permissions[id];
    const permissions = document.createElement('small');
    permissions.className = `route-permissions ${access.writable ? 'writable' : 'not-writable'}`;
    permissions.textContent = `UID:GID ${access.uid}:${access.gid} · ${access.mode} · ${access.writable ? 'Compatible con escritura' : `Sin escritura para ${access.shared_uid}:${access.shared_gid}`}`;
    label.append(permissions);
  }
  const parentPath = path.split('/').slice(0, -1).join('/');
  const parentTree = !root && treeCache.get(treeKey(id, parentPath));
  const mayExpand = !root && !leaf && parentTree?.expandable?.[path.split('/').pop()] !== false;
  if (mayExpand) {
    const expander = document.createElement('button');
    expander.type = 'button';
    expander.className = 'tree-expand';
    expander.textContent = expandedPaths.has(treeKey(id, path)) ? '−' : '+';
    expander.setAttribute('aria-label', `${expandedPaths.has(treeKey(id, path)) ? 'Contraer' : 'Abrir'} ${path}`);
    expander.addEventListener('click', async () => {
      const key = treeKey(id, path);
      if (expandedPaths.has(key)) {
        expandedPaths.delete(key);
        if (activeTarget?.id === id && activeTarget.path.startsWith(path + '/')) {
          resetForm();
          return;
        }
      } else {
        try {
          const tree = await loadTree(id, path);
          if (tree.children.length || tree.truncated) expandedPaths.add(key);
        }
        catch (error) { message(error.message, true); return; }
      }
      renderMounts();
    });
    row.append(expander);
  }
  row.append(label);
  if (share) {
    const badges = document.createElement('div');
    badges.className = 'route-badges';
    if (share.enabled === false) {
      const badge = document.createElement('span');
      badge.textContent = 'Pausado';
      badges.append(badge);
    }
    if (share.enabled !== false && share.smb_enabled) {
      const badge = document.createElement('span');
      badge.className = 'protocol-smb';
      badge.textContent = 'SMB';
      badges.append(badge);
    }
    if (share.enabled !== false && share.nfs_enabled) {
      const badge = document.createElement('span');
      badge.className = 'protocol-nfs';
      badge.textContent = 'NFS';
      badges.append(badge);
    }
    row.append(badges);
  }
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
    edit.disabled = !root && overlapping;
    edit.title = !root && overlapping ? 'No se publican rutas padre e hija a la vez: se solaparian sus permisos.' : 'Configurar esta ruta';
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
    }).catch(error => { loading.textContent = `No se pudo leer: ${error.message}`; });
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

function renderPublishedRoutes(id, parent) {
  const shares = current.settings.shares.filter(share => share.id === id && share.path);
  if (!shares.length) return;
  const list = document.createElement('div');
  list.className = 'published-routes';
  for (const share of shares) {
    const row = document.createElement('div');
    row.className = 'published-route';
    const details = document.createElement('div');
    details.className = 'published-route-details';
    const path = document.createElement('strong');
    path.textContent = routePath(id, share.path);
    const name = document.createElement('small');
    name.textContent = `Nombre en red: ${share.name}`;
    details.append(path, name);
    const access = current.share_permissions?.[treeKey(id, share.path)];
    if (access) {
      const permissions = document.createElement('small');
      permissions.className = `route-permissions ${access.writable ? 'writable' : 'not-writable'}`;
      permissions.textContent = `UID:GID ${access.uid}:${access.gid} · ${access.mode} · ${access.writable ? 'Compatible con escritura' : `Sin escritura para ${access.shared_uid}:${access.shared_gid}`}`;
      details.append(permissions);
    }
    const protocols = document.createElement('div');
    protocols.className = 'route-badges published-route-protocols';
    if (share.enabled === false) {
      const paused = document.createElement('span');
      paused.textContent = 'Pausado';
      protocols.append(paused);
    } else {
      if (share.smb_enabled) {
        const smb = document.createElement('span');
        smb.className = 'protocol-smb';
        smb.textContent = 'SMB';
        protocols.append(smb);
      }
      if (share.nfs_enabled) {
        const nfs = document.createElement('span');
        nfs.className = 'protocol-nfs';
        nfs.textContent = 'NFS';
        protocols.append(nfs);
      }
    }
    const edit = document.createElement('button');
    edit.type = 'button';
    edit.className = 'route-action edit';
    setActionContent(edit, 'edit', 'Editar');
    edit.setAttribute('aria-label', `Editar ${share.path}`);
    edit.addEventListener('click', () => openEditor(id, share.path, share));
    row.append(details, protocols, edit);
    list.append(row);
  }
  parent.append(list);
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
    mount.className = `mount-card${activeTarget?.id === id && !activeTarget.path ? ' editing' : ''}`;
    const editingRoot = activeTarget?.id === id && !activeTarget.path;
    renderRoute(id, '', mount, true, editingRoot && browsingRoot);
    if ((editingRoot && browsingRoot) || (activeTarget?.id === id && !!activeTarget.path)) {
      const tree = document.createElement('div');
      tree.className = 'mount-tree';
      renderFolderTree(id, '', tree);
      mount.append(tree);
    } else if (!editingRoot) renderPublishedRoutes(id, mount);
    list.append(mount);
  }
  if (!editorElement.isConnected) $('#resources-view').append(editorElement);
}

function renderAdmins(names) {
  const list = $('#admins');
  list.replaceChildren();
  $('#admins-empty').hidden = names.length > 0;
  for (const name of names) {
    const row = document.createElement('li');
    const title = document.createElement('strong');
    title.textContent = name;
    const rotate = document.createElement('button');
    rotate.type = 'button';
    rotate.textContent = 'Cambiar contrasena';
    rotate.addEventListener('click', async () => {
      const password = prompt(`Nueva contrasena para ${name} (6 caracteres minimo):`);
      if (password === null) return;
      const confirmation = prompt(`Confirma la nueva contrasena para ${name}:`);
      if (confirmation === null) return;
      if (password !== confirmation) {
        message('Las contrasenas no coinciden. No se ha realizado ningun cambio.', true);
        return;
      }
      try {
        render(await api(`/api/admins/${name}/password`, 'POST', { password }));
        message('Contrasena actualizada. Si era tu cuenta actual, vuelve a iniciar sesion con la nueva clave.');
      } catch (error) { message(error.message, true); }
    });
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'danger';
    remove.textContent = 'Eliminar';
    remove.addEventListener('click', async () => {
      if (!confirm(`Eliminar el administrador ${name}?`)) return;
      try { render(await api(`/api/admins/${name}`, 'DELETE')); message('Administrador eliminado.'); }
      catch (error) { message(error.message, true); }
    });
    row.append(title, rotate, remove);
    list.append(row);
  }
}

function render(state) {
  current = state;
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
  for (const key of treeCache.keys()) {
    if (!state.mounted_folders.includes(key.split('/')[0])) treeCache.delete(key);
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
    label.append(input, document.createTextNode(user));
    smbUsers.append(label);
  }
  renderMounts();
  const users = $('#users');
  users.replaceChildren();
  $('#users-empty').hidden = state.users.length > 0;
  for (const name of state.users) {
    const row = document.createElement('li');
    const details = document.createElement('div');
    details.className = 'user-details';
    const title = document.createElement('strong');
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
      access.textContent = `${share.name} · ${share.read_only ? 'lectura' : 'lectura/escritura'}`;
      access.title = `Editar permisos de ${share.name}`;
      access.addEventListener('click', () => {
        view('resources');
        openEditor(share.id, share.path, share);
      });
      details.append(access);
    }
    const rotate = document.createElement('button');
    rotate.textContent = 'Nueva clave';
    rotate.type = 'button';
    rotate.addEventListener('click', async () => {
      const password = prompt(`Nueva contrasena para ${name} (6 caracteres minimo):`);
      if (password === null) return;
      try { render(await api(`/api/users/${name}/password`, 'POST', { password })); message('Contrasena actualizada.'); }
      catch (error) { message(error.message, true); }
    });
    const remove = document.createElement('button');
    remove.textContent = 'Eliminar';
    remove.type = 'button';
    remove.className = 'danger';
    remove.addEventListener('click', async () => {
      if (!confirm(`Eliminar acceso SMB de ${name}? Sus archivos no se borraran.`)) return;
      try { render(await api(`/api/users/${name}`, 'DELETE')); message('Usuario eliminado.'); }
      catch (error) { message(error.message, true); }
    });
    row.append(details, rotate, remove);
    users.append(row);
  }
}

async function unpublishShare(share, browseAfter = false) {
  if (!confirm(`Dejar de publicar ${share.name}? Los archivos del directorio no se borraran.`)) return;
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
  if (current.settings.shares.some(share => share.id === id &&
      share.name !== editing && overlaps(share.path || '', path))) {
    editorMessage('Esta ruta se solapa con un recurso existente. Edita o retira el recurso padre o hijo antes de publicarla.', true);
    return;
  }
  const noProtocols = !$('#smb-enabled').checked && !$('#nfs-enabled').checked;
  if (noProtocols) $('#resource-enabled').checked = false;
  const enabled = $('#resource-enabled').checked;
  const smbUsers = [...$('#smb-users').querySelectorAll('input:checked')].map(input => input.value);
  if (enabled && $('#smb-enabled').checked && !$('#smb-guest').checked && !smbUsers.length) {
    editorMessage('Selecciona un usuario Samba o activa el acceso para invitados sin contrasena. Tambien puedes desactivar Samba si solo quieres NFS.', true);
    return;
  }
  if (enabled && $('#nfs-enabled').checked && !$('#clients').value.trim()) {
    editorMessage('Para publicar por NFS, indica al menos una IP o red CIDR en Clientes NFS permitidos (por ejemplo, 192.168.0.0/24).', true);
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
    smb_users: smbUsers,
    smb_guest: $('#smb-guest').checked,
    time_machine: $('#time-machine').checked
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
    message(`Protocolos NFS actualizados: ${protocols.map(item => `NFSv${item}`).join(' + ')}.`);
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
    message(limit ? `Limite Time Machine actualizado a ${limit} GB.` : 'Los destinos Time Machine quedan sin limite anunciado.');
  } catch (error) { message(error.message, true); }
});
$('#tm-limit-enabled').addEventListener('change', event => {
  $('#tm-limit-field').hidden = !event.target.checked;
  $('#tm-limit-gb').required = event.target.checked;
});
$('#share-all').addEventListener('change', event => {
  if (!activeTarget || activeTarget.path) return;
  const id = activeTarget.id;
  if (event.target.checked && current.settings.shares.some(share => share.id === id && !!share.path)) {
    event.target.checked = false;
    message('Retira primero los recursos de las subcarpetas antes de compartir todo el montaje.', true);
    return;
  }
  showRootMode();
  renderMounts();
});
$('#nfs-mapping').addEventListener('change', showNfsMapping);
$('#smb-guest').addEventListener('change', event => {
  showSmbAccess();
});
$('#smb-users').addEventListener('change', showSmbAccess);
$('#smb-enabled').addEventListener('change', event => {
  if (!event.target.checked) {
    $('#smb-guest').checked = false;
    $('#time-machine').checked = false;
  }
  if (!$('#smb-enabled').checked && !$('#nfs-enabled').checked) $('#resource-enabled').checked = false;
  showProtocolOptions();
});
$('#nfs-enabled').addEventListener('change', () => {
  if ($('#nfs-enabled').checked && $('#time-machine').checked) {
    $('#nfs-enabled').checked = false;
    editorMessage('No se puede activar NFS mientras Time Machine esta activo. Time Machine necesita un recurso exclusivo de Samba, con escritura, usuario autenticado y sin NFS.', true);
    showProtocolOptions();
    return;
  }
  if (!$('#smb-enabled').checked && !$('#nfs-enabled').checked) $('#resource-enabled').checked = false;
  showProtocolOptions();
});
$('#time-machine').addEventListener('change', () => {
  if ($('#time-machine').checked && $('#nfs-enabled').checked) {
    $('#time-machine').checked = false;
    editorMessage('No se puede activar Time Machine mientras NFS esta activo. Usa una carpeta exclusiva de Samba, con escritura, usuario autenticado y sin NFS.', true);
  }
});
$('#resource-enabled').addEventListener('change', () => {
  if ($('#resource-enabled').checked && !$('#smb-enabled').checked && !$('#nfs-enabled').checked) {
    $('#resource-enabled').checked = false;
    editorMessage('Activa SMB o NFS antes de reactivar el recurso.', true);
  } else editorMessage('');
  showResourceMode();
  showNfsMapping();
});
$('#delete-resource').addEventListener('click', async event => {
  const share = current.settings.shares.find(item => item.name === editing);
  if (!share || !confirm(`Eliminar la configuracion de ${share.name}? La carpeta y todos sus archivos se conservaran.`)) return;
  startButtonFeedback(event.currentTarget);
  try {
    const shares = current.settings.shares.filter(item => item.name !== share.name);
    const state = await api('/api/settings', 'PUT', { ...current.settings, shares });
    resetForm();
    render(state);
    message('Configuracion eliminada. La carpeta y sus archivos se conservan.');
  } catch (error) { message(error.message, true); }
});
showProtocolOptions();
$('#migrate-nfs').addEventListener('click', async () => {
  if (!confirm('Activar NFSv3? Cambia la ruta de montaje a /shares/..., requiere vers=3,nolock,port=2049,mountport=2049 y no ofrece NLM ni Kerberos.')) return;
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
      message(`${service.toUpperCase()} ${enabled ? 'activado' : 'desactivado'} sin eliminar sus recursos.`);
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
$('#tab-resources').addEventListener('click', () => view('resources'));
$('#tab-settings').addEventListener('click', () => view('settings'));

$('#user-form').addEventListener('submit', async event => {
  event.preventDefault();
  try {
    const name = $('#username').value.trim();
    render(await api('/api/users', 'POST', { name, password: $('#password').value }));
    $('#username').value = '';
    $('#password').value = '';
    message(`Usuario SMB ${name} creado. Asignale un recurso desde Recursos > Editar.`);
  } catch (error) { message(error.message, true); }
});

$('#admin-form').addEventListener('submit', async event => {
  event.preventDefault();
  try {
    const name = $('#admin-username').value.trim().toLowerCase();
    render(await api('/api/admins', 'POST', { name, password: $('#admin-password').value }));
    $('#admin-username').value = '';
    $('#admin-password').value = '';
    message(`Administrador ${name} creado.`);
  } catch (error) { message(error.message, true); }
});

api('/api/state').then(render).catch(error => message(error.message, true));
setInterval(() => api('/api/state').then(serviceStatus).catch(() => {}), 10000);
