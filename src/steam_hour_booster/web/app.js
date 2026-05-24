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
};

document.addEventListener('DOMContentLoaded', async () => {
  bindNavigation();
  bindWindowControls();
  bindDragRegion();
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
  selectPage(shellState.currentPage, false);
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
        No accounts are configured yet. The onboarding flow for credentials, QR approval,
        refresh-token reuse, and Steam Guard will populate this page in later patches.
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
        <span class="pill pill--subtle">${escapeHtml(account.login_mode)}</span>
      </div>
      <div class="account-item__meta">
        <div><span>Persona</span><strong>${escapeHtml(account.persona_state)}</strong></div>
        <div><span>Games</span><strong>${account.game_count}</strong></div>
        <div><span>Custom status</span><strong>${escapeHtml(account.custom_status || 'None')}</strong></div>
      </div>
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

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) {
    element.textContent = value;
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

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
