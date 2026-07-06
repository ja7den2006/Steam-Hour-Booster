const fallbackState = {
  last_page: 'overview',
  counts: {
    accounts: 0,
    configured_slots: 0,
  },
  accounts: [],
  activity_log: [],
  popular_games: [],
  persona_states: ['Online', 'Away', 'Busy', 'Invisible'],
  conflict_policies: [
    { value: 'pause', label: 'Pause and Wait' },
    { value: 'kick', label: 'Force Kick' },
    { value: 'yield', label: 'Yield to New Session' },
  ],
  runtime: {
    counts: {
      tracked_accounts: 0,
      ready_accounts: 0,
      boosting_accounts: 0,
      paused_accounts: 0,
      blocked_accounts: 0,
      active_slots: 0,
    },
    statuses: [],
    recent_events: [
      '[runtime] runtime controller initialized',
      '[runtime] waiting for activity',
    ],
  },
};

const shellState = {
  currentPage: 'overview',
  maximized: false,
  bootstrap: fallbackState,
  authMode: 'credentials',
  credentialGuardRequirement: null,
  pendingCredentialLogin: null,
  selectedAccountProfileId: null,
  editorGames: [],
  editorDirty: false,
  editorHydratedProfileId: null,
  pendingQrLogin: null,
  qrPollTimer: null,
  runtimePollTimer: null,
};

document.addEventListener('DOMContentLoaded', async () => {
  bindNavigation();
  bindWindowControls();
  bindAuthModes();
  bindAccountActions();
  bindEditorDraftInputs();
  bindOverviewActions();
  await waitForBridge();
  await hydrate();
  startRuntimePolling();
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
  shellState.maximized = Boolean(shellState.bootstrap.window?.maximized);

  const requestedPage = String(shellState.bootstrap.last_page || shellState.currentPage || 'overview');
  shellState.currentPage = requestedPage === 'accounts' ? 'accounts' : 'overview';
  if (requestedPage !== shellState.currentPage) {
    await callApi('set_last_page', shellState.currentPage);
    shellState.bootstrap.last_page = shellState.currentPage;
  }

  reconcileSelectedProfile();
  updateWindowButtons();
  fillOverview();
  fillAccounts();
  fillAccountEditor();
  await selectPage(shellState.currentPage, false);
}

function reconcileSelectedProfile() {
  const profileIds = (shellState.bootstrap.accounts || []).map((account) => account.profile_id);
  if (!profileIds.length) {
    shellState.selectedAccountProfileId = null;
  } else if (!profileIds.includes(shellState.selectedAccountProfileId)) {
    shellState.selectedAccountProfileId = profileIds[0];
  }
}

function startRuntimePolling() {
  stopRuntimePolling();
  shellState.runtimePollTimer = window.setInterval(async () => {
    await pollRuntimeState();
  }, 4000);
}

function stopRuntimePolling() {
  if (shellState.runtimePollTimer) {
    window.clearInterval(shellState.runtimePollTimer);
    shellState.runtimePollTimer = null;
  }
}

async function pollRuntimeState(force = false) {
  if (document.hidden && !force) {
    return;
  }

  const result = await callApi('poll_runtime_state');
  if (!result?.ok || !result.state) {
    return;
  }

  shellState.bootstrap = result.state;
  reconcileSelectedProfile();
  fillOverview();
  fillAccounts();
  fillAccountEditor();
}

function bindNavigation() {
  document.querySelectorAll('.nav-button').forEach((button) => {
    button.addEventListener('click', async () => {
      await selectPage(button.dataset.page, true);
    });
  });
}

async function selectPage(pageKey, persist) {
  shellState.currentPage = pageKey === 'accounts' ? 'accounts' : 'overview';

  document.querySelectorAll('.page').forEach((page) => {
    page.classList.toggle('is-active', page.dataset.page === shellState.currentPage);
  });

  document.querySelectorAll('.nav-button').forEach((button) => {
    button.classList.toggle('is-active', button.dataset.page === shellState.currentPage);
  });

  if (persist) {
    await callApi('set_last_page', shellState.currentPage);
    if (shellState.bootstrap) {
      shellState.bootstrap.last_page = shellState.currentPage;
    }
  }
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

function updateWindowButtons() {
  const maximizeButton = document.getElementById('maximize-button');
  if (maximizeButton) {
    maximizeButton.textContent = shellState.maximized ? '[]' : '[ ]';
  }
}

function bindAuthModes() {
  document.querySelectorAll('.mode-pill').forEach((button) => {
    button.addEventListener('click', () => {
      selectAuthMode(button.dataset.authMode);
    });
  });

  const credentialButton = document.getElementById('credentials-submit-button');
  if (credentialButton) {
    credentialButton.addEventListener('click', loginWithCredentials);
  }

  const guardSubmitButton = document.getElementById('guard-submit-button');
  if (guardSubmitButton) {
    guardSubmitButton.addEventListener('click', submitGuardCode);
  }

  const guardBackButton = document.getElementById('guard-back-button');
  if (guardBackButton) {
    guardBackButton.addEventListener('click', backFromGuardStage);
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

function selectAuthMode(mode) {
  shellState.authMode = mode === 'qr' ? 'qr' : 'credentials';

  document.querySelectorAll('.mode-pill').forEach((button) => {
    button.classList.toggle('is-active', button.dataset.authMode === shellState.authMode);
  });

  document.querySelectorAll('.auth-panel').forEach((panel) => {
    panel.classList.toggle('is-active', panel.dataset.authPanel === shellState.authMode);
  });

  if (shellState.authMode !== 'credentials') {
    clearCredentialGuardFlow({ keepInputs: true });
  }
}

function setCredentialStage(stage) {
  document.querySelectorAll('[data-auth-stage]').forEach((panel) => {
    panel.classList.toggle('is-active', panel.dataset.authStage === stage);
  });
}

async function loginWithCredentials() {
  const accountName = getValue('cred-account-name').trim();
  const password = getValue('cred-password');

  if (!accountName || !password) {
    setAuthStatus('error', 'Username and password are required.');
    return;
  }

  setButtonBusy('credentials-submit-button', true, 'Signing In...');
  setAuthStatus('info', 'Submitting sign-in to Steam.');

  const result = await callApi('login_account_with_credentials', {
    account_name: accountName,
    password,
  });

  if (result?.status === 'steam_guard_required') {
    shellState.pendingCredentialLogin = { accountName, password };
    applyCredentialGuardRequirement(result);
    setAuthStatus('info', result.message || 'Steam Guard code required.');
    setButtonBusy('credentials-submit-button', false, 'Sign In');
    return;
  }

  clearCredentialGuardFlow({ keepInputs: true });
  await handleMutationResult(result, {
    target: 'auth',
    successMessage: result?.message || 'Credential login completed.',
    clearIds: ['cred-password', 'guard-code'],
    selectProfileId: result?.account?.profile_id,
  });
  setButtonBusy('credentials-submit-button', false, 'Sign In');
}

async function submitGuardCode() {
  if (!shellState.pendingCredentialLogin) {
    backFromGuardStage();
    return;
  }

  const guardCode = getValue('guard-code').trim();
  if (!guardCode) {
    setAuthStatus('error', 'Enter the Steam Guard code first.');
    return;
  }

  setButtonBusy('guard-submit-button', true, 'Submitting...');
  setAuthStatus('info', 'Submitting Steam Guard code.');

  const result = await callApi('login_account_with_credentials', {
    account_name: shellState.pendingCredentialLogin.accountName,
    password: shellState.pendingCredentialLogin.password,
    steam_guard_code: guardCode,
  });

  if (result?.status === 'steam_guard_required') {
    shellState.credentialGuardRequirement = result;
    applyCredentialGuardRequirement(result);
    setAuthStatus('info', result.message || 'Steam Guard code required.');
    setButtonBusy('guard-submit-button', false, 'Submit Code');
    return;
  }

  clearCredentialGuardFlow({ keepInputs: true });
  await handleMutationResult(result, {
    target: 'auth',
    successMessage: result?.message || 'Steam Guard login completed.',
    clearIds: ['cred-password', 'guard-code'],
    selectProfileId: result?.account?.profile_id,
  });
  setButtonBusy('guard-submit-button', false, 'Submit Code');
}

function applyCredentialGuardRequirement(requirement) {
  shellState.credentialGuardRequirement = requirement || null;
  setCredentialStage('guard');
  setText('guard-code-label', requirement?.code_label || 'Steam Guard Code');
  setText(
    'guard-status-copy',
    requirement?.associated_message
      ? `${requirement.message} Destination: ${requirement.associated_message}.`
      : (requirement?.message || 'Enter the code from Steam to finish sign-in.'),
  );
  setInputValue('guard-code', '');
  const input = document.getElementById('guard-code');
  if (input) {
    input.placeholder = requirement?.code_placeholder || 'Enter code';
    input.focus();
    input.select();
  }
}

function backFromGuardStage() {
  if (shellState.pendingCredentialLogin) {
    setInputValue('cred-account-name', shellState.pendingCredentialLogin.accountName || '');
    setInputValue('cred-password', shellState.pendingCredentialLogin.password || '');
  }
  clearCredentialGuardFlow({ keepInputs: true });
  setAuthStatus('info', 'Steam Guard step cancelled.');
}

function clearCredentialGuardFlow(options = {}) {
  shellState.credentialGuardRequirement = null;
  shellState.pendingCredentialLogin = null;
  setCredentialStage('credentials');
  setText('guard-code-label', 'Steam Guard Code');
  setText('guard-status-copy', 'Enter the code from Steam to finish sign-in.');
  setInputValue('guard-code', '');
  if (!options.keepInputs) {
    setInputValue('cred-account-name', '');
    setInputValue('cred-password', '');
  }
  setButtonBusy('guard-submit-button', false, 'Submit Code');
}

async function beginQrLogin() {
  clearCredentialGuardFlow({ keepInputs: true });
  setButtonBusy('qr-start-button', true, 'Starting...');
  setAuthStatus('info', 'Creating a Steam QR login challenge.');

  const result = await callApi('begin_qr_account_login', {});
  if (!result?.ok) {
    setAuthStatus('error', result?.message || 'Unable to start QR login.');
    setButtonBusy('qr-start-button', false, 'Start QR Login');
    return;
  }

  shellState.pendingQrLogin = result;
  showQrPanel(result);
  startQrPolling();
  setAuthStatus('success', result.message || 'QR login challenge created.');
  setButtonBusy('qr-start-button', true, 'Waiting...');
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
    clearQrPanel();
    await handleMutationResult(result, {
      target: 'auth',
      successMessage: result.message || 'QR login approved.',
      selectProfileId: result?.account?.profile_id,
    });
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
  updateQrStatus(payload.message || 'Approve the Steam sign-in on your phone.');
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
  updateQrStatus('Approve the Steam sign-in on your phone.');
}

function updateQrStatus(text) {
  setText('qr-title', 'Waiting for approval');
  setText('qr-status-copy', text);
}

function bindOverviewActions() {
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
        } else if (action === 'toggle-boost') {
          await setAccountBoostEnabled(
            target.dataset.profileId,
            target.dataset.nextBoostEnabled === 'true',
            'runtime',
          );
        } else if (action === 'edit') {
          await focusAccountProfile(target.dataset.profileId);
        }
      } finally {
        target.disabled = false;
      }
    });
  }

  const refreshButton = document.getElementById('overview-refresh-button');
  if (refreshButton) {
    refreshButton.addEventListener('click', refreshRuntimeReadiness);
  }

  const startAllButton = document.getElementById('overview-start-all-button');
  if (startAllButton) {
    startAllButton.addEventListener('click', startAllRuntimeLanes);
  }

  const stopAllButton = document.getElementById('overview-stop-all-button');
  if (stopAllButton) {
    stopAllButton.addEventListener('click', stopAllRuntimeLanes);
  }

  const openLogButton = document.getElementById('overview-open-log-button');
  if (openLogButton) {
    openLogButton.addEventListener('click', openRuntimeLogFile);
  }
}

function bindAccountActions() {
  const list = document.getElementById('account-list');
  if (list) {
    list.addEventListener('click', async (event) => {
      const actionTarget = event.target.closest('[data-account-action]');
      if (actionTarget?.dataset.profileId) {
        const action = actionTarget.dataset.accountAction;
        const profileId = actionTarget.dataset.profileId;
        if (action === 'remove') {
          actionTarget.disabled = true;
          setAuthStatus('info', 'Removing account.');
          const result = await callApi('remove_account', profileId);
          await handleMutationResult(result, {
            target: 'auth',
            successMessage: result?.message || 'Account removed.',
          });
        } else if (action === 'toggle-boost') {
          actionTarget.disabled = true;
          try {
            await setAccountBoostEnabled(
              profileId,
              actionTarget.dataset.nextBoostEnabled === 'true',
              'editor',
            );
          } finally {
            actionTarget.disabled = false;
          }
        }
        return;
      }

      const selectedCard = event.target.closest('[data-account-select]');
      if (selectedCard?.dataset.profileId) {
        shellState.selectedAccountProfileId = selectedCard.dataset.profileId;
        resetEditorDraftState(null);
        fillAccounts();
        fillAccountEditor();
      }
    });
  }

  const saveButton = document.getElementById('account-save-button');
  if (saveButton) {
    saveButton.addEventListener('click', saveAccountProfile);
  }

  const addPresetButton = document.getElementById('editor-add-preset-button');
  if (addPresetButton) {
    addPresetButton.addEventListener('click', addPresetGameToEditor);
  }

  const addCustomGameButton = document.getElementById('editor-add-custom-game-button');
  if (addCustomGameButton) {
    addCustomGameButton.addEventListener('click', addCustomGameToEditor);
  }

  const gameList = document.getElementById('editor-games-list');
  if (gameList) {
    gameList.addEventListener('click', (event) => {
      const target = event.target.closest('[data-game-action]');
      if (!target?.dataset.gameAction || !target?.dataset.gameAppId) {
        return;
      }
      const action = target.dataset.gameAction;
      const appId = Number(target.dataset.gameAppId);
      if (action === 'remove') {
        removeEditorGame(appId);
      } else if (action === 'toggle') {
        toggleEditorGameEnabled(appId);
      } else if (action === 'up') {
        moveEditorGame(appId, -1);
      } else if (action === 'down') {
        moveEditorGame(appId, 1);
      }
    });
  }

  const appearOnlineToggle = document.getElementById('editor-appear-online');
  if (appearOnlineToggle) {
    appearOnlineToggle.addEventListener('change', updatePresenceEditorState);
  }

  const autoReplyToggle = document.getElementById('editor-auto-reply-enabled');
  if (autoReplyToggle) {
    autoReplyToggle.addEventListener('change', updateAutoReplyEditorState);
  }
}

function bindEditorDraftInputs() {
  const editorInputIds = [
    'editor-boost-enabled',
    'editor-appear-online',
    'editor-display-name',
    'editor-persona-state',
    'editor-conflict-policy',
    'editor-preset-game',
    'editor-custom-app-id',
    'editor-custom-game-title',
    'editor-auto-reply-enabled',
    'editor-auto-reply-message',
    'editor-auto-reply-cooldown',
    'editor-auto-reply-timeout',
  ];

  editorInputIds.forEach((id) => {
    const element = document.getElementById(id);
    if (!element) {
      return;
    }
    element.addEventListener('input', markEditorDirty);
    element.addEventListener('change', markEditorDirty);
  });
}

function fillOverview() {
  const state = shellState.bootstrap || fallbackState;
  const runtime = state.runtime || fallbackState.runtime;

  setText('overview-account-count', String(state.counts?.accounts || 0));
  setText('overview-active-count', String(runtime.counts?.boosting_accounts || 0));
  setText('overview-ready-count', String(runtime.counts?.ready_accounts || 0));
  setText('overview-slot-count', String(runtime.counts?.active_slots || 0));

  fillRuntimeAccounts(runtime.statuses || []);

  const log = document.getElementById('runtime-log');
  if (log) {
    const lines = Array.isArray(state.activity_log) && state.activity_log.length
      ? state.activity_log
      : (runtime.recent_events && runtime.recent_events.length
        ? runtime.recent_events
        : ['[ui] Waiting for activity...']);
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
    empty.className = 'runtime-card';
    empty.textContent = 'No accounts yet. Add one from the Accounts tab.';
    list.appendChild(empty);
    return;
  }

  statuses.forEach((status) => {
    const boostActionLabel = status.boost_enabled ? 'Disable' : 'Enable';
    const item = document.createElement('div');
    item.className = 'runtime-card';
    item.innerHTML = `
      <div class="runtime-card__header">
        <div>
          <div class="runtime-card__title">${escapeHtml(status.display_name)}</div>
          <div class="runtime-card__subtitle">${escapeHtml(formatRuntimeSubtitle(status))}</div>
        </div>
        <div class="runtime-card__actions">
          <span class="runtime-state runtime-state--${escapeHtml(status.state)}">${escapeHtml(status.state_label || status.state)}</span>
          <button class="ghost-button" data-runtime-action="edit" data-profile-id="${escapeHtml(status.profile_id)}">Edit</button>
          <button class="ghost-button" data-runtime-action="toggle-boost" data-profile-id="${escapeHtml(status.profile_id)}" data-next-boost-enabled="${status.boost_enabled ? 'false' : 'true'}">${escapeHtml(boostActionLabel)}</button>
          <button class="ghost-button" data-runtime-action="start" data-profile-id="${escapeHtml(status.profile_id)}" ${status.can_start ? '' : 'disabled'}>Start</button>
          <button class="ghost-button" data-runtime-action="stop" data-profile-id="${escapeHtml(status.profile_id)}" ${status.can_stop ? '' : 'disabled'}>Stop</button>
        </div>
      </div>
      <div class="runtime-card__meta">
        <div>
          <span>Games</span>
          <strong>${escapeHtml(formatGameIds(status.configured_app_ids || []))}</strong>
        </div>
        <div>
          <span>Boost</span>
          <strong>${status.boost_enabled ? 'On' : 'Off'}</strong>
        </div>
        <div>
          <span>Presence</span>
          <strong>${escapeHtml(status.effective_persona_state || status.persona_state || 'Online')}</strong>
        </div>
        <div>
          <span>Library</span>
          <strong>${escapeHtml(formatOwnedGamesValidationState(status.owned_games_validation_state))}</strong>
        </div>
      </div>
      <div class="runtime-card__message">${escapeHtml(status.message || 'No booster message yet.')}</div>
      ${status.last_error ? `<div class="runtime-card__detail">Last issue: ${escapeHtml(status.last_error)}</div>` : ''}
    `;
    list.appendChild(item);
  });
}

function fillAccounts() {
  const state = shellState.bootstrap || fallbackState;
  setText('accounts-count-pill', `${state.counts?.accounts || 0} saved`);

  const list = document.getElementById('account-list');
  if (!list) {
    return;
  }
  list.innerHTML = '';

  if (!state.accounts.length) {
    const empty = document.createElement('div');
    empty.className = 'account-item';
    empty.textContent = 'No accounts saved yet.';
    list.appendChild(empty);
    return;
  }

  state.accounts.forEach((account) => {
    const runtimeStatus = getRuntimeStatusByProfileId(account.profile_id);
    const boostActionLabel = account.boost_enabled ? 'Disable' : 'Enable';
    const item = document.createElement('div');
    item.className = `account-item${shellState.selectedAccountProfileId === account.profile_id ? ' account-item--selected' : ''}`;
    item.dataset.accountSelect = 'true';
    item.dataset.profileId = account.profile_id;
    item.innerHTML = `
      <div class="account-item__header">
        <div>
          <div class="account-item__title">${escapeHtml(account.display_name)}</div>
          <div class="account-item__subtitle">${escapeHtml(account.account_name || account.steam_id || 'Unknown account')}</div>
        </div>
        <div class="account-item__actions">
          <span class="pill pill--subtle">${escapeHtml(formatLoginMode(account.login_mode))}</span>
          <button class="ghost-button" data-account-action="toggle-boost" data-profile-id="${escapeHtml(account.profile_id)}" data-next-boost-enabled="${account.boost_enabled ? 'false' : 'true'}">${escapeHtml(boostActionLabel)}</button>
          <button class="ghost-button" data-account-action="remove" data-profile-id="${escapeHtml(account.profile_id)}">Remove</button>
        </div>
      </div>
      <div class="account-item__meta">
        <div>
          <span>Status</span>
          <strong>${escapeHtml(runtimeStatus?.state_label || 'Idle')}</strong>
        </div>
        <div>
          <span>Games</span>
          <strong>${escapeHtml(formatGameQueueSummary(account.enabled_game_count || 0, account.game_count || 0))}</strong>
        </div>
        <div>
          <span>Session</span>
          <strong>${escapeHtml(formatSessionStatus(account, runtimeStatus))}</strong>
        </div>
        <div>
          <span>Library</span>
          <strong>${escapeHtml(formatOwnedGamesValidationState(runtimeStatus?.owned_games_validation_state))}</strong>
        </div>
      </div>
    `;
    list.appendChild(item);
  });
}

function fillAccountEditor() {
  const editorShell = document.getElementById('account-editor-shell');
  const editorEmpty = document.getElementById('account-editor-empty');
  const profile = getSelectedAccount();
  const runtimeStatus = getSelectedRuntimeStatus();
  const preserveDraft = shouldPreserveEditorDraft(profile);

  hydratePersonaOptions();
  hydrateConflictPolicyOptions();
  hydratePopularGameOptions();

  if (!profile) {
    resetEditorDraftState(null);
    if (editorShell) {
      editorShell.classList.add('editor-shell--hidden');
    }
    if (editorEmpty) {
      editorEmpty.classList.remove('editor-empty--hidden');
    }
    setEditorIdentity(null);
    setText('account-editor-slot-pill', '0 games');
    shellState.editorGames = [];
    renderEditorGames();
    setChecked('editor-auto-reply-enabled', false);
    updateAutoReplyEditorState();
    setText('editor-runtime-state', 'Idle');
    setText('editor-session-status', 'Missing');
    setText('editor-library-status', 'Pending');
    setText('editor-runtime-message', 'No booster message yet.');
    setText('editor-library-message', 'Library validation details will appear here.');
    return;
  }

  if (editorShell) {
    editorShell.classList.remove('editor-shell--hidden');
  }
  if (editorEmpty) {
    editorEmpty.classList.add('editor-empty--hidden');
  }

  if (preserveDraft) {
    setEditorIdentity(profile);
    renderEditorGames();
    updatePresenceEditorState();
    updateAutoReplyEditorState();
    fillEditorHealth(profile, runtimeStatus);
    return;
  }

  resetEditorDraftState(profile.profile_id);
  setEditorIdentity(profile);
  setText('account-editor-slot-pill', formatGameQueueSummary(profile.enabled_game_count || 0, profile.game_count || 0));

  setChecked('editor-boost-enabled', Boolean(profile.boost_enabled));
  setChecked('editor-appear-online', profile.appear_online !== false);
  setChecked('editor-auto-reply-enabled', Boolean(profile.auto_reply_enabled));
  setInputValue('editor-display-name', profile.display_name || '');
  setInputValue('editor-account-name', profile.account_name || '');
  setInputValue('editor-steam-id', profile.steam_id || '');
  setInputValue('editor-login-mode', formatLoginMode(profile.login_mode || 'credentials'));
  setInputValue('editor-auto-reply-message', profile.auto_reply_message || '');
  setInputValue('editor-auto-reply-cooldown', String(profile.auto_reply_cooldown_seconds || 180));
  setInputValue('editor-auto-reply-timeout', String(profile.auto_reply_timeout_seconds || 1800));
  setSelectValue('editor-persona-state', profile.persona_state || 'Online');
  setSelectValue('editor-conflict-policy', profile.conflict_policy || 'pause');
  setInputValue('editor-custom-app-id', '');
  setInputValue('editor-custom-game-title', '');

  shellState.editorGames = Array.isArray(profile.games)
    ? profile.games.map((game) => ({
        app_id: Number(game.app_id),
        title: game.title || '',
        enabled: game.enabled !== false,
      }))
    : [];

  renderEditorGames();
  updatePresenceEditorState();
  updateAutoReplyEditorState();
  fillEditorHealth(profile, runtimeStatus);
}

function setEditorIdentity(profile) {
  if (!profile) {
    setText('account-editor-title', 'Select an account');
    setText('account-editor-subtitle', 'Select an account to edit its booster settings.');
    setText('editor-account-name-label', 'Username');
    return;
  }

  const title = profile.display_name || profile.account_name || profile.steam_id || 'Account';
  const username = profile.account_name || profile.steam_id || 'Unknown account';
  const isEditing = shellState.editorDirty && shellState.editorHydratedProfileId === profile.profile_id;
  const editingSuffix = isEditing ? ' (Currently Editing)' : '';
  setText('account-editor-title', title);
  setText('account-editor-subtitle', `${username}${editingSuffix}`);
  setText('editor-account-name-label', `Username${editingSuffix}`);
}

function shouldPreserveEditorDraft(profile) {
  return Boolean(
    profile
      && shellState.editorDirty
      && shellState.editorHydratedProfileId === profile.profile_id,
  );
}

function markEditorDirty() {
  const profile = getSelectedAccount();
  if (!profile) {
    return;
  }
  shellState.editorDirty = true;
  shellState.editorHydratedProfileId = profile.profile_id;
  setEditorIdentity(profile);
}

function resetEditorDraftState(profileId = null) {
  shellState.editorDirty = false;
  shellState.editorHydratedProfileId = profileId;
}

function fillEditorHealth(profile, status) {
  setText('editor-runtime-state', status?.state_label || 'Idle');
  setText('editor-session-status', formatSessionStatus(profile, status));
  setText('editor-library-status', formatOwnedGamesValidationState(status?.owned_games_validation_state));
  setText('editor-runtime-message', status?.message || 'No booster message yet.');
  setText('editor-library-message', formatOwnedGamesValidationDetail(status));
}

function hydratePersonaOptions() {
  const select = document.getElementById('editor-persona-state');
  if (!select) {
    return;
  }
  const currentValue = select.value;
  const options = shellState.bootstrap.persona_states || ['Online'];
  select.innerHTML = '';
  options.forEach((value) => {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = value;
    select.appendChild(option);
  });
  if (currentValue && Array.from(select.options).some((option) => option.value === currentValue)) {
    select.value = currentValue;
  }
}

function hydrateConflictPolicyOptions() {
  const select = document.getElementById('editor-conflict-policy');
  if (!select) {
    return;
  }
  const currentValue = select.value;
  const options = shellState.bootstrap.conflict_policies || fallbackState.conflict_policies;
  select.innerHTML = '';
  options.forEach((policy) => {
    const option = document.createElement('option');
    option.value = policy.value;
    option.textContent = policy.label;
    select.appendChild(option);
  });
  if (currentValue && Array.from(select.options).some((option) => option.value === currentValue)) {
    select.value = currentValue;
  }
}

function hydratePopularGameOptions() {
  const select = document.getElementById('editor-preset-game');
  if (!select) {
    return;
  }
  const currentValue = select.value;
  const games = shellState.bootstrap.popular_games || [];
  select.innerHTML = '';

  const placeholder = document.createElement('option');
  placeholder.value = '';
  placeholder.textContent = 'Select a game';
  select.appendChild(placeholder);

  games.forEach((game) => {
    const option = document.createElement('option');
    option.value = String(game.app_id);
    option.textContent = `${game.app_id} (${game.title})`;
    option.dataset.title = game.title || '';
    select.appendChild(option);
  });

  if (currentValue && Array.from(select.options).some((option) => option.value === currentValue)) {
    select.value = currentValue;
  }
}

function updatePresenceEditorState() {
  const appearOnline = document.getElementById('editor-appear-online');
  const personaState = document.getElementById('editor-persona-state');
  if (!appearOnline || !personaState) {
    return;
  }
  personaState.disabled = !appearOnline.checked;
}

function updateAutoReplyEditorState() {
  const toggle = document.getElementById('editor-auto-reply-enabled');
  const shell = document.getElementById('editor-auto-reply-fields');
  if (!toggle || !shell) {
    return;
  }
  const enabled = Boolean(toggle.checked);
  shell.classList.toggle('editor-auto-reply-fields--disabled', !enabled);
  shell.querySelectorAll('textarea, input').forEach((element) => {
    element.disabled = !enabled;
  });
}

function renderEditorGames() {
  const list = document.getElementById('editor-games-list');
  const empty = document.getElementById('editor-games-empty');
  if (!list || !empty) {
    return;
  }

  syncEditorGamesTextField();
  list.innerHTML = '';

  const games = shellState.editorGames || [];
  const enabledCount = games.filter((game) => game.enabled !== false).length;
  setText('editor-games-count-pill', formatGameQueueSummary(enabledCount, games.length));
  setText('account-editor-slot-pill', formatGameQueueSummary(enabledCount, games.length));

  if (!games.length) {
    empty.classList.remove('game-slot-empty--hidden');
    return;
  }

  empty.classList.add('game-slot-empty--hidden');
  games.forEach((game, index) => {
    const item = document.createElement('div');
    item.className = `game-slot-item${game.enabled === false ? ' game-slot-item--disabled' : ''}`;
    item.innerHTML = `
      <div class="game-slot-item__copy">
        <div class="game-slot-item__title-row">
          <strong>${escapeHtml(game.title || `App ${game.app_id}`)}</strong>
          <span class="pill pill--subtle">${game.enabled === false ? 'Off' : 'On'}</span>
        </div>
        <small>${escapeHtml(`App ${game.app_id} • Slot ${index + 1}`)}</small>
      </div>
      <div class="game-slot-item__actions">
        <button class="ghost-button" data-game-action="up" data-game-app-id="${escapeHtml(String(game.app_id))}" ${index === 0 ? 'disabled' : ''}>Up</button>
        <button class="ghost-button" data-game-action="down" data-game-app-id="${escapeHtml(String(game.app_id))}" ${index === (games.length - 1) ? 'disabled' : ''}>Down</button>
        <button class="ghost-button" data-game-action="toggle" data-game-app-id="${escapeHtml(String(game.app_id))}">${game.enabled === false ? 'Enable' : 'Disable'}</button>
        <button class="ghost-button" data-game-action="remove" data-game-app-id="${escapeHtml(String(game.app_id))}">Remove</button>
      </div>
    `;
    list.appendChild(item);
  });
}

function syncEditorGamesTextField() {
  const lines = (shellState.editorGames || []).map((game) => (
    game.title ? `${game.app_id}: ${game.title}` : String(game.app_id)
  ));
  setInputValue('editor-games-text', lines.join('\n'));
}

function addGameToEditor(appId, title = '') {
  const numericAppId = Number(appId || 0);
  if (!Number.isInteger(numericAppId) || numericAppId <= 0) {
    setEditorStatus('error', 'Game app IDs must be positive integers.');
    return;
  }

  const existingIndex = findEditorGameIndex(numericAppId);
  if (existingIndex >= 0) {
    const nextGames = [...(shellState.editorGames || [])];
    const existingGame = { ...nextGames[existingIndex] };
    if (!existingGame.title && String(title || '').trim()) {
      existingGame.title = String(title || '').trim();
    }
    if (existingGame.enabled === false) {
      existingGame.enabled = true;
      nextGames[existingIndex] = existingGame;
      shellState.editorGames = nextGames;
      markEditorDirty();
      renderEditorGames();
      setEditorStatus('success', `Re-enabled app ${numericAppId}.`);
      return;
    }
    setEditorStatus('info', `App ${numericAppId} is already in the queue.`);
    return;
  }

  if ((shellState.editorGames || []).length >= 32) {
    setEditorStatus('error', 'A single account can only queue up to 32 game slots.');
    return;
  }

  shellState.editorGames = [
    ...(shellState.editorGames || []),
    {
      app_id: numericAppId,
      title: String(title || '').trim(),
      enabled: true,
    },
  ];
  markEditorDirty();
  renderEditorGames();
  setEditorStatus('success', `Added app ${numericAppId}.`);
}

function addPresetGameToEditor() {
  const select = document.getElementById('editor-preset-game');
  if (!select?.value) {
    setEditorStatus('error', 'Select a game first.');
    return;
  }
  const selected = select.options[select.selectedIndex];
  addGameToEditor(Number(select.value), selected?.dataset.title || '');
}

function addCustomGameToEditor() {
  addGameToEditor(getValue('editor-custom-app-id'), getValue('editor-custom-game-title'));
  setInputValue('editor-custom-app-id', '');
  setInputValue('editor-custom-game-title', '');
}

function removeEditorGame(appId) {
  shellState.editorGames = (shellState.editorGames || []).filter((game) => Number(game.app_id) !== Number(appId));
  markEditorDirty();
  renderEditorGames();
  setEditorStatus('info', `Removed app ${appId}.`);
}

function toggleEditorGameEnabled(appId) {
  const index = findEditorGameIndex(appId);
  if (index < 0) {
    return;
  }
  const nextGames = [...(shellState.editorGames || [])];
  const nextGame = { ...nextGames[index] };
  nextGame.enabled = !(nextGame.enabled !== false);
  nextGames[index] = nextGame;
  shellState.editorGames = nextGames;
  markEditorDirty();
  renderEditorGames();
  setEditorStatus('info', nextGame.enabled === false ? `Disabled app ${appId}.` : `Enabled app ${appId}.`);
}

function moveEditorGame(appId, direction) {
  const index = findEditorGameIndex(appId);
  if (index < 0) {
    return;
  }
  const nextIndex = index + Number(direction || 0);
  const games = [...(shellState.editorGames || [])];
  if (nextIndex < 0 || nextIndex >= games.length) {
    return;
  }
  const [game] = games.splice(index, 1);
  games.splice(nextIndex, 0, game);
  shellState.editorGames = games;
  markEditorDirty();
  renderEditorGames();
  setEditorStatus('info', `Moved app ${appId} to slot ${nextIndex + 1}.`);
}

function findEditorGameIndex(appId) {
  return (shellState.editorGames || []).findIndex((game) => Number(game.app_id) === Number(appId));
}

async function saveAccountProfile() {
  const profile = getSelectedAccount();
  if (!profile) {
    setEditorStatus('error', 'Select an account first.');
    return;
  }

  setButtonBusy('account-save-button', true, 'Saving...');
  setEditorStatus('info', 'Saving account settings.');
  syncEditorGamesTextField();

  const result = await callApi('save_account_profile', {
    profile_id: profile.profile_id,
    display_name: getValue('editor-display-name'),
    boost_enabled: getChecked('editor-boost-enabled'),
    appear_online: getChecked('editor-appear-online'),
    persona_state: getValue('editor-persona-state'),
    conflict_policy: getValue('editor-conflict-policy'),
    custom_status: profile.custom_status || '',
    auto_reply_enabled: getChecked('editor-auto-reply-enabled'),
    auto_reply_message: getValue('editor-auto-reply-message'),
    auto_reply_cooldown_seconds: getValue('editor-auto-reply-cooldown'),
    auto_reply_timeout_seconds: getValue('editor-auto-reply-timeout'),
    games: (shellState.editorGames || []).map((game) => ({
      app_id: Number(game.app_id),
      title: String(game.title || ''),
      enabled: game.enabled !== false,
    })),
    games_text: getValue('editor-games-text'),
    notes: profile.notes || '',
  });

  await handleMutationResult(result, {
    target: 'editor',
    successMessage: result?.message || 'Account settings saved.',
    selectProfileId: profile.profile_id,
    resetEditorDraft: true,
  });
  setButtonBusy('account-save-button', false, 'Save');
}

async function refreshRuntimeReadiness() {
  setButtonBusy('overview-refresh-button', true, 'Refreshing...');
  setRuntimeStatus('info', 'Refreshing booster state.');
  const result = await callApi('refresh_runtime_state');
  await handleMutationResult(result, {
    target: 'runtime',
    successMessage: result?.message || 'Booster state refreshed.',
  });
  setButtonBusy('overview-refresh-button', false, 'Refresh');
}

async function startAllRuntimeLanes() {
  setButtonBusy('overview-start-all-button', true, 'Starting...');
  setRuntimeStatus('info', 'Starting all ready accounts.');
  const result = await callApi('start_all_runtime');
  await handleMutationResult(result, {
    target: 'runtime',
    successMessage: result?.message || 'Start sweep finished.',
  });
  setButtonBusy('overview-start-all-button', false, 'Start All');
}

async function stopAllRuntimeLanes() {
  setButtonBusy('overview-stop-all-button', true, 'Stopping...');
  setRuntimeStatus('info', 'Stopping all active accounts.');
  const result = await callApi('stop_all_runtime');
  await handleMutationResult(result, {
    target: 'runtime',
    successMessage: result?.message || 'All accounts stopped.',
  });
  setButtonBusy('overview-stop-all-button', false, 'Stop All');
}

async function startRuntimeLane(profileId) {
  setRuntimeStatus('info', 'Starting account.');
  const result = await callApi('start_account_runtime', profileId);
  await handleMutationResult(result, {
    target: 'runtime',
    successMessage: result?.message || 'Account started.',
  });
}

async function stopRuntimeLane(profileId) {
  setRuntimeStatus('info', 'Stopping account.');
  const result = await callApi('stop_account_runtime', profileId);
  await handleMutationResult(result, {
    target: 'runtime',
    successMessage: result?.message || 'Account stopped.',
  });
}

async function setAccountBoostEnabled(profileId, boostEnabled, target = 'editor') {
  if (target === 'runtime') {
    setRuntimeStatus('info', `${boostEnabled ? 'Enabling' : 'Disabling'} booster for this account.`);
  } else {
    setEditorStatus('info', `${boostEnabled ? 'Enabling' : 'Disabling'} booster for this account.`);
  }

  const result = await callApi('set_account_boost_enabled', {
    profile_id: profileId,
    boost_enabled: boostEnabled,
  });

  await handleMutationResult(result, {
    target,
    selectProfileId: profileId,
    successMessage: result?.message || (boostEnabled ? 'Booster enabled.' : 'Booster disabled.'),
  });
}

async function openRuntimeLogFile() {
  setRuntimeStatus('info', 'Opening log file.');
  const result = await callApi('open_runtime_log_file');
  await handleMutationResult(result, {
    target: 'runtime',
    successMessage: result?.message || 'Opened log file.',
  });
}

async function focusAccountProfile(profileId) {
  shellState.selectedAccountProfileId = profileId;
  resetEditorDraftState(null);
  fillAccounts();
  fillAccountEditor();
  await selectPage('accounts', true);
  const editorCard = document.getElementById('editor-card');
  if (editorCard) {
    editorCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

function getSelectedAccount() {
  return (shellState.bootstrap.accounts || []).find(
    (account) => account.profile_id === shellState.selectedAccountProfileId,
  ) || null;
}

function getRuntimeStatusByProfileId(profileId) {
  return (shellState.bootstrap.runtime?.statuses || []).find(
    (status) => status.profile_id === profileId,
  ) || null;
}

function getSelectedRuntimeStatus() {
  return getRuntimeStatusByProfileId(shellState.selectedAccountProfileId);
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

  if (options.resetEditorDraft) {
    resetEditorDraftState(options.selectProfileId || null);
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

function setChecked(id, value) {
  const element = document.getElementById(id);
  if (element) {
    element.checked = Boolean(value);
  }
}

function getChecked(id) {
  const element = document.getElementById(id);
  return element ? Boolean(element.checked) : false;
}

function setSelectValue(id, value) {
  const element = document.getElementById(id);
  if (element) {
    element.value = value;
  }
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

function formatLoginMode(mode) {
  if (mode === 'qr') {
    return 'QR';
  }
  if (mode === 'refresh_token') {
    return 'Refresh';
  }
  return 'Password';
}

function formatRuntimeSubtitle(status) {
  const identity = status.login_mode ? formatLoginMode(status.login_mode) : 'Unknown';
  const games = formatGameIds(status.active_app_ids && status.active_app_ids.length ? status.active_app_ids : status.configured_app_ids || []);
  return `${identity} | ${games}`;
}

function formatGameIds(appIds) {
  const values = Array.isArray(appIds) ? appIds.filter((value) => Number(value) > 0) : [];
  if (!values.length) {
    return 'No games';
  }
  return values.join(', ');
}

function formatConflictPolicy(policy) {
  if (policy === 'kick') {
    return 'Force Kick';
  }
  if (policy === 'yield') {
    return 'Yield to New Session';
  }
  return 'Pause and Wait';
}

function formatOwnedGamesValidationState(state) {
  if (state === 'valid') {
    return 'OK';
  }
  if (state === 'invalid') {
    return 'Blocked';
  }
  if (state === 'unavailable') {
    return 'Unavailable';
  }
  if (state === 'skipped') {
    return 'Skipped';
  }
  return 'Pending';
}

function formatOwnedGamesValidationDetail(status) {
  if (!status) {
    return 'Library validation details will appear here.';
  }

  const message = status.owned_games_validation_message || 'No validation details are available yet.';
  const checkedAt = status.owned_games_validation_checked_at
    ? ` Checked ${formatRuntimeTimestamp(status.owned_games_validation_checked_at)}.`
    : '';
  return `${message}${checkedAt}`;
}

function formatSessionStatus(account, runtimeStatus) {
  if (!account?.has_session_bundle) {
    return 'Missing';
  }
  if (runtimeStatus?.runtime_ready) {
    return 'Ready';
  }
  if (runtimeStatus?.session_ready) {
    return 'Saved (Web)';
  }
  return 'Saved';
}

function formatGameQueueSummary(enabledCount, totalCount) {
  const enabled = Number(enabledCount || 0);
  const total = Number(totalCount || 0);
  if (!total) {
    return '0 games';
  }
  if (enabled === total) {
    return total === 1 ? '1 game' : `${total} games`;
  }
  return `${enabled} on / ${total} total`;
}

function formatRuntimeTimestamp(value) {
  if (!value) {
    return 'Not connected yet';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
