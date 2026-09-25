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
// Which application the next fill belongs to, so a confirmation page can move
// the right row. Set by "apply" on a job card.
let currentApplicationId = null;

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

  if (["discovered", "scored", "tailored", "ready"].includes(app.status)) {
    const apply = document.createElement("button");
    apply.textContent = "apply";
    apply.title = "Open the posting and target the next fill at this application";
    apply.addEventListener("click", async () => {
      currentApplicationId = app.id;
      await chrome.tabs.create({ url: job.apply_url, active: true });
      show(`Targeting ${job.title} — open the form, then press "Fill this form".`);
    });
    row.append(apply);
  }

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

function summarise(s) {
  const bits = [`${s.filled} filled`];
  if (s.corrected) bits.push(`${s.corrected} corrected`);
  if (s.skipped) bits.push(`${s.skipped} left for you`);
  if (s.unmatched) bits.push(`${s.unmatched} unmatched`);
  if (s.failed && s.failed.length) bits.push(`${s.failed.length} failed`);
  if (s.attached && s.attached.ok) bits.push("resume attached");
  let text = `${bits.join(", ")}. Review, then submit yourself.`;
  if (s.reuseOffered) {
    text += " Workday offers to reuse your last application here — that is faster and more accurate.";
  }
  return text;
}

$("fill").addEventListener("click", async () => {
  const button = $("fill");
  button.disabled = true;
  button.textContent = "Filling…";
  const result = await chrome.runtime.sendMessage({
    type: "fill",
    shape: $("shape").value,
    applicationId: currentApplicationId,
  });
  button.disabled = false;
  button.textContent = "Fill this form";
  if (!result || !result.ok) {
    show((result && result.error) || "Could not fill this page.", true);
    return;
  }
  show(summarise(result.summary), Boolean(result.summary.failed.length));
});

$("shape").addEventListener("change", () => {
  chrome.storage.sync.set({ shape: $("shape").value });
});

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

chrome.storage.sync.get("shape").then(({ shape }) => {
  if (shape) $("shape").value = shape;
});

chrome.runtime.sendMessage({ type: "api-base" }).then((base) => {
  if (base) apiBase = base;
  refresh();
});
