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
  popular_games: [],
  conflict_policies: [
    { value: 'pause', label: 'Pause and Wait' },
    { value: 'kick', label: 'Force Kick' },
    { value: 'yield', label: 'Yield to New Session' },
  ],
  runtime: {
    slot_ceiling: 32,
    conflict_policy: 'Per-account policy with pause, force-kick, or yield',
    reconnect_posture: 'Backoff and resume',
    transport_name: 'valvepython-steam',
    preview_mode: false,
    event_log_path: 'Loading',
    event_count: 3,
    counts: {
      tracked_accounts: 0,
      ready_accounts: 0,
      boosting_accounts: 0,
      paused_accounts: 0,
      blocked_accounts: 0,
      error_accounts: 0,
      library_validation_blocked_accounts: 0,
      library_validation_unavailable_accounts: 0,
      library_validation_verified_accounts: 0,
      active_slots: 0,
    },
    statuses: [],
    recent_events: [
      '[runtime] runtime controller initialized',
      '[runtime] persistent event log active',
      '[runtime] waiting for live runtime activity',
    ],
  },
};

const shellState = {
  currentPage: 'dashboard',
  maximized: false,
  bootstrap: fallbackState,
  authMode: 'credentials',
  credentialGuardRequirement: null,
  selectedAccountProfileId: null,
  editorGames: [],
  pendingQrLogin: null,
  qrPollTimer: null,
  runtimePollTimer: null,
};

document.addEventListener('DOMContentLoaded', async () => {
  bindNavigation();
  bindWindowControls();
  bindDragRegion();
  bindAuthModes();
  bindAccountActions();
  bindRuntimeActions();
  bindSettingsActions();
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
  shellState.currentPage = shellState.bootstrap.last_page || 'dashboard';
  shellState.maximized = Boolean(shellState.bootstrap.window?.maximized);
  reconcileSelectedProfile();

  updateWindowButtons();
  fillOverview();
  fillAccounts();
  fillRuntime();
  fillSettings();
  fillAccountEditor();
  updateTopbarPill();
  if (!shellState.credentialGuardRequirement) {
    clearCredentialGuardRequirement();
  }
  selectPage(shellState.currentPage, false);
  selectAuthMode(shellState.authMode);
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
  fillRuntime();
  fillSettings();
  fillAccountEditor();
  updateTopbarPill();
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

  const openSessionButton = document.getElementById('editor-open-session-button');
  if (openSessionButton) {
    openSessionButton.addEventListener('click', openSelectedSessionBundle);
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
    appearOnlineToggle.addEventListener('change', () => {
      updatePresenceEditorState();
    });
  }

  const autoReplyToggle = document.getElementById('editor-auto-reply-enabled');
  if (autoReplyToggle) {
    autoReplyToggle.addEventListener('change', () => {
      updateAutoReplyEditorState();
    });
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

  const openLogButton = document.getElementById('runtime-open-log-button');
  if (openLogButton) {
    openLogButton.addEventListener('click', openRuntimeLogFile);
  }

  const openLogsButton = document.getElementById('runtime-open-logs-button');
  if (openLogsButton) {
    openLogsButton.addEventListener('click', openLogsDirectoryFromRuntime);
  }

  const exportSnapshotButton = document.getElementById('runtime-export-snapshot-button');
  if (exportSnapshotButton) {
    exportSnapshotButton.addEventListener('click', exportRuntimeSnapshot);
  }
}

function bindSettingsActions() {
  const openConfigButton = document.getElementById('settings-open-config-button');
  if (openConfigButton) {
    openConfigButton.addEventListener('click', openConfigFile);
  }

  const openSessionsButton = document.getElementById('settings-open-sessions-button');
  if (openSessionsButton) {
    openSessionsButton.addEventListener('click', openSessionsDirectory);
  }

  const openLogsButton = document.getElementById('settings-open-logs-button');
  if (openLogsButton) {
    openLogsButton.addEventListener('click', openLogsDirectoryFromSettings);
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
    const runtimeStatus = getRuntimeStatusByProfileId(account.profile_id);
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
        <div><span>Boost</span><strong>${account.boost_enabled ? 'Enabled' : 'Disabled'}</strong></div>
        <div><span>Runtime</span><strong>${escapeHtml(runtimeStatus?.state_label || 'Pending')}</strong></div>
        <div><span>Presence</span><strong>${escapeHtml(account.effective_persona_state || account.persona_state || 'Online')}</strong></div>
        <div><span>Policy</span><strong>${escapeHtml(formatConflictPolicy(account.conflict_policy))}</strong></div>
        <div><span>Games</span><strong>${escapeHtml(formatGameQueueSummary(account.enabled_game_count || 0, account.game_count || 0))}</strong></div>
        <div><span>Session</span><strong>${runtimeStatus ? (runtimeStatus.session_ready ? 'Ready' : 'Missing') : (account.has_session_bundle ? 'Saved' : 'Missing')}</strong></div>
        <div><span>Library</span><strong>${escapeHtml(runtimeStatus?.owned_games_validation_label || 'Pending')}</strong></div>
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
  const runtimeStatus = getSelectedRuntimeStatus();

  hydratePersonaOptions();
  hydrateConflictPolicyOptions();
  hydratePopularGameOptions();

  if (!profile) {
    if (editorShell) {
      editorShell.classList.add('editor-shell--hidden');
    }
    if (editorEmpty) {
      editorEmpty.classList.remove('editor-empty--hidden');
    }
    setText('account-editor-title', 'Runtime-ready account settings');
    setText('account-editor-slot-pill', '0 active');
    shellState.editorGames = [];
    renderEditorGames();
    setChecked('editor-auto-reply-enabled', false);
    updateAutoReplyEditorState();
    fillEditorLibraryValidation(null);
    return;
  }

  if (editorShell) {
    editorShell.classList.remove('editor-shell--hidden');
  }
  if (editorEmpty) {
    editorEmpty.classList.add('editor-empty--hidden');
  }

  setText('account-editor-title', profile.display_name || profile.account_name || profile.steam_id);
  setText(
    'account-editor-slot-pill',
    formatGameQueueSummary(profile.enabled_game_count || 0, profile.game_count || 0),
  );

  setChecked('editor-boost-enabled', Boolean(profile.boost_enabled));
  setChecked('editor-appear-online', profile.appear_online !== false);
  setChecked('editor-auto-reply-enabled', Boolean(profile.auto_reply_enabled));
  setInputValue('editor-display-name', profile.display_name || '');
  setInputValue('editor-account-name', profile.account_name || '');
  setInputValue('editor-steam-id', profile.steam_id || '');
  setInputValue('editor-login-mode', profile.login_mode || '');
  setInputValue('editor-custom-status', profile.custom_status || '');
  setInputValue('editor-auto-reply-message', profile.auto_reply_message || '');
  setInputValue('editor-auto-reply-cooldown', String(profile.auto_reply_cooldown_seconds || 180));
  setInputValue('editor-auto-reply-timeout', String(profile.auto_reply_timeout_seconds || 1800));
  setInputValue('editor-games-text', profile.games_text || '');
  setInputValue('editor-notes', profile.notes || '');
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

  const sessionSummary = profile.session_summary || {};
  setText('editor-session-path', profile.session_bundle_path || 'No session bundle file');
  setText('editor-session-modified', sessionSummary.modified_at || 'Unknown');
  setText('editor-session-refresh', sessionSummary.has_refresh_token ? 'Available' : 'Missing');
  setText('editor-session-access', sessionSummary.has_access_token ? 'Available' : 'Missing');
  setText('editor-session-id', sessionSummary.has_session_id ? 'Available' : 'Missing');
  fillEditorLibraryValidation(runtimeStatus);
}

function fillEditorLibraryValidation(status) {
  const badge = document.getElementById('editor-library-status');
  if (badge) {
    badge.textContent = status?.owned_games_validation_label || formatOwnedGamesValidationState(status?.owned_games_validation_state);
    badge.className = `runtime-state runtime-state--${formatOwnedGamesValidationClass(status?.owned_games_validation_state)}`;
  }

  setText(
    'editor-library-checked-at',
    status?.owned_games_validation_checked_at
      ? formatRuntimeTimestamp(status.owned_games_validation_checked_at)
      : 'Not checked yet',
  );
  setText(
    'editor-library-api-key',
    status
      ? (status.owned_games_api_key_available ? 'Available' : 'Unavailable')
      : 'Unknown',
  );
  setText(
    'editor-library-missing',
    Array.isArray(status?.owned_games_missing_app_ids) && status.owned_games_missing_app_ids.length
      ? status.owned_games_missing_app_ids.join(', ')
      : 'None',
  );
  setText(
    'editor-library-message',
    formatOwnedGamesValidationDetail(status),
  );
}

function fillRuntime() {
  const runtime = shellState.bootstrap.runtime;
  setText('runtime-slot-ceiling', `${runtime.slot_ceiling} app IDs`);
  setText('runtime-conflict-policy', runtime.conflict_policy);
  setText('runtime-reconnect-posture', runtime.reconnect_posture);
  setText('runtime-ready-count', String(runtime.counts?.ready_accounts || 0));
  setText('runtime-boosting-count', String(runtime.counts?.boosting_accounts || 0));
  setText('runtime-paused-count', String(runtime.counts?.paused_accounts || 0));
  setText('runtime-blocked-count', String(runtime.counts?.blocked_accounts || 0));
  setText('runtime-validation-blocked-count', String(runtime.counts?.library_validation_blocked_accounts || 0));
  setText('runtime-validation-unavailable-count', String(runtime.counts?.library_validation_unavailable_accounts || 0));
  setText('runtime-active-slot-count', String(runtime.counts?.active_slots || 0));
  setText('runtime-validation-verified-count', String(runtime.counts?.library_validation_verified_accounts || 0));
  setText('runtime-transport-name', runtime.transport_name || 'Unknown');
  setText('runtime-accounts-pill', `${runtime.counts?.tracked_accounts || 0} tracked`);
  setText('runtime-mode-pill', runtime.preview_mode ? 'Preview transport' : 'Live transport');
  setText('runtime-log-path', runtime.event_log_path || 'Unavailable');
  setText('runtime-event-count', String(runtime.event_count || 0));
  fillRuntimeAccounts(runtime.statuses || []);

  const log = document.getElementById('runtime-log');
  if (log) {
    const lines = runtime.recent_events && runtime.recent_events.length
      ? runtime.recent_events
      : [
          '[runtime] runtime controller initialized',
          '[runtime] persistent event log active',
          '[runtime] waiting for live runtime activity',
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
          <div class="runtime-account__subtitle">${escapeHtml(status.login_mode || 'unknown')} • ${escapeHtml(status.effective_persona_state || status.persona_state || 'Online')}</div>
        </div>
        <div class="runtime-account__actions">
          <span class="runtime-state runtime-state--${escapeHtml(status.state)}">${escapeHtml(status.state_label || status.state)}</span>
          <button class="ghost-button" data-runtime-action="start" data-profile-id="${escapeHtml(status.profile_id)}" ${status.can_start ? '' : 'disabled'}>Start</button>
          <button class="ghost-button" data-runtime-action="stop" data-profile-id="${escapeHtml(status.profile_id)}" ${status.can_stop ? '' : 'disabled'}>Stop</button>
        </div>
      </div>
      <div class="runtime-account__meta">
        <div>
          <span>Boost</span>
          <strong>${status.boost_enabled ? 'Enabled' : 'Disabled'}</strong>
        </div>
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
        <div>
          <span>Auth</span>
          <strong>${escapeHtml(formatRuntimeAuthSource(status.auth_source))}</strong>
        </div>
        <div>
          <span>Policy</span>
          <strong>${escapeHtml(formatConflictPolicy(status.conflict_policy))}</strong>
        </div>
        <div>
          <span>Auto-Reply</span>
          <strong>${status.auto_reply_enabled ? 'Armed' : 'Off'}</strong>
        </div>
        <div>
          <span>Reconnects</span>
          <strong>${Number(status.reconnect_attempts || 0)}</strong>
        </div>
        <div>
          <span>Library</span>
          <strong>${escapeHtml(status.owned_games_validation_label || formatOwnedGamesValidationState(status.owned_games_validation_state))}</strong>
        </div>
      </div>
      <div class="runtime-account__message">${escapeHtml(status.message || 'No runtime message available.')}</div>
      <div class="runtime-account__details">
        <div class="runtime-account__detail"><strong>Connected:</strong> ${escapeHtml(formatRuntimeTimestamp(status.connected_at))}</div>
        <div class="runtime-account__detail"><strong>Owned games:</strong> ${escapeHtml(formatOwnedGamesValidationDetail(status))}</div>
        ${status.auto_reply_enabled ? `<div class="runtime-account__detail"><strong>Auto-reply:</strong> ${escapeHtml(formatAutoReplySummary(status))}</div>` : ''}
        ${Array.isArray(status.owned_games_missing_app_ids) && status.owned_games_missing_app_ids.length ? `<div class="runtime-account__detail"><strong>Missing app IDs:</strong> ${escapeHtml(status.owned_games_missing_app_ids.join(', '))}</div>` : ''}
        ${status.blocked_by_playing_session ? `<div class="runtime-account__detail"><strong>Blocked app:</strong> ${escapeHtml(formatBlockedApp(status.blocked_app_id))}</div>` : ''}
        ${status.last_error ? `<div class="runtime-account__detail"><strong>Last issue:</strong> ${escapeHtml(status.last_error)}</div>` : ''}
      </div>
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
  setText('settings-runtime-log-path', state.runtime?.event_log_path || 'Unavailable');
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
  const runtimeCounts = shellState.bootstrap.runtime?.counts || {};
  const boosting = runtimeCounts.boosting_accounts || 0;
  const paused = runtimeCounts.paused_accounts || 0;
  const ready = runtimeCounts.ready_accounts || 0;
  const accounts = shellState.bootstrap.counts.accounts;
  if (boosting > 0) {
    pill.textContent = `${boosting} lane${boosting === 1 ? '' : 's'} active`;
    return;
  }
  if (paused > 0) {
    pill.textContent = `${paused} lane${paused === 1 ? '' : 's'} reconnecting`;
    return;
  }
  if (ready > 0) {
    pill.textContent = `${ready} account${ready === 1 ? '' : 's'} ready`;
    return;
  }
  pill.textContent = accounts > 0 ? `${accounts} account${accounts === 1 ? '' : 's'} saved` : 'Onboarding enabled';
}

function selectAuthMode(mode) {
  shellState.authMode = mode || 'credentials';

  document.querySelectorAll('.mode-pill').forEach((button) => {
    button.classList.toggle('is-active', button.dataset.authMode === shellState.authMode);
  });

  document.querySelectorAll('.auth-panel').forEach((panel) => {
    panel.classList.toggle('is-active', panel.dataset.authPanel === shellState.authMode);
  });

  if (shellState.authMode !== 'credentials') {
    clearCredentialGuardRequirement();
  }
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
  if (result?.status === 'steam_guard_required') {
    shellState.credentialGuardRequirement = result;
    applyCredentialGuardRequirement(result);
    setAuthStatus('info', result.message || 'Steam Guard code required.');
    setButtonBusy('credentials-submit-button', false, 'Sign In With Credentials');
    const button = document.getElementById('credentials-submit-button');
    if (button) {
      button.textContent = 'Submit Steam Guard Code';
      button.dataset.defaultLabel = 'Submit Steam Guard Code';
    }
    return;
  }
  clearCredentialGuardRequirement();
  await handleMutationResult(result, {
    successMessage: result?.message || 'Credential login completed.',
    clearIds: ['cred-password', 'cred-guard-code'],
    selectProfileId: result?.account?.profile_id,
  });
  setButtonBusy('credentials-submit-button', false, 'Sign In With Credentials');
}

async function loginWithRefreshToken() {
  clearCredentialGuardRequirement();
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
  clearCredentialGuardRequirement();
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

function applyCredentialGuardRequirement(requirement) {
  shellState.credentialGuardRequirement = requirement || null;
  setText('cred-guard-code-label', requirement?.code_label || 'Steam Guard Code');
  setText(
    'cred-guard-helper',
    requirement?.associated_message
      ? `${requirement.message} Steam reported destination: ${requirement.associated_message}.`
      : (requirement?.message || 'If Steam Guard is required, enter the code here and submit again.'),
  );

  const input = document.getElementById('cred-guard-code');
  if (input) {
    input.placeholder = requirement?.code_placeholder || 'Email or app code if required';
    input.focus();
    input.select();
  }
}

function clearCredentialGuardRequirement() {
  shellState.credentialGuardRequirement = null;
  setText('cred-guard-code-label', 'Steam Guard Code');
  setText('cred-guard-helper', 'If Steam Guard is required, enter the code here and submit again.');
  const input = document.getElementById('cred-guard-code');
  if (input) {
    input.placeholder = 'Email or app code if required';
  }
  const button = document.getElementById('credentials-submit-button');
  if (button && !button.disabled) {
    button.textContent = 'Sign In With Credentials';
    button.dataset.defaultLabel = 'Sign In With Credentials';
  }
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
    } else if (options.target === 'settings') {
      setSettingsStatus('error', result?.message || 'The request failed.');
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
  } else if (options.target === 'settings') {
    setSettingsStatus('success', options.successMessage || result.message || 'Saved.');
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

function setSettingsStatus(kind, message) {
  const banner = document.getElementById('settings-status');
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

function hydrateConflictPolicyOptions() {
  const select = document.getElementById('editor-conflict-policy');
  if (!select) {
    return;
  }
  const options = shellState.bootstrap.conflict_policies || fallbackState.conflict_policies;
  select.innerHTML = '';
  options.forEach((policy) => {
    const option = document.createElement('option');
    option.value = policy.value;
    option.textContent = policy.label;
    select.appendChild(option);
  });
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
  placeholder.textContent = 'Select a popular game preset';
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
          <span class="pill pill--subtle">${game.enabled === false ? 'Disabled' : 'Armed'}</span>
        </div>
        <small>${escapeHtml(`App ${game.app_id} • Queue ${index + 1}`)}</small>
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
      renderEditorGames();
      setEditorStatus('success', `Re-enabled app ${numericAppId} in the boost queue.`);
      return;
    }
    if (
      existingGame.title !== nextGames[existingIndex].title
      || existingGame.enabled !== nextGames[existingIndex].enabled
    ) {
      nextGames[existingIndex] = existingGame;
      shellState.editorGames = nextGames;
      renderEditorGames();
    }
    setEditorStatus('info', `App ${numericAppId} is already in this account queue.`);
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
  renderEditorGames();
  setEditorStatus('success', `Added app ${numericAppId} to the boost queue.`);
}

function addPresetGameToEditor() {
  const select = document.getElementById('editor-preset-game');
  if (!select?.value) {
    setEditorStatus('error', 'Select a preset game first.');
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
  renderEditorGames();
  setEditorStatus('info', `Removed app ${appId} from the boost queue.`);
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
  renderEditorGames();
  setEditorStatus(
    'info',
    nextGame.enabled === false
      ? `Disabled app ${appId} without removing it from the queue.`
      : `Re-enabled app ${appId} in the queue.`,
  );
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
  renderEditorGames();
  setEditorStatus('info', `Moved app ${appId} to queue position ${nextIndex + 1}.`);
}

function findEditorGameIndex(appId) {
  return (shellState.editorGames || []).findIndex((game) => Number(game.app_id) === Number(appId));
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
    custom_status: getValue('editor-custom-status'),
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
    notes: getValue('editor-notes'),
  });
  await handleMutationResult(result, {
    target: 'editor',
    successMessage: result?.message || 'Account settings saved.',
    selectProfileId: profile.profile_id,
  });
  setButtonBusy('account-save-button', false, 'Save Account Settings');
}

async function openSelectedSessionBundle() {
  const profile = getSelectedAccount();
  if (!profile) {
    setEditorStatus('error', 'Select an account first.');
    return;
  }

  setEditorStatus('info', 'Opening the saved session bundle.');
  const result = await callApi('open_account_session_bundle', profile.profile_id);
  await handleMutationResult(result, {
    target: 'editor',
    successMessage: result?.message || 'Opened the session bundle.',
    selectProfileId: profile.profile_id,
  });
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

async function openRuntimeLogFile() {
  setRuntimeStatus('info', 'Opening the runtime log file.');
  const result = await callApi('open_runtime_log_file');
  await handleMutationResult(result, {
    target: 'runtime',
    successMessage: result?.message || 'Opened the runtime log file.',
  });
}

async function openLogsDirectoryFromRuntime() {
  setRuntimeStatus('info', 'Opening the logs directory.');
  const result = await callApi('open_logs_directory');
  await handleMutationResult(result, {
    target: 'runtime',
    successMessage: result?.message || 'Opened the logs directory.',
  });
}

async function exportRuntimeSnapshot() {
  setButtonBusy('runtime-export-snapshot-button', true, 'Exporting...');
  setRuntimeStatus('info', 'Exporting the current runtime snapshot.');
  const result = await callApi('export_runtime_snapshot');
  await handleMutationResult(result, {
    target: 'runtime',
    successMessage: result?.path ? `Exported runtime snapshot to ${result.path}` : (result?.message || 'Exported the runtime snapshot.'),
  });
  setButtonBusy('runtime-export-snapshot-button', false, 'Export Snapshot');
}

async function openConfigFile() {
  setSettingsStatus('info', 'Opening the config file.');
  const result = await callApi('open_config_file');
  await handleMutationResult(result, {
    target: 'settings',
    successMessage: result?.message || 'Opened the config file.',
  });
}

async function openSessionsDirectory() {
  setSettingsStatus('info', 'Opening the sessions directory.');
  const result = await callApi('open_sessions_directory');
  await handleMutationResult(result, {
    target: 'settings',
    successMessage: result?.message || 'Opened the sessions directory.',
  });
}

async function openLogsDirectoryFromSettings() {
  setSettingsStatus('info', 'Opening the logs directory.');
  const result = await callApi('open_logs_directory');
  await handleMutationResult(result, {
    target: 'settings',
    successMessage: result?.message || 'Opened the logs directory.',
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

function formatRuntimeAuthSource(source) {
  if (!source) {
    return 'Pending';
  }
  if (source === 'refresh_token') {
    return 'Refresh token';
  }
  if (source === 'login_key') {
    return 'Login key';
  }
  return source.replace(/_/g, ' ');
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

function formatAutoReplySummary(status) {
  const sentCount = Number(status.auto_reply_sent_count || 0);
  const cooldown = Number(status.auto_reply_cooldown_seconds || 0);
  const timeout = Number(status.auto_reply_timeout_seconds || 0);
  const lastSender = status.auto_reply_last_sender ? ` Last reply: ${status.auto_reply_last_sender}.` : '';
  const lastSent = status.auto_reply_last_sent_at ? ` Sent ${formatRuntimeTimestamp(status.auto_reply_last_sent_at)}.` : '';
  return `${sentCount} sent, ${cooldown}s cooldown, ${timeout}s timeout.${lastSender}${lastSent}`;
}

function formatBlockedApp(appId) {
  const numericId = Number(appId || 0);
  if (!numericId) {
    return 'Another active session';
  }
  return `App ${numericId}`;
}

function formatOwnedGamesValidationState(state) {
  if (state === 'valid') {
    return 'Verified';
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

function formatOwnedGamesValidationClass(state) {
  if (state === 'valid') {
    return 'ready';
  }
  if (state === 'invalid') {
    return 'error';
  }
  if (state === 'unavailable') {
    return 'paused';
  }
  if (state === 'skipped') {
    return 'idle';
  }
  return 'starting';
}

function formatOwnedGamesValidationDetail(status) {
  if (!status) {
    return 'Validation will appear after the runtime controller syncs this account.';
  }

  const message = status.owned_games_validation_message || 'No validation details are available yet.';
  const checkedAt = status.owned_games_validation_checked_at
    ? ` Checked ${formatRuntimeTimestamp(status.owned_games_validation_checked_at)}.`
    : '';
  return `${formatOwnedGamesValidationState(status.owned_games_validation_state)}. ${message}${checkedAt}`;
}

function formatGameQueueSummary(enabledCount, totalCount) {
  const enabled = Number(enabledCount || 0);
  const total = Number(totalCount || 0);
  if (!total) {
    return '0 active';
  }
  if (enabled === total) {
    return `${enabled} active`;
  }
  return `${enabled} active / ${total} total`;
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
