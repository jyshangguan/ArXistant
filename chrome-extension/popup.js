// ArXistant Popup Script

const DEFAULT_SERVER_URL = 'http://localhost:8765';
const SERVER_LAUNCH_URL = 'arxistant://start';
const SERVER_API_VERSION = 2;

// ── DOM Elements ──
const serverSection = document.getElementById('server-section');
const btnDaily = document.getElementById('btn-daily');
const btnSearch = document.getElementById('btn-search');
const btnChat = document.getElementById('btn-chat');
const btnRecent = document.getElementById('btn-recent');
const btnML = document.getElementById('btn-ml');
const btnDB = document.getElementById('btn-db');
const btnPubs = document.getElementById('btn-pubs');
const linkOptions = document.getElementById('link-options');
const linkPower = document.getElementById('link-power');
const serverHelp = document.getElementById('server-help');
let currentPlatform = 'unknown';
let serverBusy = false;

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
  setPowerButton(serverOnline);
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

function setPowerButton(online, busyText) {
  linkPower.textContent = busyText || (online ? '⏻ Stop Server' : '⏻ Start Server');
}

function showServerOffline() {
  serverSection.style.display = 'block';
  if (currentPlatform === 'linux') {
    serverHelp.hidden = false;
    serverHelp.textContent = 'Use the footer button below, or run: systemctl --user restart arxistant.service';
  } else if (currentPlatform !== 'mac') {
    serverHelp.hidden = false;
    serverHelp.textContent = 'Start the ArXistant companion server, then reopen this popup.';
  } else {
    serverHelp.hidden = true;
  }
}

function hideServerOffline() {
  serverSection.style.display = 'none';
}

// ── Start / Stop Server ──
async function startServer() {
  setPowerButton(false, '⏻ Starting…');
  try {
    // On Linux, use Chrome Native Messaging to start the server reliably.
    // On macOS, use the registered arxistant:// URL scheme handler.
    if (currentPlatform === 'linux') {
      await chrome.runtime.sendNativeMessage(
        'com.arxistant.server',
        { action: 'start-server' }
      );
    } else {
      // macOS: open the URL scheme handler. Use an anchor click so the popup
      // window stays alive for the polling loop.
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
        setPowerButton(true);
        return;
      }
    }

    throw new Error('Server did not become ready');
  } catch (e) {
    console.error('Failed to start server:', e);
    setPowerButton(false, '⏻ Start failed — retry');
  }
}

async function stopServer() {
  if (!confirm('Stop the ArXistant server?')) return;
  setPowerButton(true, '⏻ Stopping…');
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 3000);
    await fetch(DEFAULT_SERVER_URL + '/api/shutdown', {
      method: 'POST',
      signal: controller.signal
    });
    clearTimeout(timeout);
  } catch (e) {
    // The server may already be offline or fail to respond; fall through and
    // re-check its status below.
  }

  for (let attempt = 0; attempt < 12; attempt++) {
    await new Promise(resolve => setTimeout(resolve, 400));
    if (!(await isServerOnline())) break;
  }
  await checkServerAndUpdateUI();
}

async function toggleServer() {
  if (serverBusy) return;
  serverBusy = true;
  try {
    if (await isServerOnline()) {
      await stopServer();
    } else {
      await startServer();
    }
  } finally {
    serverBusy = false;
  }
}

// ── Button Bindings ──
function bindButtons() {
  btnDaily.addEventListener('click', () => openPage('/daily.html'));
  btnSearch.addEventListener('click', () => openPage('/search-arxiv.html'));
  btnChat.addEventListener('click', () => openPage('/chat.html'));
  btnRecent.addEventListener('click', () => openPage('/recent.html'));
  btnML.addEventListener('click', () => openPage('/ml-features.html'));
  btnDB.addEventListener('click', () => openPage('/database.html'));
  btnPubs.addEventListener('click', () => openPage('/publications.html'));

  linkOptions.addEventListener('click', (e) => {
    e.preventDefault();
    chrome.runtime.openOptionsPage();
  });

  linkPower.addEventListener('click', (e) => {
    e.preventDefault();
    toggleServer();
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
