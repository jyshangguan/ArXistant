// ArXistant Popup Script

const DEFAULT_SERVER_URL = 'http://localhost:8765';
const SERVER_LAUNCH_URL = 'arxistant://start';

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

// ── Initialize ──
document.addEventListener('DOMContentLoaded', async () => {
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
    const response = await fetch(DEFAULT_SERVER_URL + '/daily.html', {
      method: 'GET',
      signal: controller.signal
    });
    clearTimeout(timeout);
    return response.ok;
  } catch (e) {
    return false;
  }
}

function showServerOffline() {
  serverSection.style.display = 'block';
}

function hideServerOffline() {
  serverSection.style.display = 'none';
}

// ── Start Server ──
async function startServer() {
  btnStartServer.disabled = true;
  btnStartServer.querySelector('.label').textContent = 'Starting...';

  try {
    // Chrome cannot spawn a process directly. The registered macOS URL handler
    // opens the bundled helper app, which runs the sanitized start_server.sh.
    window.location.href = SERVER_LAUNCH_URL;

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
