// The queue. Reads the local API, opens apply pages, moves statuses along.

const $ = (id) => document.getElementById(id);
const NEXT_STATUS = {
  discovered: ["ready", "skipped"],
  scored: ["ready", "skipped"],
  tailored: ["ready", "skipped"],
  ready: ["applied", "skipped"],
  applied: ["interview", "rejected"],
  acknowledged: ["interview", "rejected"],
  screening: ["interview", "rejected"],
  assessment: ["interview", "rejected"],
  interview: ["offer", "rejected"],
};

let apiBase = "http://127.0.0.1:8765";

const show = (text, warn = false) => {
  const node = $("message");
  node.textContent = text;
  node.hidden = !text;
  node.classList.toggle("warn", warn);
};

async function api(path, options) {
  const response = await fetch(`${apiBase}${path}`, options);
  if (!response.ok) throw new Error(`${response.status} from ${path}`);
  return response.json();
}

function jobCard(job) {
  const app = job.application;
  const item = document.createElement("li");
  item.className = "job";

  const heading = document.createElement("h3");
  const link = document.createElement("a");
  link.href = job.apply_url;
  link.target = "_blank";
  link.rel = "noreferrer";
  link.textContent = job.title;
  heading.append(link);

  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = [job.company, (job.locations || []).join(" / "), job.source]
    .filter(Boolean)
    .join(" · ");

  const row = document.createElement("div");
  row.className = "row";
  const badge = document.createElement("span");
  badge.className = `badge ${app.status}`;
  badge.textContent = app.status;
  row.append(badge);

  for (const next of NEXT_STATUS[app.status] || []) {
    const button = document.createElement("button");
    button.textContent = next;
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        await api(`/applications/${app.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: next }),
        });
        await refresh();
      } catch (error) {
        show(String(error.message || error), true);
        button.disabled = false;
      }
    });
    row.append(button);
  }

  item.append(heading, meta, row);
  return item;
}

async function refresh() {
  const params = new URLSearchParams();
  const status = $("status").value;
  if (status === "open") params.set("open_only", "true");
  else if (status) params.set("status", status);
  if ($("search").value.trim()) params.set("search", $("search").value.trim());

  try {
    const [jobs, stats] = await Promise.all([
      api(`/jobs?${params}`),
      api("/stats"),
    ]);
    show("");

    $("stats").textContent =
      `${stats.total_jobs} tracked · ${stats.applied_this_week} applied this week`;

    const list = $("jobs");
    list.textContent = "";
    if (!jobs.length) {
      const empty = document.createElement("li");
      empty.className = "empty";
      empty.textContent = "Nothing here yet. Open a job posting and hit Save.";
      list.append(empty);
      return;
    }
    for (const job of jobs) list.append(jobCard(job));
  } catch {
    show("Tracker unreachable. Start it with: uvicorn api.main:app --port 8765", true);
  }
}

$("capture").addEventListener("click", async () => {
  const button = $("capture");
  button.disabled = true;
  button.textContent = "Saving…";
  const result = await chrome.runtime.sendMessage({ type: "capture" });
  button.disabled = false;
  button.textContent = "Save this job";
  if (result && result.ok && result.result.duplicate_warning) {
    show(result.result.duplicate_warning, true);
  } else if (result && !result.ok) {
    show("Could not read this page as a job posting.", true);
  }
  refresh();
});

$("search").addEventListener("input", () => {
  clearTimeout(window.__t);
  window.__t = setTimeout(refresh, 250);
});
$("status").addEventListener("change", refresh);
chrome.runtime.onMessage.addListener((m) => {
  if (m.type === "jobs-changed") refresh();
});

chrome.runtime.sendMessage({ type: "api-base" }).then((base) => {
  if (base) apiBase = base;
  refresh();
});
