#!/usr/bin/env node
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

const SteamUser = require('steam-user');

const DEFAULT_TIMEOUT_MS = 75000;
const DEFAULT_MACHINE_NAME = 'Steam Hour Booster';

function log(message) {
  const timestamp = new Date().toISOString().slice(11, 19);
  process.stderr.write(`[${timestamp}] [steam-client-bridge] ${message}\n`);
}

function writeJson(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}

function readStdin() {
  return new Promise((resolve, reject) => {
    let raw = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (chunk) => {
      raw += chunk;
    });
    process.stdin.on('end', () => {
      try {
        resolve(raw.trim() ? JSON.parse(raw) : {});
      } catch (error) {
        reject(new Error(`Invalid JSON input: ${error.message}`));
      }
    });
    process.stdin.on('error', reject);
  });
}

function asString(value) {
  return value === undefined || value === null ? '' : String(value).trim();
}

function parsePositiveNumber(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function normalizeDataDirectory(value) {
  const requested = asString(value);
  if (requested) {
    fs.mkdirSync(requested, {recursive: true});
    return requested;
  }

  const fallback = path.join(os.tmpdir(), 'steam-hour-booster-client-bridge');
  fs.mkdirSync(fallback, {recursive: true});
  return fallback;
}

function eresultName(value) {
  if (value === undefined || value === null) {
    return '';
  }
  return SteamUser.EResult[value] || String(value);
}

function decodeJwt(token) {
  const parts = asString(token).split('.');
  if (parts.length < 2) {
    return {};
  }

  const padded = parts[1].padEnd(parts[1].length + ((4 - (parts[1].length % 4)) % 4), '=');
  try {
    return JSON.parse(Buffer.from(padded, 'base64url').toString('utf8'));
  } catch (error) {
    return {};
  }
}

function errorPayload(error) {
  const resultName = eresultName(error && error.eresult);
  const message = resultName || (error && error.message) || 'Steam client authorization failed.';
  return {
    status: 'error',
    message: `Steam client authorization failed: ${message}.`,
    error_type: error && error.name ? error.name : 'SteamClientBridgeError',
    eresult: error && error.eresult ? error.eresult : 0,
    eresult_name: resultName,
  };
}

function authorize(payload) {
  return new Promise((resolve) => {
    const accountName = asString(payload.accountName || payload.account_name);
    const password = asString(payload.password);
    const requestedSteamId = asString(payload.steamId || payload.steam_id);
    const guardCode = asString(payload.steamGuardCode || payload.steam_guard_code);
    const dataDirectory = normalizeDataDirectory(payload.dataDirectory || payload.data_directory);
    const machineName = asString(payload.machineName || payload.machine_name) || DEFAULT_MACHINE_NAME;
    const timeoutMs = parsePositiveNumber(payload.timeoutMs || payload.timeout_ms, DEFAULT_TIMEOUT_MS);

    if (!accountName) {
      resolve({status: 'error', message: 'Steam account username is required.'});
      return;
    }
    if (!password) {
      resolve({status: 'error', message: 'Steam account password is required.'});
      return;
    }

    const user = new SteamUser({
      autoRelogin: false,
      dataDirectory,
      renewRefreshTokens: false,
    });

    let settled = false;
    let suppliedGuardCode = false;
    let refreshToken = '';
    let loggedOnDetails = null;

    function finish(result) {
      if (settled) {
        return;
      }

      settled = true;
      clearTimeout(timeout);
      try {
        if (user.steamID) {
          user.logOff();
        } else {
          user.removeAllListeners();
        }
      } catch (error) {
        log(`Cleanup warning: ${error.message}`);
      }
      resolve(result);
    }

    function successResult() {
      const steamId = user.steamID ? user.steamID.getSteamID64() : requestedSteamId;
      const claims = decodeJwt(refreshToken);
      return {
        status: 'success',
        account_name: accountName,
        steam_id: steamId,
        refresh_token: refreshToken,
        token_audiences: Array.isArray(claims.aud) ? claims.aud : [],
        token_subject: asString(claims.sub),
        token_expires_at: claims.exp ? Number(claims.exp) : 0,
        logged_on: true,
        logon_details_eresult: loggedOnDetails && loggedOnDetails.eresult ? loggedOnDetails.eresult : 0,
        source: 'steam-user',
      };
    }

    const timeout = setTimeout(() => {
      log(`Authorization timed out after ${Math.round(timeoutMs / 1000)}s.`);
      finish({
        status: 'error',
        message: 'Steam client authorization timed out while waiting for Steam.',
        error_type: 'TimeoutError',
      });
    }, timeoutMs);

    user.on('debug', (message) => {
      log(`debug: ${message}`);
    });

    user.on('refreshToken', (token) => {
      refreshToken = asString(token);
      const claims = decodeJwt(refreshToken);
      const audiences = Array.isArray(claims.aud) ? claims.aud.join(',') : '';
      log(`Received Steam client refresh token for steam_id=${asString(claims.sub) || 'unknown'} audiences=${audiences || 'unknown'}.`);
    });

    user.on('machineAuthToken', () => {
      log('Received and stored a Steam Guard machine auth token for future email-guard logins.');
    });

    user.on('steamGuard', (domain, callback, lastCodeWrong) => {
      const codeKind = domain ? 'email' : 'app';
      if (guardCode && !suppliedGuardCode) {
        suppliedGuardCode = true;
        log(`Steam requested a ${codeKind} Guard code; submitting the code supplied by the desktop UI.`);
        callback(guardCode);
        return;
      }

      log(`Steam requested a ${codeKind} Guard code and no code is available yet.`);
      finish({
        status: 'steam_guard_required',
        code_kind: codeKind,
        associated_message: domain || '',
        last_code_wrong: Boolean(lastCodeWrong),
        message: domain
          ? `A Steam Guard email code is required for ${domain}.`
          : 'A Steam Guard app code is required.',
      });
    });

    user.on('loggedOn', (details) => {
      loggedOnDetails = details || {};
      log(`Steam client login succeeded for steam_id=${user.steamID ? user.steamID.getSteamID64() : 'unknown'}.`);
      if (refreshToken) {
        finish(successResult());
        return;
      }

      setTimeout(() => {
        if (refreshToken) {
          finish(successResult());
          return;
        }
        finish({
          status: 'error',
          message: 'Steam client login succeeded, but no refresh token was emitted.',
          error_type: 'MissingRefreshTokenError',
        });
      }, 1500);
    });

    user.on('error', (error) => {
      log(`Steam client error: ${(error && error.message) || 'Unknown error'}${error && error.eresult ? ` (${eresultName(error.eresult)})` : ''}.`);
      finish(errorPayload(error || new Error('Unknown Steam client error.')));
    });

    user.on('disconnected', (eresult, message) => {
      log(`Steam client disconnected: ${eresultName(eresult) || eresult || 0}${message ? ` ${message}` : ''}.`);
    });

    log(`Starting modern Steam client login for account=${accountName} dataDirectory=${dataDirectory}.`);
    try {
      user.logOn({
        accountName,
        password,
        machineName,
      });
    } catch (error) {
      finish(errorPayload(error));
    }
  });
}

async function main() {
  if (process.argv.includes('--self-test')) {
    writeJson({
      status: 'ok',
      steam_user_loaded: Boolean(SteamUser),
      eresult_ok: SteamUser.EResult.OK,
    });
    return;
  }

  const command = process.argv[2] || 'authorize';
  const payload = await readStdin();

  if (command !== 'authorize') {
    writeJson({status: 'error', message: `Unsupported bridge command: ${command}`});
    process.exitCode = 2;
    return;
  }

  const result = await authorize(payload);
  writeJson(result);
}

main().catch((error) => {
  writeJson({
    status: 'error',
    message: error && error.message ? error.message : 'Steam client bridge failed.',
    error_type: error && error.name ? error.name : 'SteamClientBridgeError',
  });
  process.exitCode = 1;
});
