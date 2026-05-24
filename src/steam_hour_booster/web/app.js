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
  },
};

const shellState = {
  currentPage: 'dashboard',
  maximized: false,
  bootstrap: fallbackState,
  authMode: 'credentials',
  pendingQrLogin: null,
  qrPollTimer: null,
};

document.addEventListener('DOMContentLoaded', async () => {
  bindNavigation();
  bindWindowControls();
  bindDragRegion();
  bindAuthModes();
  bindAccountActions();
  await hydrate();
  window.setTimeout(() => document.body.classList.add('is-ready'), 10);
});

async function hydrate() {
  const bootstrap = await callApi('get_bootstrap_state');
  shellState.bootstrap = bootstrap || fallbackState;
  shellState.currentPage = shellState.bootstrap.last_page || 'dashboard';
  shellState.maximized = Boolean(shellState.bootstrap.window?.maximized);

  updateWindowButtons();
  fillOverview();
  fillAccounts();
  fillRuntime();
  fillSettings();
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
    item.className = 'account-item';
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

function fillRuntime() {
  const runtime = shellState.bootstrap.runtime;
  setText('runtime-slot-ceiling', `${runtime.slot_ceiling} app IDs`);
  setText('runtime-conflict-policy', runtime.conflict_policy);
  setText('runtime-reconnect-posture', runtime.reconnect_posture);
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
  const accounts = shellState.bootstrap.counts.accounts;
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
    setAuthStatus('error', result?.message || 'The request failed.');
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

  setAuthStatus('success', options.successMessage || result.message || 'Saved.');
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
