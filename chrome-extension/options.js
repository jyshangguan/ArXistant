// ArXistant Options Page Script

const DEFAULT_SETTINGS = {
  serverUrl: 'http://localhost:8765/daily.html',
  reminderTimes: ['10:30'],
  skipWeekends: true,
  retrainAfterChanges: 5,
  panelOnArxiv: true,
  panelOnScix: true
};

const serverUrlInput = document.getElementById('server-url');
const reminderTimesList = document.getElementById('reminder-times');
const skipWeekendsInput = document.getElementById('skip-weekends');
const panelArxivInput = document.getElementById('panel-arxiv');
const panelScixInput = document.getElementById('panel-scix');
const btnAddTime = document.getElementById('btn-add-time');
const btnSave = document.getElementById('btn-save');
const btnReset = document.getElementById('btn-reset');
const saveStatus = document.getElementById('save-status');
const btnTestNotify = document.getElementById('btn-test-notify');
const testStatus = document.getElementById('test-status');
const alarmStatus = document.getElementById('alarm-status');
const retrainAfterChangesInput = document.getElementById('retrain-after-changes');
const retrainingStatus = document.getElementById('retraining-status');
const cloudProviderInput = document.getElementById('cloud-provider');
const cloudFolderInput = document.getElementById('cloud-folder');
const cloudEnabledInput = document.getElementById('cloud-enabled');
const btnCloudConnect = document.getElementById('btn-cloud-connect');
const btnCloudDisconnect = document.getElementById('btn-cloud-disconnect');
const cloudStatus = document.getElementById('cloud-status');
const cloudLocalGroup = document.getElementById('cloud-local-group');
const cloudWebdavGroup = document.getElementById('cloud-webdav-group');
const webdavUrlInput = document.getElementById('webdav-url');
const webdavUsernameInput = document.getElementById('webdav-username');
const webdavPasswordInput = document.getElementById('webdav-password');
const llmPresetInput = document.getElementById('llm-preset');
const llmBaseUrlInput = document.getElementById('llm-base-url');
const llmModelInput = document.getElementById('llm-model');
const llmApiKeyInput = document.getElementById('llm-api-key');
const btnLlmSave = document.getElementById('btn-llm-save');
const btnLlmTest = document.getElementById('btn-llm-test');
const llmStatus = document.getElementById('llm-status');
const adsTokenInput = document.getElementById('ads-token');
const btnAdsSave = document.getElementById('btn-ads-save');
const btnAdsTest = document.getElementById('btn-ads-test');
const btnAdsClear = document.getElementById('btn-ads-clear');
const adsStatus = document.getElementById('ads-status');
const ttsVoiceInput = document.getElementById('tts-voice');
const ttsPapersInput = document.getElementById('tts-papers');
const ttsRateInput = document.getElementById('tts-rate');
const btnTtsSave = document.getElementById('btn-tts-save');
const btnTtsTest = document.getElementById('btn-tts-test');
const ttsStatus = document.getElementById('tts-status');

const LLM_PRESETS = {
  openai:     { baseUrl: 'https://api.openai.com/v1',            model: 'gpt-4o-mini' },
  deepseek:   { baseUrl: 'https://api.deepseek.com/v1',          model: 'deepseek-chat' },
  openrouter: { baseUrl: 'https://openrouter.ai/api/v1',         model: 'openai/gpt-4o-mini' },
  moonshot:   { baseUrl: 'https://api.moonshot.cn/v1',           model: 'moonshot-v1-8k' },
  zhipu:      { baseUrl: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4-flash' },
  ollama:     { baseUrl: 'http://localhost:11434/v1',            model: 'llama3.1' }
};

let currentPlatform = 'unknown';

document.addEventListener('DOMContentLoaded', async () => {
  bindEvents();
  try {
    currentPlatform = (await chrome.runtime.getPlatformInfo()).os;
  } catch (e) {
    currentPlatform = 'unknown';
  }
  // The notification troubleshooting box is macOS-specific; only show it there.
  if (currentPlatform === 'mac') {
    const macHelp = document.getElementById('macos-notify-help');
    if (macHelp) macHelp.style.display = 'block';
  }
  await loadSettings();
  await loadCloudStatus();
  await loadLlmConfig();
  await loadAdsToken();
  await loadTtsConfig();
});

// Folded sections: reveal the one a validation error points at, so the user
// can fix the field without hunting for the right title to click.
function expandSection(id) {
  const section = document.getElementById(id);
  if (section instanceof HTMLDetailsElement) section.open = true;
}

function addTimeRow(time = '10:30') {
  const row = document.createElement('div');
  row.className = 'reminder-time-row';
  row.innerHTML = `
    <input type="time" class="reminder-time" value="${time}" required>
    <button type="button" class="btn-remove-time" aria-label="Remove reminder">Remove</button>
  `;
  row.querySelector('.btn-remove-time').addEventListener('click', () => {
    row.remove();
    if (!reminderTimesList.children.length) addTimeRow();
  });
  reminderTimesList.appendChild(row);
}

function renderTimes(times) {
  reminderTimesList.replaceChildren();
  (times.length ? times : DEFAULT_SETTINGS.reminderTimes).forEach(addTimeRow);
}

function collectTimes() {
  return [...new Set(
    [...document.querySelectorAll('.reminder-time')].map(input => input.value).filter(Boolean)
  )].sort();
}

async function loadSettings() {
  try {
    const response = await chrome.runtime.sendMessage({ action: 'getSettings' });
    const settings = response.settings || DEFAULT_SETTINGS;
    serverUrlInput.value = settings.serverUrl || DEFAULT_SETTINGS.serverUrl;
    renderTimes(settings.reminderTimes || DEFAULT_SETTINGS.reminderTimes);
    skipWeekendsInput.checked = settings.skipWeekends !== false;
    retrainAfterChangesInput.value = settings.retrainAfterChanges || DEFAULT_SETTINGS.retrainAfterChanges;
    // Default on: an absent flag means the setting predates the toggle.
    panelArxivInput.checked = settings.panelOnArxiv !== false;
    panelScixInput.checked = settings.panelOnScix !== false;
    await updateAlarmStatus();
    await updateRetrainingStatus();
  } catch (error) {
    console.error('Failed to load settings:', error);
    renderTimes(DEFAULT_SETTINGS.reminderTimes);
    showStatus('Could not load settings.', 'error');
  }
}

async function saveSettings() {
  const serverUrl = serverUrlInput.value.trim();
  const reminderTimes = collectTimes();
  const skipWeekends = skipWeekendsInput.checked;
  const retrainAfterChanges = Number.parseInt(retrainAfterChangesInput.value, 10);
  const panelOnArxiv = panelArxivInput.checked;
  const panelOnScix = panelScixInput.checked;
  if (!serverUrl) {
    expandSection('section-server');
    return showStatus('Server URL cannot be empty.', 'error');
  }
  if (!reminderTimes.length) {
    expandSection('section-reminders');
    return showStatus('Add at least one reminder time.', 'error');
  }
  if (!Number.isInteger(retrainAfterChanges) || retrainAfterChanges < 1 || retrainAfterChanges > 100) {
    expandSection('section-retraining');
    return showStatus('Retraining threshold must be between 1 and 100.', 'error');
  }

  try {
    const response = await chrome.runtime.sendMessage({
      action: 'saveSettings',
      settings: {
        serverUrl, reminderTimes, skipWeekends, retrainAfterChanges,
        panelOnArxiv, panelOnScix
      }
    });
    if (!response.success) throw new Error(response.error || 'Failed to save settings');
    renderTimes(response.settings.reminderTimes);
    showStatus(response.retrainingSync?.success === false
      ? `Settings saved locally, but the ML server is unavailable: ${response.retrainingSync.error}`
      : 'Settings saved, reminders rescheduled, and ML threshold updated.',
      response.retrainingSync?.success === false ? 'error' : 'success');
    await updateAlarmStatus();
    await updateRetrainingStatus();
  } catch (error) {
    showStatus(`Error saving settings: ${error.message}`, 'error');
  }
}

async function resetSettings() {
  serverUrlInput.value = DEFAULT_SETTINGS.serverUrl;
  renderTimes(DEFAULT_SETTINGS.reminderTimes);
  skipWeekendsInput.checked = DEFAULT_SETTINGS.skipWeekends;
  retrainAfterChangesInput.value = DEFAULT_SETTINGS.retrainAfterChanges;
  panelArxivInput.checked = DEFAULT_SETTINGS.panelOnArxiv;
  panelScixInput.checked = DEFAULT_SETTINGS.panelOnScix;
  await saveSettings();
}

function formatRetrainingState(state) {
  const progress = `${state.changes_since_training} / ${state.retrain_after_changes} saved-set changes`;
  const activity = state.training ? 'Training now…' : 'Idle';
  const trained = state.last_trained_at
    ? `Last trained: ${new Date(state.last_trained_at).toLocaleString()}`
    : 'Last trained: not recorded';
  const error = state.last_error ? `\nLast error: ${state.last_error}` : '';
  return `${activity}\n${progress}\n${trained}${error}`;
}

async function updateRetrainingStatus() {
  retrainingStatus.textContent = 'Loading ML retraining status…';
  try {
    const response = await chrome.runtime.sendMessage({ action: 'getMLRetrainingStatus' });
    if (!response.success) throw new Error(response.error || 'Status unavailable');
    retrainingStatus.textContent = formatRetrainingState(response.state);
  } catch (error) {
    retrainingStatus.textContent = `ML server unavailable: ${error.message}`;
  }
}

async function updateAlarmStatus() {
  const response = await chrome.runtime.sendMessage({ action: 'getReminderStatus' });
  if (!response.alarms?.length) {
    alarmStatus.textContent = 'No reminders are currently scheduled.';
    return;
  }
  const scheduled = response.alarms
    .sort((a, b) => a.scheduledTime - b.scheduledTime)
    .map(alarm => `${alarm.time} → ${new Date(alarm.scheduledTime).toLocaleString()}`)
    .join('\n');
  alarmStatus.textContent = `Notification permission: ${response.permissionLevel}\n${scheduled}`;
}

async function testNotification() {
  testStatus.textContent = 'Sending test notification...';
  try {
    const response = await chrome.runtime.sendMessage({ action: 'testNotification' });
    if (!response.success) throw new Error(response.error || 'Unknown error');
    if (response.acceptedByChrome) {
      testStatus.textContent = currentPlatform === 'mac'
        ? 'Chrome accepted and retained the notification. If no banner appeared, check macOS banner style and Focus mode below.'
        : 'Chrome accepted and retained the notification. If no banner appeared, check your desktop notification settings (and Do Not Disturb).';
      testStatus.style.color = '#e65100';
    } else {
      testStatus.textContent = 'Chrome accepted the request but did not retain the notification. Reload the extension and inspect its service worker console.';
      testStatus.style.color = '#c62828';
    }
  } catch (error) {
    testStatus.textContent = `✗ ${error.message}`;
    testStatus.style.color = '#c62828';
  }
}

function showStatus(message, type) {
  saveStatus.textContent = message;
  saveStatus.className = `save-status ${type}`;
}

function bindEvents() {
  btnAddTime.addEventListener('click', () => addTimeRow());
  btnSave.addEventListener('click', saveSettings);
  btnReset.addEventListener('click', resetSettings);
  btnTestNotify.addEventListener('click', testNotification);
  btnCloudConnect.addEventListener('click', connectCloud);
  btnCloudDisconnect.addEventListener('click', disconnectCloud);
  cloudProviderInput.addEventListener('change', updateCloudProviderFields);
  cloudEnabledInput.addEventListener('change', onCloudEnabledChange);
  llmPresetInput.addEventListener('change', applyLlmPreset);
  btnLlmSave.addEventListener('click', saveLlmConfig);
  btnLlmTest.addEventListener('click', testLlmConnection);
  btnAdsSave.addEventListener('click', saveAdsToken);
  btnAdsTest.addEventListener('click', testAdsToken);
  btnAdsClear.addEventListener('click', clearAdsToken);
  btnTtsSave.addEventListener('click', saveTtsConfig);
  btnTtsTest.addEventListener('click', testVoice);
  // Voice and rate are discrete selects — save them the moment they change
  // (a separate Save click is easy to miss after using Test Voice, which
  // left the change unapplied on the Daily page).
  ttsVoiceInput.addEventListener('change', saveTtsConfig);
  ttsRateInput.addEventListener('change', saveTtsConfig);
}

// ── LLM (Chat) ──
// The credentials live on the ArXistant server (same store the Chat page's
// status reads); the background worker relays them to /api/chat/config.

function applyLlmPreset() {
  const preset = LLM_PRESETS[llmPresetInput.value];
  if (!preset) return;
  if (preset.baseUrl) llmBaseUrlInput.value = preset.baseUrl;
  if (preset.model) llmModelInput.value = preset.model;
}

function updateLlmStatus(cfg) {
  const missing = [];
  if (!cfg.base_url) missing.push('base URL');
  if (!cfg.model) missing.push('model');
  if (!cfg.has_api_key) missing.push('API key');
  if (missing.length === 0) {
    llmStatus.textContent = '✅ Ready — ' + cfg.model +
      (cfg.key_storage === 'file' ? ' · key in local file'
        : cfg.key_storage === 'keychain' ? ' · key from OS keychain' : '') +
      ' (use Test Connection to verify)';
    llmStatus.className = 'hint ok';
  } else {
    llmStatus.textContent = '⚠️ Missing: ' + missing.join(', ');
    llmStatus.className = 'hint warn';
  }
}

async function loadLlmConfig() {
  llmStatus.textContent = 'Loading LLM settings…';
  llmStatus.className = 'hint';
  try {
    const response = await chrome.runtime.sendMessage({ action: 'getChatConfig' });
    if (!response.success) throw new Error(response.error || 'Failed to load LLM settings');
    const cfg = response.config || {};
    llmBaseUrlInput.value = cfg.base_url || '';
    llmModelInput.value = cfg.model || '';
    llmApiKeyInput.value = '';
    llmApiKeyInput.placeholder = cfg.has_api_key ? 'API key saved — leave blank to keep' : 'API key';
    updateLlmStatus(cfg);
  } catch (error) {
    llmStatus.textContent = '⚠️ Could not load LLM settings: ' + error.message;
    llmStatus.className = 'hint warn';
  }
}

async function saveLlmConfig() {
  const payload = {
    base_url: llmBaseUrlInput.value.trim(),
    model: llmModelInput.value.trim()
  };
  const apiKey = llmApiKeyInput.value.trim();
  if (apiKey) payload.api_key = apiKey;
  if (!payload.base_url || !payload.model) {
    llmStatus.textContent = '⚠️ Base URL and model cannot be empty.';
    llmStatus.className = 'hint warn';
    return;
  }
  try {
    const response = await chrome.runtime.sendMessage({ action: 'saveChatConfig', config: payload });
    if (!response.success) throw new Error(response.error || 'Failed to save LLM settings');
    await loadLlmConfig();
    // Verify the saved credentials right away so a missing or stale key is
    // caught here, not later as a provider 401 mid-chat.
    await testLlmConnection();
  } catch (error) {
    llmStatus.textContent = '⚠️ ' + error.message;
    llmStatus.className = 'hint warn';
  }
}

async function testLlmConnection() {
  llmStatus.textContent = '⏳ Testing connection…';
  llmStatus.className = 'hint';
  try {
    const response = await chrome.runtime.sendMessage({ action: 'testChatConnection' });
    if (!response.success) throw new Error(response.error || 'Connection test failed');
    const model = response.result?.model;
    llmStatus.textContent = '✅ Connection OK — the provider answered a test request' +
      (model ? ` (${model})` : '') + '.';
    llmStatus.className = 'hint ok';
  } catch (error) {
    llmStatus.textContent = '⚠️ ' + error.message;
    llmStatus.className = 'hint warn';
  }
}

// ── ADS / SciX token ──
// The token lives on the ArXistant server (owner-only ads_token.txt in its
// data directory); the background worker relays saves/tests to /api/ads/token.

async function loadAdsToken() {
  adsStatus.textContent = 'Loading ADS / SciX token status…';
  adsStatus.className = 'hint';
  try {
    const response = await chrome.runtime.sendMessage({ action: 'getAdsToken' });
    if (!response.success) throw new Error(response.error || 'Failed to load token status');
    const has = !!(response.state && response.state.has_token);
    adsTokenInput.value = '';
    adsTokenInput.placeholder = has ? 'Token saved — paste a new one to replace' : 'API token';
    adsStatus.textContent = has
      ? '✅ Token saved (use Test Token to verify it works).'
      : '⚠️ No token saved — ADS / SciX search, the scixplorer.org panel, and SciX chat lookups need it.';
    adsStatus.className = has ? 'hint ok' : 'hint warn';
  } catch (error) {
    adsStatus.textContent = '⚠️ Could not load token status: ' + error.message;
    adsStatus.className = 'hint warn';
  }
}

async function saveAdsToken() {
  const token = adsTokenInput.value.trim();
  if (!token) {
    adsStatus.textContent = '⚠️ Paste a token first (get one at ui.adsabs.harvard.edu → Account → API Token).';
    adsStatus.className = 'hint warn';
    return;
  }
  try {
    const response = await chrome.runtime.sendMessage({ action: 'saveAdsToken', token });
    if (!response.success) throw new Error(response.error || 'Failed to save the token');
    await loadAdsToken();
    // Verify right away so a stale or mistyped token surfaces here.
    await testAdsToken();
  } catch (error) {
    adsStatus.textContent = '⚠️ ' + error.message;
    adsStatus.className = 'hint warn';
  }
}

async function testAdsToken() {
  adsStatus.textContent = '⏳ Testing token…';
  adsStatus.className = 'hint';
  try {
    const response = await chrome.runtime.sendMessage({ action: 'testAdsToken' });
    if (!response.success) throw new Error(response.error || 'Token test failed');
    const result = response.result || {};
    if (!result.success) throw new Error(result.error || 'The server could not verify the token');
    adsStatus.textContent = '✅ ' + (result.message || 'Token accepted.');
    adsStatus.className = 'hint ok';
  } catch (error) {
    adsStatus.textContent = '⚠️ ' + error.message;
    adsStatus.className = 'hint warn';
  }
}

async function clearAdsToken() {
  if (!confirm('Remove the stored ADS / SciX token? ADS search, the scixplorer.org panel, and SciX chat lookups will stop working.')) return;
  try {
    const response = await chrome.runtime.sendMessage({ action: 'saveAdsToken', token: '' });
    if (!response.success) throw new Error(response.error || 'Failed to remove the token');
    await loadAdsToken();
  } catch (error) {
    adsStatus.textContent = '⚠️ ' + error.message;
    adsStatus.className = 'hint warn';
  }
}

// ── Voice Reading (Listen) ──
// Voice role, papers-per-batch, and rate live on the ArXistant server (same
// store the Daily page reads); the background worker relays them to
// /api/tts/config. The voice is a ROLE ('', 'male', 'female') that every
// device resolves against its own speechSynthesis voices — keeping only
// the useful choices instead of a long list of system voices. Resolution
// prefers AMERICAN ENGLISH (Google's US voice when Chrome offers it).
// NOTE: kept in sync with the resolver in the Daily page's Listen script.

const TTS_DEFAULT_OPTION = 'System default';

function ttsVoices() {
  if (typeof speechSynthesis === 'undefined') return [];
  return speechSynthesis.getVoices() || [];
}

// Same resolution the Daily page uses, so what you test here is what
// you will hear there.
const FEMALE_VOICE_HINTS = ['female', 'samantha', 'karen', 'moira', 'tessa',
  'fiona', 'victoria', 'serena', 'allison', 'ava', 'susan', 'zoe', 'nicky',
  'catherine', 'charlotte', 'shelley', 'flo', 'kate', 'zira', 'hazel', 'eva',
  'michelle', 'google us english'];
const MALE_VOICE_HINTS = ['male', 'alex', 'daniel', 'david', 'fred', 'tom',
  'mark', 'matt', 'oliver', 'jacob', 'aaron', 'gordon', 'reed', 'bruce',
  'junior', 'davis', 'grandpa'];

function voiceMatchesGender(v, gender) {
  const n = String(v.name || '').toLowerCase();
  // Names that state their gender ("Google UK English Female"); note
  // "female" contains "male", so it must be tested first.
  if (n.includes('female')) return gender === 'female';
  if (n.includes('male')) return gender === 'male';
  const hints = gender === 'female' ? FEMALE_VOICE_HINTS : MALE_VOICE_HINTS;
  return hints.some(h => n.includes(h));
}

function voicePreferenceScore(v) {
  const n = String(v.name || '').toLowerCase();
  const lang = String(v.lang || '').toLowerCase().replace('_', '-');
  let score = 0;
  if (n.includes('google')) score += 50;      // Google TTS voice
  if (lang.startsWith('en-us')) score += 100; // American English
  else if (lang.startsWith('en')) score += 10; // any other English
  return score;
}

function findGenderedVoice(gender) {
  const all = ttsVoices();
  // Prefer English voices — the digests are English.
  let pool = all.filter(v => {
    const lang = String(v.lang || '').toLowerCase().replace('_', '-');
    return lang.startsWith('en') || String(v.name || '').toLowerCase().includes('english');
  });
  if (!pool.length) pool = all.slice();
  let best = null, bestScore = -1;
  for (const v of pool) {
    if (!voiceMatchesGender(v, gender)) continue;
    const s = voicePreferenceScore(v);
    if (s > bestScore) { bestScore = s; best = v; }
  }
  return best;
}

function resolveTtsVoice(role) {
  if (role === 'male' || role === 'female') return findGenderedVoice(role);
  return null;  // '' (system default) or unknown role
}

function renderTtsVoices(selectedRole) {
  ttsVoiceInput.replaceChildren();
  const def = document.createElement('option');
  def.value = '';
  def.textContent = TTS_DEFAULT_OPTION;
  ttsVoiceInput.appendChild(def);

  const male = resolveTtsVoice('male');
  const female = resolveTtsVoice('female');
  for (const [role, label, voice] of [['male', 'Man', male], ['female', 'Woman', female]]) {
    const opt = document.createElement('option');
    opt.value = role;
    // Show which concrete voice the role resolves to on this computer.
    opt.textContent = voice ? `${label} (${voice.name})` : `${label} (not found on this computer)`;
    if (!voice) opt.disabled = true;
    ttsVoiceInput.appendChild(opt);
  }
  ttsVoiceInput.value = selectedRole || '';
  return ttsVoiceInput.value === (selectedRole || '');
}

function updateTtsStatus(cfg, voiceFound) {
  const missing = [];
  if (!cfg.llm_model) missing.push('LLM (see the section above)');
  const notes = missing.length
    ? `⚠️ Without ${missing.join(', ')}, Listen reads the raw title and abstract.`
    : '✅ Digests will be written by ' + cfg.llm_model + '.';
  const role = cfg.voice || '';
  let voiceNote;
  if (role) {
    const v = resolveTtsVoice(role);
    voiceNote = v
      ? ` Voice: ${role === 'male' ? 'Man' : 'Woman'} (${v.name}) on this computer.`
      : ` ⚠️ The ${role === 'male' ? 'man' : 'woman'} voice is not available on this computer — the system default will be used.`;
  } else {
    voiceNote = ' Using the system default voice.';
  }
  ttsStatus.textContent = notes + voiceNote;
  ttsStatus.className = 'hint ' + (missing.length || (role && !voiceFound) ? 'warn' : 'ok');
}

async function loadTtsConfig() {
  ttsStatus.textContent = 'Loading voice settings…';
  ttsStatus.className = 'hint';
  let cfg = {};
  try {
    const response = await chrome.runtime.sendMessage({ action: 'getTtsConfig' });
    if (!response.success) throw new Error(response.error || 'Failed to load voice settings');
    cfg = response.config || {};
  } catch (error) {
    ttsStatus.textContent = '⚠️ Could not load voice settings: ' + error.message;
    ttsStatus.className = 'hint warn';
    return;
  }
  ttsPapersInput.value = cfg.papers_per_read || 5;
  ttsRateInput.value = String(cfg.rate || 1.0);
  const voiceFound = renderTtsVoices(cfg.voice || '');
  updateTtsStatus(cfg, voiceFound);
  // getVoices() is often empty on first paint and fills asynchronously.
  if (typeof speechSynthesis !== 'undefined') {
    speechSynthesis.addEventListener?.('voiceschanged', () => {
      const found = renderTtsVoices(cfg.voice || '');
      updateTtsStatus(cfg, found);
    });
  }
}

async function saveTtsConfig() {
  const papers = Number.parseInt(ttsPapersInput.value, 10);
  if (!Number.isInteger(papers) || papers < 1 || papers > 50) {
    expandSection('section-voice');
    ttsStatus.textContent = '⚠️ Papers per reading must be between 1 and 50.';
    ttsStatus.className = 'hint warn';
    return;
  }
  try {
    const response = await chrome.runtime.sendMessage({
      action: 'saveTtsConfig',
      config: {
        voice: ttsVoiceInput.value || '',
        papers_per_read: papers,
        rate: Number.parseFloat(ttsRateInput.value) || 1.0
      }
    });
    if (!response.success) throw new Error(response.error || 'Failed to save voice settings');
    const saved = response.config || {};
    ttsStatus.textContent = '✅ Saved. The 🔊 Listen button now reads ' +
      saved.papers_per_read + ' papers per batch at ' + saved.rate + '× speed' +
      (saved.voice ? ' with the ' + (saved.voice === 'male' ? 'man' : 'woman') + ' voice.'
                   : ' with the system default voice.');
    ttsStatus.className = 'hint ok';
  } catch (error) {
    ttsStatus.textContent = '⚠️ ' + error.message;
    ttsStatus.className = 'hint warn';
  }
}

function testVoice() {
  if (typeof speechSynthesis === 'undefined') {
    ttsStatus.textContent = '⚠️ This browser has no speech synthesis.';
    ttsStatus.className = 'hint warn';
    return;
  }
  speechSynthesis.cancel();
  // Sample mirrors the real reading: announcement, pause, digest.
  const u = new SpeechSynthesisUtterance(
    'Paper 1. The Dark-matter Origin of Little Red Dots. By Hua-Peng Gu and colleagues. ' +
    'This is how every paper is announced before its digest is read aloud.');
  const voice = resolveTtsVoice(ttsVoiceInput.value);
  if (voice) { u.voice = voice; u.lang = voice.lang || 'en-US'; }
  u.rate = Number.parseFloat(ttsRateInput.value) || 1.0;
  speechSynthesis.speak(u);
  ttsStatus.textContent = '▶ Playing a sample with ' + (voice ? voice.name : 'the system default voice') +
    ' at ' + u.rate + '× — adjust above; voice and rate changes save immediately.';
  ttsStatus.className = 'hint';
}

// ── Cloud Sync ──
function formatCloudStatus(state) {
  const lines = [];
  lines.push(`Provider: ${state.provider || 'none'}${state.enabled ? ' (enabled)' : ' (disabled)'}`);
  if (state.device_id) lines.push(`Device ID: ${state.device_id}`);
  const ps = state.provider_status || {};
  if (state.provider === 'local_folder') {
    if (ps.path) lines.push(`Folder: ${ps.path}`);
    if (ps.file_exists) lines.push('Remote snapshot: present');
  } else if (state.provider === 'webdav') {
    lines.push(`WebDAV configured: ${ps.configured ? 'yes' : 'no'}`);
    if (ps.url) lines.push(`Address: ${ps.url}`);
    if (ps.username) lines.push(`Email: ${ps.username}`);
    if (ps.folder) lines.push(`Folder: ${ps.folder} (auto-created)`);
  }
  if (state.keychain_available === false) {
    lines.push('⚠ Keychain unavailable — credentials cannot be stored securely.');
  }
  lines.push(`Last sync: ${state.last_sync_at ? new Date(state.last_sync_at).toLocaleString() : 'never'}`);
  if (state.last_error) lines.push(`Last error: ${state.last_error}`);
  return lines.join('\n');
}

function updateCloudProviderFields() {
  const provider = cloudProviderInput.value;
  cloudLocalGroup.style.display = provider === 'local_folder' ? 'block' : 'none';
  cloudWebdavGroup.style.display = provider === 'webdav' ? 'block' : 'none';
}

async function loadCloudStatus() {
  cloudStatus.textContent = 'Loading cloud sync status…';
  cloudStatus.style.color = '';
  try {
    const response = await chrome.runtime.sendMessage({ action: 'getCloudStatus' });
    if (!response.success) throw new Error(response.error || 'Status unavailable');
    const state = response.state;
    const cfg = state.config || {};
    cloudProviderInput.value = state.provider || 'local_folder';
    cloudFolderInput.value = cfg.local_folder_path || '';
    webdavUrlInput.value = cfg.webdav_url || '';
    webdavUsernameInput.value = cfg.webdav_username || '';
    webdavPasswordInput.value = '';
    webdavPasswordInput.placeholder = cfg.webdav_password_set
      ? 'Saved in system keychain (leave blank to keep)'
      : 'App password';
    cloudEnabledInput.checked = state.enabled !== false;
    updateCloudProviderFields();
    cloudStatus.textContent = formatCloudStatus(state);
  } catch (error) {
    cloudStatus.textContent = `Cloud sync unavailable: ${error.message}`;
  }
}

async function sendCloudSettings(config) {
  const response = await chrome.runtime.sendMessage({ action: 'saveCloudSettings', config });
  if (!response.success) throw new Error(response.error || 'Failed to save settings');
  return response;
}

function collectCloudConfig() {
  return {
    provider: cloudProviderInput.value,
    local_folder_path: cloudFolderInput.value.trim(),
    webdav_url: webdavUrlInput.value.trim(),
    webdav_username: webdavUsernameInput.value.trim(),
    webdav_password: webdavPasswordInput.value,
    enabled: true
  };
}

async function connectCloud() {
  cloudStatus.textContent = 'Connecting…';
  cloudStatus.style.color = '';
  try {
    await sendCloudSettings(collectCloudConfig());
    cloudEnabledInput.checked = true;
    const syncResp = await chrome.runtime.sendMessage({ action: 'cloudSync' });
    if (!syncResp.success) throw new Error(syncResp.error || 'Sync failed');
    const stats = syncResp.result?.stats ? `\nChanges: ${JSON.stringify(syncResp.result.stats)}` : '';
    cloudStatus.textContent = 'Connected.' + stats;
  } catch (error) {
    cloudStatus.textContent = `✗ ${error.message}`;
    cloudStatus.style.color = '#c62828';
    cloudStatus.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
}

async function onCloudEnabledChange() {
  try {
    await sendCloudSettings({ enabled: cloudEnabledInput.checked });
  } catch (error) {
    cloudStatus.textContent = `✗ ${error.message}`;
    cloudStatus.style.color = '#c62828';
  }
}

async function disconnectCloud() {
  try {
    const response = await chrome.runtime.sendMessage({ action: 'cloudDisconnect' });
    if (!response.success) throw new Error(response.error || 'Disconnect failed');
    await loadCloudStatus();
    cloudStatus.textContent = 'Cloud sync disabled.';
  } catch (error) {
    cloudStatus.textContent = `Disconnect failed: ${error.message}`;
  }
}
