const fallbackState = {
  theme: 'ember',
  last_page: 'dashboard',
  counts: {
    accounts: 0,
    configured_slots: 0,
  },
  paths: {
    config: 'Loading',
    sessions: 'Loading',
    logs: 'Loading',
  },
  build: {
    desktop_stack: 'pywebview + HTML/CSS/JS',
    auth_reference: 'SteamCommunityKit',
    repo_flow: 'Local first, GitHub backup',
    default_branch: 'main',
  },
  window: {
    maximized: false,
    width: 1460,
    height: 920,
  },
  accounts: [],
  runtime: {
    slot_ceiling: 32,
    conflict_policy: 'Pause before force-kick',
    reconnect_posture: 'Backoff and resume',
    transport_name: 'valvepython-steam',
    preview_mode: false,
    counts: {
      tracked_accounts: 0,
      ready_accounts: 0,
      boosting_accounts: 0,
      error_accounts: 0,
      active_slots: 0,
    },
    statuses: [],
    recent_events: [
      '[runtime] runtime controller initialized',
      '[runtime] Steam client transport available',
      '[next] harden reconnect and live slot updates',
    ],
  },
};

const shellState = {
  currentPage: 'dashboard',
  maximized: false,
  bootstrap: fallbackState,
  authMode: 'credentials',
  selectedAccountProfileId: null,
  pendingQrLogin: null,
  qrPollTimer: null,
};

document.addEventListener('DOMContentLoaded', async () => {
  bindNavigation();
  bindWindowControls();
  bindDragRegion();
  bindAuthModes();
  bindAccountActions();
  bindRuntimeActions();
  await waitForBridge();
  await hydrate();
  window.setTimeout(() => document.body.classList.add('is-ready'), 10);
});

async function waitForBridge() {
  const startedAt = Date.now();
  while (Date.now() - startedAt < 1500) {
    if (window.pywebview && window.pywebview.api) {
      return;
    }
    await new Promise((resolve) => window.setTimeout(resolve, 50));
  }
}

async function hydrate() {
  const bootstrap = await callApi('get_bootstrap_state');
  shellState.bootstrap = bootstrap || fallbackState;
  shellState.currentPage = shellState.bootstrap.last_page || 'dashboard';
  shellState.maximized = Boolean(shellState.bootstrap.window?.maximized);
  const profileIds = (shellState.bootstrap.accounts || []).map((account) => account.profile_id);
  if (!profileIds.length) {
    shellState.selectedAccountProfileId = null;
  } else if (!profileIds.includes(shellState.selectedAccountProfileId)) {
    shellState.selectedAccountProfileId = profileIds[0];
  }

  updateWindowButtons();
  fillOverview();
  fillAccounts();
  fillRuntime();
  fillSettings();
  fillAccountEditor();
  updateTopbarPill();
  selectPage(shellState.currentPage, false);
  selectAuthMode(shellState.authMode);
}

function bindNavigation() {
  document.querySelectorAll('.nav-button').forEach((button) => {
    button.addEventListener('click', async () => {
      await selectPage(button.dataset.page, true);
    });
  });
}

function bindWindowControls() {
  document.querySelectorAll('.window-button').forEach((button) => {
    button.addEventListener('click', async () => {
      const action = button.dataset.action;
      if (action === 'minimize') {
        await callApi('minimize_window');
        return;
      }
      if (action === 'maximize') {
        const result = await callApi('toggle_maximize_window');
        shellState.maximized = Boolean(result?.maximized);
        updateWindowButtons();
        return;
      }
      if (action === 'close') {
        await callApi('close_window');
      }
    });
  });
}

function bindDragRegion() {
  const dragRegion = document.getElementById('drag-region');
  if (!dragRegion) {
    return;
  }
  dragRegion.addEventListener('dblclick', async () => {
    const result = await callApi('toggle_maximize_window');
    shellState.maximized = Boolean(result?.maximized);
    updateWindowButtons();
  });
}

function bindAuthModes() {
  document.querySelectorAll('.mode-pill').forEach((button) => {
    button.addEventListener('click', () => {
      selectAuthMode(button.dataset.authMode);
    });
  });

  const credentialsSubmitButton = document.getElementById('credentials-submit-button');
  if (credentialsSubmitButton) {
    credentialsSubmitButton.addEventListener('click', loginWithCredentials);
  }

  const refreshSubmitButton = document.getElementById('refresh-submit-button');
  if (refreshSubmitButton) {
    refreshSubmitButton.addEventListener('click', loginWithRefreshToken);
  }

  const qrStartButton = document.getElementById('qr-start-button');
  if (qrStartButton) {
    qrStartButton.addEventListener('click', beginQrLogin);
  }

  const qrCancelButton = document.getElementById('qr-cancel-button');
  if (qrCancelButton) {
    qrCancelButton.addEventListener('click', cancelQrLogin);
  }
}

function bindAccountActions() {
  const list = document.getElementById('account-list');
  if (!list) {
    return;
  }
  list.addEventListener('click', async (event) => {
    const target = event.target.closest('[data-account-action]');
    if (!target) {
      const selectedCard = event.target.closest('[data-account-select]');
      if (selectedCard?.dataset.profileId) {
        shellState.selectedAccountProfileId = selectedCard.dataset.profileId;
        fillAccounts();
        fillAccountEditor();
      }
      return;
    }
    const action = target.dataset.accountAction;
    const profileId = target.dataset.profileId;
    if (action === 'remove' && profileId) {
      target.disabled = true;
      setAuthStatus('info', 'Removing account and deleting its saved session bundle.');
      const result = await callApi('remove_account', profileId);
      await handleMutationResult(result, {
        successMessage: result?.message || 'Account removed.',
      });
    }
  });

  const saveButton = document.getElementById('account-save-button');
  if (saveButton) {
    saveButton.addEventListener('click', saveAccountProfile);
  }
}

function bindRuntimeActions() {
  const list = document.getElementById('runtime-account-list');
  if (list) {
    list.addEventListener('click', async (event) => {
      const target = event.target.closest('[data-runtime-action]');
      if (!target?.dataset.profileId) {
        return;
      }
      const action = target.dataset.runtimeAction;
      target.disabled = true;
      try {
        if (action === 'start') {
          await startRuntimeLane(target.dataset.profileId);
        } else if (action === 'stop') {
          await stopRuntimeLane(target.dataset.profileId);
        }
      } finally {
        target.disabled = false;
      }
    });
  }

  const refreshButton = document.getElementById('runtime-refresh-button');
  if (refreshButton) {
    refreshButton.addEventListener('click', refreshRuntimeReadiness);
  }

  const startAllButton = document.getElementById('runtime-start-all-button');
  if (startAllButton) {
    startAllButton.addEventListener('click', startAllRuntimeLanes);
  }

  const stopAllButton = document.getElementById('runtime-stop-all-button');
  if (stopAllButton) {
    stopAllButton.addEventListener('click', stopAllRuntimeLanes);
  }
}

async function selectPage(pageKey, persist) {
  shellState.currentPage = pageKey;

  document.querySelectorAll('.page').forEach((page) => {
    page.classList.toggle('is-active', page.dataset.page === pageKey);
  });

  document.querySelectorAll('.nav-button').forEach((button) => {
    button.classList.toggle('is-active', button.dataset.page === pageKey);
  });

  if (persist) {
    await callApi('set_last_page', pageKey);
    if (shellState.bootstrap) {
      shellState.bootstrap.last_page = pageKey;
      fillSettings();
    }
  }
}

function fillOverview() {
  const state = shellState.bootstrap;
  setText('build-desktop-stack', state.build.desktop_stack);
  setText('build-auth-reference', state.build.auth_reference);
  setText('build-repo-flow', state.build.repo_flow);
  setText('build-default-branch', state.build.default_branch);
  setText('build-account-count', String(state.counts.accounts));
  setText('configured-slot-count', String(state.counts.configured_slots));
  setText('path-config', state.paths.config);
  setText('path-sessions', state.paths.sessions);
  setText('path-logs', state.paths.logs);
}

function fillAccounts() {
  const state = shellState.bootstrap;
  setText('accounts-count-pill', `${state.counts.accounts} configured`);

  const list = document.getElementById('account-list');
  list.innerHTML = '';

  if (!state.accounts.length) {
    const empty = document.createElement('div');
    empty.className = 'account-item';
    empty.innerHTML = `
      <div class="account-empty">
        No accounts are configured yet. Use the authentication studio on the right to create
        the first saved session profile.
      </div>
    `;
    list.appendChild(empty);
    return;
  }

  state.accounts.forEach((account) => {
    const item = document.createElement('div');
    item.className = `account-item${shellState.selectedAccountProfileId === account.profile_id ? ' account-item--selected' : ''}`;
    item.dataset.accountSelect = 'true';
    item.dataset.profileId = account.profile_id;
    item.innerHTML = `
      <div class="account-item__header">
        <div>
          <div class="account-item__title">${escapeHtml(account.display_name)}</div>
          <div class="account-item__subtitle">${escapeHtml(account.account_name || account.steam_id || 'Unresolved account identity')}</div>
        </div>
        <div class="account-item__actions">
          <span class="pill pill--subtle">${escapeHtml(account.login_mode)}</span>
          <button class="ghost-button" data-account-action="remove" data-profile-id="${escapeHtml(account.profile_id)}">Remove</button>
        </div>
      </div>
      <div class="account-item__meta">
        <div><span>Persona</span><strong>${escapeHtml(account.persona_state)}</strong></div>
        <div><span>Games</span><strong>${account.game_count}</strong></div>
        <div><span>Session</span><strong>${account.has_session_bundle ? 'Saved' : 'Missing'}</strong></div>
      </div>
      <div class="account-item__path">${escapeHtml(account.session_bundle_path || 'No session bundle file')}</div>
    `;
    list.appendChild(item);
  });
}

function fillAccountEditor() {
  const editorShell = document.getElementById('account-editor-shell');
  const editorEmpty = document.getElementById('account-editor-empty');
  const profile = getSelectedAccount();

  hydratePersonaOptions();

  if (!profile) {
    if (editorShell) {
      editorShell.classList.add('editor-shell--hidden');
    }
    if (editorEmpty) {
      editorEmpty.classList.remove('editor-empty--hidden');
    }
    setText('account-editor-title', 'Runtime-ready account settings');
    setText('account-editor-slot-pill', '0 slots');
    return;
  }

  if (editorShell) {
    editorShell.classList.remove('editor-shell--hidden');
  }
  if (editorEmpty) {
    editorEmpty.classList.add('editor-empty--hidden');
  }

  setText('account-editor-title', profile.display_name || profile.account_name || profile.steam_id);
  setText('account-editor-slot-pill', `${profile.game_count} slot${profile.game_count === 1 ? '' : 's'}`);

  setInputValue('editor-display-name', profile.display_name || '');
  setInputValue('editor-account-name', profile.account_name || '');
  setInputValue('editor-steam-id', profile.steam_id || '');
  setInputValue('editor-login-mode', profile.login_mode || '');
  setInputValue('editor-custom-status', profile.custom_status || '');
  setInputValue('editor-games-text', profile.games_text || '');
  setInputValue('editor-notes', profile.notes || '');
  setSelectValue('editor-persona-state', profile.persona_state || 'Online');

  const sessionSummary = profile.session_summary || {};
  setText('editor-session-path', profile.session_bundle_path || 'No session bundle file');
  setText('editor-session-modified', sessionSummary.modified_at || 'Unknown');
  setText('editor-session-refresh', sessionSummary.has_refresh_token ? 'Available' : 'Missing');
  setText('editor-session-access', sessionSummary.has_access_token ? 'Available' : 'Missing');
  setText('editor-session-id', sessionSummary.has_session_id ? 'Available' : 'Missing');
}

function fillRuntime() {
  const runtime = shellState.bootstrap.runtime;
  setText('runtime-slot-ceiling', `${runtime.slot_ceiling} app IDs`);
  setText('runtime-conflict-policy', runtime.conflict_policy);
  setText('runtime-reconnect-posture', runtime.reconnect_posture);
  setText('runtime-ready-count', String(runtime.counts?.ready_accounts || 0));
  setText('runtime-boosting-count', String(runtime.counts?.boosting_accounts || 0));
  setText('runtime-active-slot-count', String(runtime.counts?.active_slots || 0));
  setText('runtime-transport-name', runtime.transport_name || 'Unknown');
  setText('runtime-accounts-pill', `${runtime.counts?.tracked_accounts || 0} tracked`);
  setText('runtime-mode-pill', runtime.preview_mode ? 'Preview transport' : 'Live transport');
  fillRuntimeAccounts(runtime.statuses || []);

  const log = document.getElementById('runtime-log');
  if (log) {
    const lines = runtime.recent_events && runtime.recent_events.length
      ? runtime.recent_events
      : [
          '[runtime] runtime controller initialized',
          '[runtime] Steam client transport available',
          '[next] harden reconnect and live slot updates',
        ];
    log.textContent = lines.join('\n');
  }
}

function fillRuntimeAccounts(statuses) {
  const list = document.getElementById('runtime-account-list');
  if (!list) {
    return;
  }
  list.innerHTML = '';

  if (!statuses.length) {
    const empty = document.createElement('div');
    empty.className = 'runtime-account runtime-account--empty';
    empty.textContent = 'No runtime-tracked accounts yet. Add an account and save at least one session bundle first.';
    list.appendChild(empty);
    return;
  }

  statuses.forEach((status) => {
    const item = document.createElement('div');
    item.className = 'runtime-account';
    item.innerHTML = `
      <div class="runtime-account__header">
        <div>
          <div class="runtime-account__title">${escapeHtml(status.display_name)}</div>
          <div class="runtime-account__subtitle">${escapeHtml(status.login_mode || 'unknown')} • ${escapeHtml(status.persona_state || 'Online')}</div>
        </div>
        <div class="runtime-account__actions">
          <span class="runtime-state runtime-state--${escapeHtml(status.state)}">${escapeHtml(status.state_label || status.state)}</span>
          <button class="ghost-button" data-runtime-action="start" data-profile-id="${escapeHtml(status.profile_id)}" ${status.can_start ? '' : 'disabled'}>Start</button>
          <button class="ghost-button" data-runtime-action="stop" data-profile-id="${escapeHtml(status.profile_id)}" ${status.can_stop ? '' : 'disabled'}>Stop</button>
        </div>
      </div>
      <div class="runtime-account__meta">
        <div>
          <span>Configured</span>
          <strong>${status.configured_slot_count}</strong>
        </div>
        <div>
          <span>Active</span>
          <strong>${status.active_slot_count}</strong>
        </div>
        <div>
          <span>Session</span>
          <strong>${status.session_ready ? 'Ready' : 'Missing'}</strong>
        </div>
      </div>
      <div class="runtime-account__message">${escapeHtml(status.message || 'No runtime message available.')}</div>
    `;
    list.appendChild(item);
  });
}

function fillSettings() {
  const state = shellState.bootstrap;
  setText('settings-theme', state.theme);
  setText('settings-last-page', shellState.currentPage);
  setText('settings-window-size', `${state.window.width} x ${state.window.height}`);
  setText('settings-config-path', state.paths.config);
  setText('settings-sessions-path', state.paths.sessions);
  setText('settings-logs-path', state.paths.logs);
}

function updateWindowButtons() {
  const maximizeButton = document.getElementById('maximize-button');
  if (maximizeButton) {
    maximizeButton.textContent = shellState.maximized ? '[]' : '[ ]';
  }
}

function updateTopbarPill() {
  const pill = document.getElementById('topbar-pill');
  if (!pill) {
    return;
  }
  const boosting = shellState.bootstrap.runtime?.counts?.boosting_accounts || 0;
  const accounts = shellState.bootstrap.counts.accounts;
  if (boosting > 0) {
    pill.textContent = `${boosting} lane${boosting === 1 ? '' : 's'} active`;
    return;
  }
  pill.textContent = accounts > 0 ? `${accounts} account${accounts === 1 ? '' : 's'} ready` : 'Onboarding enabled';
}

function selectAuthMode(mode) {
  shellState.authMode = mode || 'credentials';

  document.querySelectorAll('.mode-pill').forEach((button) => {
    button.classList.toggle('is-active', button.dataset.authMode === shellState.authMode);
  });

  document.querySelectorAll('.auth-panel').forEach((panel) => {
    panel.classList.toggle('is-active', panel.dataset.authPanel === shellState.authMode);
  });
}

async function loginWithCredentials() {
  setButtonBusy('credentials-submit-button', true, 'Signing In...');
  setAuthStatus('info', 'Submitting credential login to Steam.');
  const result = await callApi('login_account_with_credentials', {
    display_name: getValue('cred-display-name'),
    account_name: getValue('cred-account-name'),
    password: getValue('cred-password'),
    steam_guard_code: getValue('cred-guard-code'),
  });
  await handleMutationResult(result, {
    successMessage: result?.message || 'Credential login completed.',
    clearIds: ['cred-password', 'cred-guard-code'],
    selectProfileId: result?.account?.profile_id,
  });
  setButtonBusy('credentials-submit-button', false, 'Sign In With Credentials');
}

async function loginWithRefreshToken() {
  setButtonBusy('refresh-submit-button', true, 'Attaching...');
  setAuthStatus('info', 'Building a session from the provided refresh token.');
  const result = await callApi('login_account_with_refresh_token', {
    display_name: getValue('refresh-display-name'),
    refresh_token: getValue('refresh-token'),
  });
  await handleMutationResult(result, {
    successMessage: result?.message || 'Refresh token attached.',
    clearIds: ['refresh-token'],
    selectProfileId: result?.account?.profile_id,
  });
  setButtonBusy('refresh-submit-button', false, 'Attach From Refresh Token');
}

async function beginQrLogin() {
  setButtonBusy('qr-start-button', true, 'Starting...');
  setAuthStatus('info', 'Creating a Steam QR login challenge.');
  const result = await callApi('begin_qr_account_login', {
    display_name: getValue('qr-display-name'),
    device_friendly_name: getValue('qr-device-name'),
  });

  if (!result?.ok) {
    setAuthStatus('error', result?.message || 'Unable to start QR login.');
    setButtonBusy('qr-start-button', false, 'Start QR Login');
    return;
  }

  shellState.pendingQrLogin = result;
  showQrPanel(result);
  startQrPolling();
  setAuthStatus('success', result.message || 'QR login challenge created.');
  setButtonBusy('qr-start-button', true, 'QR Session Active');
  const cancelButton = document.getElementById('qr-cancel-button');
  if (cancelButton) {
    cancelButton.disabled = false;
  }
}

function startQrPolling() {
  stopQrPolling();
  shellState.qrPollTimer = window.setInterval(async () => {
    await pollQrLogin();
  }, 2500);
}

function stopQrPolling() {
  if (shellState.qrPollTimer) {
    window.clearInterval(shellState.qrPollTimer);
    shellState.qrPollTimer = null;
  }
}

async function pollQrLogin() {
  if (!shellState.pendingQrLogin?.pending_id) {
    stopQrPolling();
    return;
  }

  const result = await callApi('poll_qr_account_login', shellState.pendingQrLogin.pending_id);
  if (!result) {
    return;
  }

  if (result.ok && result.status === 'waiting') {
    updateQrStatus(result.message || 'Waiting for Steam mobile confirmation.');
    return;
  }

  if (result.ok && result.status === 'approved') {
    stopQrPolling();
    await handleMutationResult(result, {
      successMessage: result.message || 'QR login approved.',
      clearIds: ['qr-display-name'],
      selectProfileId: result?.account?.profile_id,
    });
    clearQrPanel();
    return;
  }

  stopQrPolling();
  setAuthStatus('error', result.message || 'QR login failed.');
  clearQrPanel();
}

async function cancelQrLogin() {
  if (!shellState.pendingQrLogin?.pending_id) {
    clearQrPanel();
    return;
  }
  const result = await callApi('cancel_qr_account_login', shellState.pendingQrLogin.pending_id);
  stopQrPolling();
  clearQrPanel();
  if (result?.ok) {
    setAuthStatus('info', result.message || 'QR login cancelled.');
  } else {
    setAuthStatus('error', result?.message || 'Unable to cancel QR login.');
  }
}

function showQrPanel(payload) {
  const panel = document.getElementById('qr-panel');
  const image = document.getElementById('qr-image');
  const challenge = document.getElementById('qr-challenge-url');
  if (panel) {
    panel.classList.remove('qr-panel--hidden');
  }
  if (image) {
    image.src = payload.qr_image_url;
  }
  if (challenge) {
    challenge.value = payload.challenge_url || '';
  }
  updateQrStatus(payload.message || 'Approve the login from the Steam mobile app.');
}

function clearQrPanel() {
  shellState.pendingQrLogin = null;
  const panel = document.getElementById('qr-panel');
  const image = document.getElementById('qr-image');
  const challenge = document.getElementById('qr-challenge-url');
  const cancelButton = document.getElementById('qr-cancel-button');
  if (panel) {
    panel.classList.add('qr-panel--hidden');
  }
  if (image) {
    image.removeAttribute('src');
  }
  if (challenge) {
    challenge.value = '';
  }
  if (cancelButton) {
    cancelButton.disabled = true;
  }
  setButtonBusy('qr-start-button', false, 'Start QR Login');
  updateQrStatus('Approve the pending Steam sign-in request from your phone.');
}

function updateQrStatus(text) {
  setText('qr-title', 'Waiting for mobile approval');
  setText('qr-status-copy', text);
}

async function handleMutationResult(result, options = {}) {
  if (!result?.ok) {
    if (options.target === 'editor') {
      setEditorStatus('error', result?.message || 'The request failed.');
    } else if (options.target === 'runtime') {
      setRuntimeStatus('error', result?.message || 'The request failed.');
    } else {
      setAuthStatus('error', result?.message || 'The request failed.');
    }
    return;
  }

  if (Array.isArray(options.clearIds)) {
    options.clearIds.forEach((id) => {
      const input = document.getElementById(id);
      if (input) {
        input.value = '';
      }
    });
  }

  if (options.selectProfileId) {
    shellState.selectedAccountProfileId = options.selectProfileId;
  }

  if (options.target === 'editor') {
    setEditorStatus('success', options.successMessage || result.message || 'Saved.');
  } else if (options.target === 'runtime') {
    setRuntimeStatus('success', options.successMessage || result.message || 'Saved.');
  } else {
    setAuthStatus('success', options.successMessage || result.message || 'Saved.');
  }
  await hydrate();
}

function setAuthStatus(kind, message) {
  const banner = document.getElementById('auth-status');
  if (!banner) {
    return;
  }
  banner.textContent = message || '';
  banner.className = `status-banner status-banner--${kind}`;
  if (!message) {
    banner.classList.add('status-banner--hidden');
  }
}

function setEditorStatus(kind, message) {
  const banner = document.getElementById('account-editor-status');
  if (!banner) {
    return;
  }
  banner.textContent = message || '';
  banner.className = `status-banner status-banner--${kind}`;
  if (!message) {
    banner.classList.add('status-banner--hidden');
  }
}

function setRuntimeStatus(kind, message) {
  const banner = document.getElementById('runtime-status');
  if (!banner) {
    return;
  }
  banner.textContent = message || '';
  banner.className = `status-banner status-banner--${kind}`;
  if (!message) {
    banner.classList.add('status-banner--hidden');
  }
}

function setButtonBusy(id, busy, busyLabel) {
  const button = document.getElementById(id);
  if (!button) {
    return;
  }
  if (!button.dataset.defaultLabel) {
    button.dataset.defaultLabel = button.textContent;
  }
  button.disabled = busy;
  button.textContent = busy ? busyLabel : button.dataset.defaultLabel;
}

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) {
    element.textContent = value;
  }
}

function getValue(id) {
  const element = document.getElementById(id);
  return element ? element.value : '';
}

function setInputValue(id, value) {
  const element = document.getElementById(id);
  if (element) {
    element.value = value;
  }
}

function setSelectValue(id, value) {
  const element = document.getElementById(id);
  if (element) {
    element.value = value;
  }
}

function hydratePersonaOptions() {
  const select = document.getElementById('editor-persona-state');
  if (!select) {
    return;
  }
  const options = shellState.bootstrap.persona_states || ['Online'];
  select.innerHTML = '';
  options.forEach((state) => {
    const option = document.createElement('option');
    option.value = state;
    option.textContent = state;
    select.appendChild(option);
  });
}

function getSelectedAccount() {
  return (shellState.bootstrap.accounts || []).find(
    (account) => account.profile_id === shellState.selectedAccountProfileId,
  ) || null;
}

async function saveAccountProfile() {
  const profile = getSelectedAccount();
  if (!profile) {
    setEditorStatus('error', 'Select an account first.');
    return;
  }

  setButtonBusy('account-save-button', true, 'Saving...');
  setEditorStatus('info', 'Saving account settings.');
  const result = await callApi('save_account_profile', {
    profile_id: profile.profile_id,
    display_name: getValue('editor-display-name'),
    persona_state: getValue('editor-persona-state'),
    custom_status: getValue('editor-custom-status'),
    games_text: getValue('editor-games-text'),
    notes: getValue('editor-notes'),
  });
  await handleMutationResult(result, {
    target: 'editor',
    successMessage: result?.message || 'Account settings saved.',
    selectProfileId: profile.profile_id,
  });
  setButtonBusy('account-save-button', false, 'Save Account Settings');
}

async function refreshRuntimeReadiness() {
  setButtonBusy('runtime-refresh-button', true, 'Refreshing...');
  setRuntimeStatus('info', 'Refreshing runtime readiness from saved account state.');
  const result = await callApi('refresh_runtime_state');
  await handleMutationResult(result, {
    target: 'runtime',
    successMessage: result?.message || 'Runtime readiness refreshed.',
  });
  setButtonBusy('runtime-refresh-button', false, 'Refresh Readiness');
}

async function startAllRuntimeLanes() {
  setButtonBusy('runtime-start-all-button', true, 'Starting...');
  setRuntimeStatus('info', 'Starting every ready boost lane.');
  const result = await callApi('start_all_runtime');
  await handleMutationResult(result, {
    target: 'runtime',
    successMessage: result?.message || 'Runtime start sweep finished.',
  });
  setButtonBusy('runtime-start-all-button', false, 'Start Ready Lanes');
}

async function stopAllRuntimeLanes() {
  setButtonBusy('runtime-stop-all-button', true, 'Stopping...');
  setRuntimeStatus('info', 'Stopping all active boost lanes.');
  const result = await callApi('stop_all_runtime');
  await handleMutationResult(result, {
    target: 'runtime',
    successMessage: result?.message || 'All runtime lanes stopped.',
  });
  setButtonBusy('runtime-stop-all-button', false, 'Stop All Lanes');
}

async function startRuntimeLane(profileId) {
  setRuntimeStatus('info', 'Starting boost lane.');
  const result = await callApi('start_account_runtime', profileId);
  await handleMutationResult(result, {
    target: 'runtime',
    successMessage: result?.message || 'Boost lane started.',
  });
}

async function stopRuntimeLane(profileId) {
  setRuntimeStatus('info', 'Stopping boost lane.');
  const result = await callApi('stop_account_runtime', profileId);
  await handleMutationResult(result, {
    target: 'runtime',
    successMessage: result?.message || 'Boost lane stopped.',
  });
}

async function callApi(methodName, ...args) {
  if (
    window.pywebview &&
    window.pywebview.api &&
    typeof window.pywebview.api[methodName] === 'function'
  ) {
    try {
      return await window.pywebview.api[methodName](...args);
    } catch (error) {
      console.error(`pywebview API call failed: ${methodName}`, error);
      return null;
    }
  }

  if (methodName === 'set_last_page') {
    return { ok: true };
  }
  if (methodName === 'toggle_maximize_window') {
    shellState.maximized = !shellState.maximized;
    return { maximized: shellState.maximized };
  }

  return null;
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
