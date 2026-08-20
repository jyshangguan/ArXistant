// ArXistant Chrome Extension - Background Service Worker

const DEFAULT_SERVER_URL = 'http://localhost:8765/daily.html';
const DEFAULT_REMINDER_TIMES = ['10:30'];
const DEFAULT_SKIP_WEEKENDS = true;
const DEFAULT_RETRAIN_AFTER_CHANGES = 5;
const LEGACY_ALARM_NAME = 'daily-arxiv-reminder';
const ALARM_PREFIX = 'arxistant-reminder:';
const STORAGE_KEY_SETTINGS = 'settings';
const STORAGE_KEY_LAST_DAILY_REFRESH = 'lastDailyRefreshDate';
let dailyRefreshInFlight = null;

chrome.runtime.onInstalled.addListener(() => {
  syncReminderAlarms().catch(error => console.error('[ArXistant] Alarm setup failed:', error));
  chrome.action.setBadgeText({ text: '' });
});

chrome.runtime.onStartup.addListener(() => {
  // Keep overdue alarms intact so Chrome can deliver them after the browser
  // was closed or the device was asleep. Clearing them here can discard the
  // first reminder of the day before its onAlarm event is dispatched.
  ensureReminderAlarms().catch(error => console.error('[ArXistant] Alarm recovery failed:', error));
});

function normalizeReminderTimes(value) {
  const source = Array.isArray(value) ? value : [];
  return [...new Set(source.filter(time => /^([01]\d|2[0-3]):[0-5]\d$/.test(time)))].sort();
}

async function getSettings() {
  const result = await chrome.storage.local.get(STORAGE_KEY_SETTINGS);
  const stored = result[STORAGE_KEY_SETTINGS] || {};
  let reminderTimes = normalizeReminderTimes(stored.reminderTimes);

  // Migrate the original single hour/minute setting without losing it.
  if (!reminderTimes.length && Number.isInteger(stored.reminderHour) && Number.isInteger(stored.reminderMinute)) {
    reminderTimes = [
      `${String(stored.reminderHour).padStart(2, '0')}:${String(stored.reminderMinute).padStart(2, '0')}`
    ];
  }

  return {
    serverUrl: stored.serverUrl || DEFAULT_SERVER_URL,
    reminderTimes: reminderTimes.length ? reminderTimes : DEFAULT_REMINDER_TIMES,
    // Missing means this setting predates weekend support, so use the new
    // default. Only an explicit false enables Saturday/Sunday reminders.
    skipWeekends: stored.skipWeekends !== false,
    retrainAfterChanges: Number.isInteger(stored.retrainAfterChanges)
      ? Math.min(100, Math.max(1, stored.retrainAfterChanges))
      : DEFAULT_RETRAIN_AFTER_CHANGES
  };
}

async function saveSettings(settings) {
  const normalized = {
    serverUrl: settings.serverUrl || DEFAULT_SERVER_URL,
    reminderTimes: normalizeReminderTimes(settings.reminderTimes),
    skipWeekends: settings.skipWeekends !== false,
    retrainAfterChanges: Math.min(100, Math.max(1,
      Number.parseInt(settings.retrainAfterChanges, 10) || DEFAULT_RETRAIN_AFTER_CHANGES))
  };
  await chrome.storage.local.set({ [STORAGE_KEY_SETTINGS]: normalized });
  return normalized;
}

function serverApiUrl(serverUrl, path) {
  return new URL(path, serverUrl).toString();
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const responseText = await response.text();
  let data;
  try {
    data = JSON.parse(responseText);
  } catch (_) {
    if (response.status === 404) {
      throw new Error('The running ArXistant server is outdated. Restart the server and try again.');
    }
    throw new Error(responseText.trim() || `Server returned ${response.status}`);
  }
  if (!response.ok || data.success === false) throw new Error(data.error || `Server returned ${response.status}`);
  return data;
}

async function syncRetrainingSetting(settings) {
  return fetchJson(serverApiUrl(settings.serverUrl, '/api/ml-retraining/settings'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ retrain_after_changes: settings.retrainAfterChanges })
  });
}

function localDateKey(date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

async function refreshDailyAtOrAfterFirstReminder(settings, reminderTime, now = new Date()) {
  const firstReminder = normalizeReminderTimes(settings.reminderTimes)[0];
  // A later reminder is also eligible. This catches up when the first alarm
  // was missed while Chrome was closed/asleep; the stored date still limits
  // successful refreshes to one per day.
  if (!firstReminder || reminderTime < firstReminder) return { skipped: 'before-first-reminder' };

  const today = localDateKey(now);
  const stored = await chrome.storage.local.get(STORAGE_KEY_LAST_DAILY_REFRESH);
  if (stored[STORAGE_KEY_LAST_DAILY_REFRESH] === today) return { skipped: 'already-refreshed' };

  // Coalesce duplicate alarm delivery while a refresh request is still active.
  if (!dailyRefreshInFlight) {
    dailyRefreshInFlight = (async () => {
      const result = await fetchJson(serverApiUrl(settings.serverUrl, '/api/refresh-daily'), {
        method: 'POST'
      });
      await chrome.storage.local.set({ [STORAGE_KEY_LAST_DAILY_REFRESH]: today });
      console.log(`[ArXistant] Daily papers refreshed at reminder ${reminderTime}`);
      return result;
    })().finally(() => {
      dailyRefreshInFlight = null;
    });
  }
  return dailyRefreshInFlight;
}

function nextOccurrence(time, skipWeekends = DEFAULT_SKIP_WEEKENDS, now = new Date()) {
  const [hour, minute] = time.split(':').map(Number);
  const next = new Date(now.getFullYear(), now.getMonth(), now.getDate(), hour, minute, 0, 0);
  if (next.getTime() <= now.getTime()) next.setDate(next.getDate() + 1);
  if (skipWeekends) {
    while (next.getDay() === 0 || next.getDay() === 6) {
      next.setDate(next.getDate() + 1);
    }
  }
  return next;
}

async function scheduleReminder(time, skipWeekends = DEFAULT_SKIP_WEEKENDS) {
  const when = nextOccurrence(time, skipWeekends);
  await chrome.alarms.create(ALARM_PREFIX + time, { when: when.getTime() });
  console.log(`[ArXistant] Reminder ${time} scheduled for ${when.toLocaleString()}`);
}

async function syncReminderAlarms() {
  const settings = await getSettings();
  const alarms = await chrome.alarms.getAll();
  await Promise.all(
    alarms
      .filter(alarm => alarm.name === LEGACY_ALARM_NAME || alarm.name.startsWith(ALARM_PREFIX))
      .map(alarm => chrome.alarms.clear(alarm.name))
  );
  await Promise.all(settings.reminderTimes.map(time => scheduleReminder(time, settings.skipWeekends)));
}

async function ensureReminderAlarms() {
  const settings = await getSettings();
  const alarms = await chrome.alarms.getAll();
  const existing = new Set(alarms.map(alarm => alarm.name));
  await Promise.all(
    settings.reminderTimes
      .filter(time => !existing.has(ALARM_PREFIX + time))
      .map(time => scheduleReminder(time, settings.skipWeekends))
  );
}

chrome.alarms.onAlarm.addListener(async alarm => {
  if (!alarm.name.startsWith(ALARM_PREFIX)) return;
  const time = alarm.name.slice(ALARM_PREFIX.length);
  console.log(`[ArXistant] Reminder ${time} fired`);
  const settings = await getSettings();
  const today = new Date().getDay();
  const isWeekend = today === 0 || today === 6;

  try {
    if (settings.skipWeekends && isWeekend) {
      console.log(`[ArXistant] Reminder ${time} skipped for the weekend`);
    } else {
      try {
        await refreshDailyAtOrAfterFirstReminder(settings, time);
      } catch (error) {
        // A network/server failure must not suppress the user's reminder.
        console.error('[ArXistant] Automatic daily refresh failed:', error);
      }
      await showReminderNotification(time);
    }
  } finally {
    // Schedule by local calendar day rather than adding 24 hours, which avoids
    // daylight-saving drift and restores one-shot alarms after browser sleep.
    await scheduleReminder(time, settings.skipWeekends);
  }
});

async function showReminderNotification(time = '') {
  const permissionLevel = await chrome.notifications.getPermissionLevel();
  if (permissionLevel !== 'granted') {
    throw new Error(`Chrome notification permission is ${permissionLevel}`);
  }

  const notificationId = `arxistant-reminder-${Date.now()}`;
  await chrome.notifications.create(notificationId, {
    type: 'basic',
    iconUrl: 'icons/icon128.png',
    title: 'Daily arXiv Papers Ready',
    message: time
      ? `Your ${time} ArXistant reminder: personalized papers are waiting.`
      : 'Your personalized astro-ph feed is waiting at ArXistant.',
    priority: 2,
    requireInteraction: true,
    silent: false
  });

  // create() only confirms that Chrome accepted the request. getAll() lets the
  // diagnostics UI distinguish that from a Chrome-side delivery failure;
  // macOS can still hide an accepted notification because of Focus or banner
  // presentation settings.
  const activeNotifications = await chrome.notifications.getAll();
  return {
    notificationId,
    permissionLevel,
    acceptedByChrome: Object.prototype.hasOwnProperty.call(activeNotifications, notificationId)
  };
}

chrome.notifications.onClicked.addListener(async notificationId => {
  if (!notificationId.startsWith('arxistant-reminder-')) return;
  await openDailyPapers();
  await chrome.notifications.clear(notificationId);
});

async function openDailyPapers() {
  const settings = await getSettings();
  const url = settings.serverUrl;
  const tabs = await chrome.tabs.query({ url: url + '*' });
  if (tabs.length) {
    await chrome.tabs.update(tabs[0].id, { active: true });
    await chrome.windows.update(tabs[0].windowId, { focused: true });
  } else {
    await chrome.tabs.create({ url });
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    switch (message.action) {
      case 'openDaily':
        await openDailyPapers();
        return { success: true };
      case 'getSettings':
        return { settings: await getSettings() };
      case 'saveSettings': {
        const settings = await saveSettings(message.settings);
        await syncReminderAlarms();
        let retrainingSync;
        try {
          retrainingSync = { success: true, state: await syncRetrainingSetting(settings) };
        } catch (error) {
          retrainingSync = { success: false, error: error.message };
        }
        return { success: true, settings, retrainingSync };
      }
      case 'getMLRetrainingStatus': {
        const settings = await getSettings();
        await syncRetrainingSetting(settings);
        return { success: true, state: await fetchJson(serverApiUrl(settings.serverUrl, '/api/ml-retraining')) };
      }
      case 'getReminderStatus': {
        const alarms = (await chrome.alarms.getAll())
          .filter(alarm => alarm.name.startsWith(ALARM_PREFIX))
          .map(alarm => ({ time: alarm.name.slice(ALARM_PREFIX.length), scheduledTime: alarm.scheduledTime }));
        return { alarms, permissionLevel: await chrome.notifications.getPermissionLevel() };
      }
      case 'testNotification':
        return { success: true, ...(await showReminderNotification()) };
      case 'getCloudStatus': {
        const settings = await getSettings();
        return { success: true, state: await fetchJson(serverApiUrl(settings.serverUrl, '/api/cloud/status')) };
      }
      case 'saveCloudSettings': {
        const settings = await getSettings();
        return {
          success: true,
          config: await fetchJson(serverApiUrl(settings.serverUrl, '/api/cloud/settings'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(message.config || {})
          })
        };
      }
      case 'cloudSync': {
        const settings = await getSettings();
        return { success: true, result: await fetchJson(serverApiUrl(settings.serverUrl, '/api/cloud/sync'), { method: 'POST' }) };
      }
      case 'cloudDisconnect': {
        const settings = await getSettings();
        return { success: true, result: await fetchJson(serverApiUrl(settings.serverUrl, '/api/cloud/disconnect'), { method: 'POST' }) };
      }
      default:
        return { success: false, error: 'Unknown action' };
    }
  })().then(sendResponse).catch(error => {
    console.error('[ArXistant] Message handler error:', error);
    sendResponse({ success: false, error: error.message });
  });
  return true;
});

ensureReminderAlarms().catch(error => console.error('[ArXistant] Alarm recovery failed:', error));
console.log('[ArXistant] Background service worker loaded');
