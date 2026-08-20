// ArXistant Popup Script

const DEFAULT_SERVER_URL = 'http://localhost:8765';
const SERVER_LAUNCH_URL = 'arxistant://start';
const SERVER_API_VERSION = 2;

// ── DOM Elements ──
const serverSection = document.getElementById('server-section');
const btnStartServer = document.getElementById('btn-start-server');
const btnDaily = document.getElementById('btn-daily');
const btnRecent = document.getElementById('btn-recent');
const btnML = document.getElementById('btn-ml');
const btnDB = document.getElementById('btn-db');
const linkPubs = document.getElementById('link-pubs');
const linkSearch = document.getElementById('link-search');
const linkOptions = document.getElementById('link-options');
const serverHelp = document.getElementById('server-help');
let currentPlatform = 'unknown';

// ── Initialize ──
document.addEventListener('DOMContentLoaded', async () => {
  currentPlatform = (await chrome.runtime.getPlatformInfo()).os;
  await checkServerAndUpdateUI();
  bindButtons();
});

// ── Check Server Status ──
async function checkServerAndUpdateUI() {
  const serverOnline = await isServerOnline();

  if (!serverOnline) {
    showServerOffline();
  } else {
    hideServerOffline();
  }
}

async function isServerOnline() {
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 2000);
    const response = await fetch(DEFAULT_SERVER_URL + '/api/health', {
      method: 'GET',
      signal: controller.signal
    });
    const health = response.ok ? await response.json() : null;
    clearTimeout(timeout);
    return health?.success === true && health.api_version === SERVER_API_VERSION;
  } catch (e) {
    return false;
  }
}

function showServerOffline() {
  serverSection.style.display = 'block';
  const canLaunchHelper = currentPlatform === 'mac' || currentPlatform === 'linux';
  btnStartServer.hidden = !canLaunchHelper;
  serverHelp.hidden = canLaunchHelper;
  if (!canLaunchHelper) {
    serverHelp.textContent = 'Start the ArXistant companion server, then reopen this popup.';
  }
}

function hideServerOffline() {
  serverSection.style.display = 'none';
}

// ── Start Server ──
async function startServer() {
  btnStartServer.disabled = true;
  btnStartServer.querySelector('.label').textContent = 'Starting...';

  try {
    // On Linux, use Chrome Native Messaging to start the server reliably.
    // On macOS, use the registered arxistant:// URL scheme handler.
    if (currentPlatform === 'linux') {
      await chrome.runtime.sendNativeMessage(
        'com.arxistant.server',
        { action: 'start-server' }
      );
    } else {
      // macOS: open the URL scheme handler.  Use an anchor click so the
      // popup window stays alive for the polling loop.
      const a = document.createElement('a');
      a.href = SERVER_LAUNCH_URL;
      a.target = '_blank';
      a.rel = 'noopener';
      a.style.display = 'none';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    }

    for (let attempt = 0; attempt < 20; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 500));
      if (await isServerOnline()) {
        hideServerOffline();
        return;
      }
    }

    throw new Error('Server did not become ready');
  } catch (e) {
    console.error('Failed to start server:', e);
    btnStartServer.querySelector('.label').textContent = 'Start failed — retry';
  } finally {
    btnStartServer.disabled = false;
  }
}

// ── Button Bindings ──
function bindButtons() {
  btnStartServer.addEventListener('click', startServer);
  btnDaily.addEventListener('click', () => openPage('/daily.html'));
  btnRecent.addEventListener('click', () => openPage('/recent.html'));
  btnML.addEventListener('click', () => openPage('/ml-features.html'));
  btnDB.addEventListener('click', () => openPage('/database.html'));
  linkPubs.addEventListener('click', (e) => { e.preventDefault(); openPage('/publications.html'); });
  linkSearch.addEventListener('click', (e) => { e.preventDefault(); openPage('/search-arxiv.html'); });

  linkOptions.addEventListener('click', (e) => {
    e.preventDefault();
    chrome.runtime.openOptionsPage();
  });
}

// ── Open Page ──
async function openPage(path) {
  // First check if server is online
  const online = await isServerOnline();
  if (!online) {
    showServerOffline();
    return;
  }

  try {
    const settings = await chrome.runtime.sendMessage({ action: 'getSettings' });
    const baseUrl = settings.settings?.serverUrl || DEFAULT_SERVER_URL;
    const url = baseUrl.replace(/\/[^\/]*$/, '') + path;

    // Check if already open
    const tabs = await chrome.tabs.query({ url: url + '*' });
    if (tabs.length > 0) {
      await chrome.tabs.update(tabs[0].id, { active: true });
      await chrome.windows.update(tabs[0].windowId, { focused: true });
    } else {
      await chrome.tabs.create({ url });
    }

    // Close popup
    window.close();
  } catch (e) {
    console.error('Failed to open page:', e);
    const url = DEFAULT_SERVER_URL + path;
    chrome.tabs.create({ url });
    window.close();
  }
}
