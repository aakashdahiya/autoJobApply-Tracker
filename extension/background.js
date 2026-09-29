// Service worker: orchestrates capture and form filling, talks to the local
// API, and watches for the confirmation page after you submit.
// No secrets live here — the extension only ever talks to 127.0.0.1.

const DEFAULT_API = "http://127.0.0.1:8765";
const PENDING = "pendingApplications"; // tabId -> {applicationId, ats}

async function apiBase() {
  const { apiBase } = await chrome.storage.sync.get("apiBase");
  return apiBase || DEFAULT_API;
}

async function api(path, options) {
  const response = await fetch(`${await apiBase()}${path}`, options);
  if (!response.ok) throw new Error(`API ${response.status} on ${path}`);
  return response.json();
}

const postJson = (body) => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

function notify(title, message) {
  chrome.notifications.create({ type: "basic", iconUrl: "icon.png", title, message });
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab && tab.id && /^https?:/.test(tab.url || "") ? tab : null;
}

// The agent guards itself with a window flag, so re-injecting is free.
async function ensureAgent(tabId) {
  await chrome.scripting.executeScript({ target: { tabId }, files: ["content/agent.js"] });
}

const ask = (tabId, message) => chrome.tabs.sendMessage(tabId, message);

// --- capture ---------------------------------------------------------------

async function captureActiveTab() {
  const tab = await activeTab();
  if (!tab) {
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
    notify("Could not read this posting", "No title or company found on this page.");
    return { ok: false, error: "unreadable" };
  }

  try {
    const result = await api("/jobs", postJson(job));
    if (result.duplicate_warning) notify("Careful — already applied", result.duplicate_warning);
    else {
      notify(result.created ? "Saved" : "Already tracked",
        `${result.job.title} — ${result.job.company}`);
    }
    chrome.runtime.sendMessage({ type: "jobs-changed" }).catch(() => {});
    return { ok: true, result };
  } catch (error) {
    notify("Tracker unreachable", "Is the API running on 127.0.0.1:8765?");
    return { ok: false, error: String(error) };
  }
}

// --- saving from a results card --------------------------------------------

// A card gives title, company, location and the job's URL, but never the
// description — that lives on the detail page. So the save happens first (it is
// what you clicked for), and the description is fetched afterwards on a best
// effort. Scoring and tailoring both need it, so when the fetch comes back
// empty the job is left marked as needing its description rather than being
// presented as ready to apply.
async function saveCard(payload) {
  let result;
  try {
    result = await api("/jobs", postJson(payload));
  } catch (error) {
    notify("Tracker unreachable", "Is the API running on 127.0.0.1:8765?");
    return { ok: false, error: String(error.message || error) };
  }

  chrome.runtime.sendMessage({ type: "jobs-changed" }).catch(() => {});
  const jobId = result.job && result.job.id;

  // Enrich in the background: the click has already been answered.
  if (jobId && payload.apply_url) {
    enrich(payload).catch(() => {});
  }

  return { ok: true, duplicate: result.created === false, result };
}

// Re-saves the same job with its description attached. `POST /jobs` dedupes on
// the same key and fills in a description the first save lacked, so this needs
// no separate endpoint and inherits the capture path's own tests.
async function enrich(payload) {
  let html;
  try {
    const response = await fetch(payload.apply_url, { credentials: "include" });
    if (!response.ok) return;
    html = await response.text();
  } catch {
    return; // offline, blocked, or a login wall — the job is still saved
  }

  const description = descriptionFromHtml(html);
  if (!description) return;

  await api("/jobs", postJson({ ...payload, description }));
  chrome.runtime.sendMessage({ type: "jobs-changed" }).catch(() => {});
}

// The service worker has no DOM, so this reads the fetched page as text.
// schema.org first, because both sites emit it and it survives redesigns;
// the container ids are the fallback.
function descriptionFromHtml(html) {
  for (const match of html.matchAll(
    /<script[^>]+application\/ld\+json[^>]*>([\s\S]*?)<\/script>/gi
  )) {
    try {
      const found = findPosting(JSON.parse(match[1]));
      if (found && found.description) return stripTags(found.description).slice(0, 20000);
    } catch {
      // A malformed block is not a reason to give up on the others.
    }
  }
  const container =
    /<div[^>]+id="jobDescriptionText"[^>]*>([\s\S]*?)<\/div>/i.exec(html) ||
    /<div[^>]+class="[^"]*jobs-description__content[^"]*"[^>]*>([\s\S]*?)<\/div>/i.exec(html);
  return container ? stripTags(container[1]).slice(0, 20000) : "";
}

function findPosting(data) {
  const nodes = Array.isArray(data) ? data : data["@graph"] || [data];
  for (const node of [].concat(nodes)) {
    const type = node && node["@type"];
    if (type === "JobPosting" || (Array.isArray(type) && type.includes("JobPosting"))) {
      return node;
    }
  }
  return null;
}

function stripTags(value) {
  return String(value)
    .replace(/<[^>]*>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&#39;|&apos;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/\s+/g, " ")
    .trim();
}

// --- filling ---------------------------------------------------------------

// The name the employer's ATS shows next to your upload. The server picks it,
// and picks the same one for every posting on purpose: the internal filename
// carries the company and the role shape, which would tell a recruiter you keep
// a per-company variant.
function resumeFilename(resolved, shape) {
  return (
    (resolved && resolved.resume_filename) || (shape ? `resume-${shape}.pdf` : "resume.pdf")
  );
}


async function fillActiveTab({ shape, applicationId }) {
  const tab = await activeTab();
  if (!tab) return { ok: false, error: "no page" };

  try {
    await ensureAgent(tab.id);
  } catch (error) {
    return { ok: false, error: `cannot run on this page: ${error.message || error}` };
  }

  const scan = await ask(tab.id, { type: "scan" });
  if (!scan || !scan.fields.length) {
    return { ok: false, error: "no form fields found on this page" };
  }

  const ats = scan.workday ? "workday" : "generic";
  let resolved;
  try {
    resolved = await api("/autofill/resolve", postJson({
      fields: scan.fields,
      shape: shape || null,
      ats,
      // The application decides which resume gets attached: its own tailored
      // one when it has been tailored, the shape's only as a fallback.
      application_id: applicationId || null,
    }));
  } catch (error) {
    notify("Tracker unreachable", "Start the API before filling a form.");
    return { ok: false, error: String(error) };
  }

  const applied = await ask(tab.id, { type: "apply", fills: resolved.fills });

  let attached = null;
  if (resolved.resume_url) {
    attached = await ask(tab.id, {
      type: "attach",
      url: `${await apiBase()}${resolved.resume_url}`,
      filename: resumeFilename(resolved, shape),
    });
  }

  if (applicationId) {
    // Remember which application this tab is applying to, so the confirmation
    // page can move its status without anyone having to remember to.
    const store = (await chrome.storage.session.get(PENDING))[PENDING] || {};
    store[tab.id] = { applicationId, ats };
    await chrome.storage.session.set({ [PENDING]: store });

    await api("/autofill/log", postJson({
      application_id: applicationId,
      ats,
      step: scan.workday ? scan.workday.step : null,
      filled: applied.filled,
      corrected: applied.corrected,
    })).catch(() => {});
  }

  const parts = [
    `${Object.keys(applied.filled).length} filled`,
    Object.keys(applied.corrected).length ? `${Object.keys(applied.corrected).length} corrected` : "",
    resolved.unmatched ? `${resolved.unmatched} unmatched` : "",
    applied.failed.length ? `${applied.failed.length} failed` : "",
  ].filter(Boolean);
  notify("Form filled — review before you submit", parts.join(", "));

  return {
    ok: true,
    summary: {
      filled: Object.keys(applied.filled).length,
      corrected: Object.keys(applied.corrected).length,
      unmatched: resolved.unmatched,
      skipped: resolved.skipped,
      failed: applied.failed,
      attached,
      workday: scan.workday,
      reuseOffered: Boolean(scan.workday && scan.workday.canReuseLastApplication),
    },
  };
}

// --- confirmation watching -------------------------------------------------

chrome.tabs.onUpdated.addListener(async (tabId, info) => {
  if (info.status !== "complete") return;
  const store = (await chrome.storage.session.get(PENDING))[PENDING] || {};
  const pending = store[tabId];
  if (!pending) return;

  try {
    await ensureAgent(tabId);
    const result = await ask(tabId, { type: "confirmation" });
    if (!result || !result.confirmation) return;

    await api(`/applications/${pending.applicationId}/submitted`, { method: "POST" });
    delete store[tabId];
    await chrome.storage.session.set({ [PENDING]: store });
    notify("Marked as applied", "Confirmation page detected.");
    chrome.runtime.sendMessage({ type: "jobs-changed" }).catch(() => {});
  } catch {
    // A page we cannot read is not an error worth interrupting anyone over.
  }
});

chrome.tabs.onRemoved.addListener(async (tabId) => {
  const store = (await chrome.storage.session.get(PENDING))[PENDING] || {};
  if (store[tabId]) {
    delete store[tabId];
    await chrome.storage.session.set({ [PENDING]: store });
  }
});

// --- plumbing --------------------------------------------------------------

chrome.commands.onCommand.addListener((command) => {
  if (command === "capture-job") captureActiveTab();
});

chrome.runtime.onMessage.addListener((message, _sender, respond) => {
  if (message.type === "capture") {
    captureActiveTab().then(respond);
    return true;
  }
  if (message.type === "fill") {
    fillActiveTab(message).then(respond);
    return true;
  }
  if (message.type === "save-card") {
    saveCard(message.payload).then(respond);
    return true;
  }
  if (message.type === "api-base") {
    apiBase().then(respond);
    return true;
  }
  return false;
});

// Exposed for the tests, the way the content scripts do it. The description
// parser reads other people's HTML, so it is tested rather than trusted.
if (typeof globalThis !== "undefined") {
  globalThis.__jobTrackerInternals = { descriptionFromHtml, stripTags, resumeFilename };
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
});
