// ArXistant Options Page Script

const DEFAULT_SETTINGS = {
  serverUrl: 'http://localhost:8765/daily.html',
  reminderTimes: ['10:30']
};

const serverUrlInput = document.getElementById('server-url');
const reminderTimesList = document.getElementById('reminder-times');
const btnAddTime = document.getElementById('btn-add-time');
const btnSave = document.getElementById('btn-save');
const btnReset = document.getElementById('btn-reset');
const saveStatus = document.getElementById('save-status');
const btnTestNotify = document.getElementById('btn-test-notify');
const testStatus = document.getElementById('test-status');
const alarmStatus = document.getElementById('alarm-status');

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
    await updateAlarmStatus();
  } catch (error) {
    console.error('Failed to load settings:', error);
    renderTimes(DEFAULT_SETTINGS.reminderTimes);
    showStatus('Could not load settings.', 'error');
  }
}

async function saveSettings() {
  const serverUrl = serverUrlInput.value.trim();
  const reminderTimes = collectTimes();
  if (!serverUrl) return showStatus('Server URL cannot be empty.', 'error');
  if (!reminderTimes.length) return showStatus('Add at least one reminder time.', 'error');

  try {
    const response = await chrome.runtime.sendMessage({
      action: 'saveSettings',
      settings: { serverUrl, reminderTimes }
    });
    if (!response.success) throw new Error(response.error || 'Failed to save settings');
    renderTimes(response.settings.reminderTimes);
    showStatus('Settings saved and reminders rescheduled.', 'success');
    await updateAlarmStatus();
  } catch (error) {
    showStatus(`Error saving settings: ${error.message}`, 'error');
  }
}

async function resetSettings() {
  serverUrlInput.value = DEFAULT_SETTINGS.serverUrl;
  renderTimes(DEFAULT_SETTINGS.reminderTimes);
  await saveSettings();
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
