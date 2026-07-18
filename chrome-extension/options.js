// ArXistant Options Page Script

const DEFAULT_SETTINGS = {
  serverUrl: 'http://localhost:8765/daily.html',
  reminderTimes: ['10:30'],
  skipWeekends: true,
  retrainAfterChanges: 5
};

const serverUrlInput = document.getElementById('server-url');
const reminderTimesList = document.getElementById('reminder-times');
const skipWeekendsInput = document.getElementById('skip-weekends');
const btnAddTime = document.getElementById('btn-add-time');
const btnSave = document.getElementById('btn-save');
const btnReset = document.getElementById('btn-reset');
const saveStatus = document.getElementById('save-status');
const btnTestNotify = document.getElementById('btn-test-notify');
const testStatus = document.getElementById('test-status');
const alarmStatus = document.getElementById('alarm-status');
const retrainAfterChangesInput = document.getElementById('retrain-after-changes');
const retrainingStatus = document.getElementById('retraining-status');

document.addEventListener('DOMContentLoaded', async () => {
  bindEvents();
  await loadSettings();
});

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
  if (!serverUrl) return showStatus('Server URL cannot be empty.', 'error');
  if (!reminderTimes.length) return showStatus('Add at least one reminder time.', 'error');
  if (!Number.isInteger(retrainAfterChanges) || retrainAfterChanges < 1 || retrainAfterChanges > 100) {
    return showStatus('Retraining threshold must be between 1 and 100.', 'error');
  }

  try {
    const response = await chrome.runtime.sendMessage({
      action: 'saveSettings',
      settings: { serverUrl, reminderTimes, skipWeekends, retrainAfterChanges }
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
      testStatus.textContent = 'Chrome accepted and retained the notification. If no banner appeared, check macOS banner style and Focus mode below.';
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
}
