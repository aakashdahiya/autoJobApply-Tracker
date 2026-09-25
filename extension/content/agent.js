// The actuator. It reads the form, applies the values the API decided on, and
// shows you what it touched. It never clicks submit — that is the whole point
// of the design, and it is what keeps applications reviewable and accounts
// un-banned.
//
// Injected once per tab, then driven by messages from the service worker.

if (!window.__jobTrackerAgent) {
  window.__jobTrackerAgent = true;

  const MARK = "data-jt-id";
  const SKIP_TYPES = new Set(["hidden", "submit", "button", "reset", "image"]);
  let counter = 0;

  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();

  // CSS.escape is missing in some engines and in test harnesses; our own ids
  // never need escaping, but the page's do.
  const esc = (s) =>
    typeof CSS !== "undefined" && CSS.escape
      ? CSS.escape(String(s))
      : String(s).replace(/([^\w-])/g, "\\$1");

  // --- reading the form --------------------------------------------------

  function labelFor(el) {
    const id = el.getAttribute("id");
    if (id) {
      const label = document.querySelector(`label[for="${esc(id)}"]`);
      if (label) return norm(label.textContent);
    }
    const aria = el.getAttribute("aria-labelledby");
    if (aria) {
      const parts = aria
        .split(/\s+/)
        .map((ref) => document.getElementById(ref))
        .filter(Boolean)
        .map((node) => norm(node.textContent));
      if (parts.length) return parts.join(" ");
    }
    if (el.getAttribute("aria-label")) return norm(el.getAttribute("aria-label"));

    const wrapping = el.closest("label");
    if (wrapping) return norm(wrapping.textContent);

    // Workday and friends render the label as a sibling heading rather than a
    // <label>, so walk up a little and take the nearest preceding text.
    let node = el.parentElement;
    for (let depth = 0; node && depth < 4; depth += 1, node = node.parentElement) {
      const candidate = node.querySelector("label, legend, h2, h3, [id$='label']");
      if (candidate && !candidate.contains(el)) {
        const text = norm(candidate.textContent);
        if (text) return text;
      }
    }
    return norm(el.getAttribute("placeholder") || el.getAttribute("name") || "");
  }

  function isVisible(el) {
    if (el.hidden || el.closest("[hidden]")) return false;
    const style = window.getComputedStyle(el);
    return style.display !== "none" && style.visibility !== "hidden";
  }

  function fieldType(el) {
    const tag = el.tagName.toLowerCase();
    if (tag === "textarea") return "textarea";
    if (tag === "select") return "select";
    if (tag === "button" || el.getAttribute("aria-haspopup") === "listbox") return "listbox";
    const type = (el.getAttribute("type") || "text").toLowerCase();
    return type;
  }

  function optionsFor(el, type) {
    if (type === "select") return [...el.options].map((o) => norm(o.textContent)).filter(Boolean);
    if (type === "radio" && el.name) {
      return [...document.getElementsByName(el.name)].map((r) => labelFor(r)).filter(Boolean);
    }
    return []; // a listbox only reveals its options once opened
  }

  function fillable() {
    const nodes = document.querySelectorAll(
      "input, select, textarea, button[aria-haspopup='listbox'], [role='combobox']"
    );
    const seen = new Set();
    const out = [];
    for (const el of nodes) {
      const type = fieldType(el);
      if (SKIP_TYPES.has(type)) continue;
      if (el.disabled || el.readOnly) continue;
      // Not `offsetParent`: that is also null for position:fixed elements,
      // which are perfectly visible and often hold the very fields we want.
      if (!isVisible(el) && type !== "file") continue;
      if (type === "radio") {
        if (seen.has(el.name)) continue;
        seen.add(el.name);
      }
      if (!el.getAttribute(MARK)) el.setAttribute(MARK, `jt${(counter += 1)}`);
      out.push({
        id: el.getAttribute(MARK),
        label: labelFor(el),
        type,
        options: optionsFor(el, type),
        required: el.required || el.getAttribute("aria-required") === "true",
        automation_id: el.getAttribute("data-automation-id") || "",
        current: type === "select" ? norm(el.selectedOptions?.[0]?.textContent) : norm(el.value),
      });
    }
    return out;
  }

  // --- writing to the form -----------------------------------------------

  // React and friends install their own value setter and only notice changes
  // that go through it, so assigning `.value` directly is silently ignored.
  function setNative(el, value) {
    const proto = el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : el instanceof HTMLSelectElement
        ? HTMLSelectElement.prototype
        : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    if (setter) setter.call(el, value);
    else el.value = value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  // Mirrors the matcher in api/autofill.py: exact, then whole words. Never a
  // bare substring — "no" lives inside "visible minority".
  function matchOption(texts, want) {
    const target = norm(want).toLowerCase();
    if (!target) return -1;
    const lower = texts.map((t) => norm(t).toLowerCase());
    let i = lower.indexOf(target);
    if (i !== -1) return i;
    const word = new RegExp(`\\b${target.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`);
    i = lower.findIndex((t) => word.test(t));
    if (i !== -1) return i;
    return lower.findIndex((t) =>
      new RegExp(`\\b${t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`).test(target)
    );
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function pickFromListbox(el, want) {
    el.click();
    for (let tries = 0; tries < 12; tries += 1) {
      await sleep(60);
      const options = [...document.querySelectorAll("[role='option']")].filter(isVisible);
      if (!options.length) continue;
      const index = matchOption(options.map((o) => o.textContent), want);
      if (index === -1) {
        el.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
        return false;
      }
      options[index].click();
      return true;
    }
    return false;
  }

  function outline(el, kind) {
    const colour = { filled: "#2d8a4e", corrected: "#b4530a", review: "#b48a0a" }[kind];
    el.style.outline = `2px solid ${colour}`;
    el.style.outlineOffset = "1px";
    el.setAttribute("data-jt-state", kind);
  }

  async function applyFills(fills) {
    const filled = {};
    const corrected = {};
    const failed = [];

    for (const fill of fills) {
      if (fill.action !== "fill" && fill.action !== "select") continue;
      const el = document.querySelector(`[${MARK}="${esc(fill.field_id)}"]`);
      if (!el || fill.value == null) continue;

      const type = fieldType(el);
      const before = type === "select"
        ? norm(el.selectedOptions?.[0]?.textContent)
        : norm(el.value);

      let ok = true;
      try {
        if (type === "select") {
          const index = matchOption([...el.options].map((o) => o.textContent), fill.value);
          if (index === -1) ok = false;
          else {
            el.selectedIndex = index;
            el.dispatchEvent(new Event("change", { bubbles: true }));
          }
        } else if (type === "radio") {
          const group = [...document.getElementsByName(el.name)];
          const index = matchOption(group.map((r) => labelFor(r)), fill.value);
          if (index === -1) ok = false;
          else group[index].click();
        } else if (type === "checkbox") {
          const want = /^(yes|true)$/i.test(fill.value);
          if (el.checked !== want) el.click();
        } else if (type === "listbox" || el.getAttribute("role") === "combobox") {
          ok = await pickFromListbox(el, fill.value);
        } else {
          setNative(el, fill.value);
        }
      } catch {
        ok = false;
      }

      if (!ok) {
        failed.push({ id: fill.field_id, label: labelFor(el), wanted: fill.value });
        continue;
      }

      // A field that already held something different was *corrected*, which
      // is the normal case on Workday: it parses your resume first, usually
      // wrongly, and this is the diff against what the profile actually says.
      if (before && before !== norm(String(fill.value))) {
        corrected[fill.field_id] = `${before} -> ${fill.value}`;
        outline(el, "corrected");
      } else {
        filled[fill.field_id] = fill.value;
        outline(el, fill.confidence === "high" ? "filled" : "review");
      }
    }

    for (const fill of fills) {
      if (fill.action !== "skip" && fill.action !== "unmatched") continue;
      const el = document.querySelector(`[${MARK}="${esc(fill.field_id)}"]`);
      if (el && (el.required || fill.action === "skip")) outline(el, "review");
    }

    return { filled, corrected, failed };
  }

  async function attachResume(url, filename) {
    const input = document.querySelector("input[type=file]");
    if (!input) return { ok: false, reason: "no file input on this page" };
    try {
      const blob = await (await fetch(url)).blob();
      const file = new File([blob], filename, { type: "application/pdf" });
      const transfer = new DataTransfer();
      transfer.items.add(file);
      input.files = transfer.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
      outline(input, "filled");
      return { ok: true };
    } catch (error) {
      return { ok: false, reason: String(error.message || error) };
    }
  }

  // --- Workday specifics --------------------------------------------------

  function workdayState() {
    const step = norm(
      document.querySelector("[data-automation-id='progressBarActiveStep']")?.textContent ||
        document.querySelector("h2")?.textContent ||
        ""
    );
    const reuse = document.querySelector(
      "[data-automation-id='useMyLastApplication'], [data-automation-id='autofillWithResume']"
    );
    const tenant = location.hostname.split(".")[0];
    return { step, tenant, canReuseLastApplication: Boolean(reuse) };
  }

  function confirmationSeen() {
    // innerText respects visibility and is the better signal in a real browser,
    // but it does not exist without a layout engine.
    const body = document.body;
    const blob = norm((body && (body.innerText || body.textContent)) || "").toLowerCase();
    const phrases = [
      "application submitted",
      "thank you for applying",
      "we have received your application",
      "your application has been submitted",
      "thanks for applying",
      "application received",
    ];
    return phrases.some((p) => blob.includes(p));
  }

  // --- message plumbing ---------------------------------------------------

  // Exposed so the tests can drive the real code rather than a copy of it.
  window.__jobTrackerInternals = {
    fillable, applyFills, attachResume, matchOption, labelFor, confirmationSeen,
    workdayState, setNative,
  };

  if (typeof chrome === "undefined" || !chrome.runtime || !chrome.runtime.onMessage) {
    // Running outside the extension (a test harness). Nothing to listen on.
  } else
  chrome.runtime.onMessage.addListener((message, _sender, respond) => {
    if (message.type === "scan") {
      respond({
        fields: fillable(),
        workday: location.hostname.includes("myworkdayjobs.com") ? workdayState() : null,
        confirmation: confirmationSeen(),
        url: location.href,
      });
      return true;
    }
    if (message.type === "apply") {
      applyFills(message.fills).then(respond);
      return true;
    }
    if (message.type === "attach") {
      attachResume(message.url, message.filename).then(respond);
      return true;
    }
    if (message.type === "confirmation") {
      respond({ confirmation: confirmationSeen() });
      return true;
    }
    return false;
  });
}
