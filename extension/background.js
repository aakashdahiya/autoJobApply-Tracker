// Service worker: runs the capture, talks to the local API, nudges the panel.
// No secrets live here — the extension only ever talks to 127.0.0.1.

const DEFAULT_API = "http://127.0.0.1:8765";

async function apiBase() {
  const { apiBase } = await chrome.storage.sync.get("apiBase");
  return apiBase || DEFAULT_API;
}

function notify(title, message) {
  chrome.notifications.create({
    type: "basic",
    iconUrl: "icon.png",
    title,
    message,
  });
}

async function captureActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id || !/^https?:/.test(tab.url || "")) {
    notify("Nothing to save", "Open a job posting first.");
    return { ok: false, error: "no page" };
  }

  let injection;
  try {
    [injection] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["content/capture.js"],
    });
  } catch (error) {
    notify("Could not read the page", String(error.message || error));
    return { ok: false, error: String(error) };
  }

  const job = injection && injection.result;
  if (!job || !job.ok) {
    notify(
      "Could not read this posting",
      "No title or company found. Open the job's own page and try again."
    );
    return { ok: false, error: "unreadable", job };
  }

  try {
    const response = await fetch(`${await apiBase()}/jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(job),
    });
    if (!response.ok) throw new Error(`API said ${response.status}`);
    const result = await response.json();

    if (result.duplicate_warning) {
      notify("Careful — already applied", result.duplicate_warning);
    } else {
      notify(
        result.created ? "Saved" : "Already tracked",
        `${result.job.title} — ${result.job.company}`
      );
    }
    chrome.runtime.sendMessage({ type: "jobs-changed" }).catch(() => {});
    return { ok: true, result };
  } catch (error) {
    notify("Tracker unreachable", "Is the API running on 127.0.0.1:8765?");
    return { ok: false, error: String(error) };
  }
}

chrome.commands.onCommand.addListener((command) => {
  if (command === "capture-job") captureActiveTab();
});

chrome.runtime.onMessage.addListener((message, _sender, respond) => {
  if (message.type === "capture") {
    captureActiveTab().then(respond);
    return true; // respond asynchronously
  }
  if (message.type === "api-base") {
    apiBase().then(respond);
    return true;
  }
});

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
});
