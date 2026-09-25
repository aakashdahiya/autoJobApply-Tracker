// Drives the real fill engine against real DOMs. This is the part that touches
// other people's pages, so it is tested rather than trusted.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";

const SCRIPT = readFileSync(new URL("../content/agent.js", import.meta.url), "utf8");

function load(html, url = "https://example.com/apply") {
  const dom = new JSDOM(`<html><body>${html}</body></html>`, { url, runScripts: "outside-only" });
  dom.window.eval(SCRIPT);
  return { dom, win: dom.window, api: dom.window.__jobTrackerInternals };
}

const byLabel = (fields, label) => fields.find((f) => f.label.includes(label));
// Objects from the jsdom realm are not reference-equal to this realm's, which
// deepStrictEqual cares about and we do not.
const plain = (value) => JSON.parse(JSON.stringify(value));

// --- reading the form -------------------------------------------------------

test("finds fields and their labels from label[for], aria-label and wrapping labels", () => {
  const { api } = load(`
    <label for="fn">First Name</label><input id="fn">
    <input aria-label="Email Address">
    <label>Phone <input name="phone"></label>
    <input type="hidden" name="csrf">
  `);
  const fields = api.fillable();
  assert.equal(fields.length, 3, "the hidden field is not offered");
  assert.ok(byLabel(fields, "First Name"));
  assert.ok(byLabel(fields, "Email Address"));
  assert.ok(byLabel(fields, "Phone"));
});

test("reads a Workday field whose label is a sibling rather than a <label>", () => {
  const { api } = load(`
    <div><h3>Legal Name</h3>
      <input data-automation-id="legalNameSection_firstName">
    </div>
  `);
  const [field] = api.fillable();
  assert.equal(field.automation_id, "legalNameSection_firstName");
  assert.ok(field.label.includes("Legal Name"));
});

test("select options are read, and a radio group appears once", () => {
  const { api } = load(`
    <label for="s">Country</label>
    <select id="s"><option>Canada</option><option>United States</option></select>
    <label for="r1">Yes</label><input type="radio" id="r1" name="auth">
    <label for="r2">No</label><input type="radio" id="r2" name="auth">
  `);
  const fields = api.fillable();
  assert.deepEqual(plain(byLabel(fields, "Country").options), ["Canada", "United States"]);
  assert.equal(fields.filter((f) => f.type === "radio").length, 1, "one entry per group");
});

test("display:none fields are skipped but position:fixed ones are not", () => {
  const { api } = load(`
    <label for="a">Visible</label><input id="a" style="position:fixed">
    <label for="b">Hidden</label><input id="b" style="display:none">
  `);
  const labels = api.fillable().map((f) => f.label);
  assert.ok(labels.some((l) => l.includes("Visible")));
  assert.ok(!labels.some((l) => l.includes("Hidden")));
});

// --- the option matcher -----------------------------------------------------

test("matchOption will not match inside a word", () => {
  const options = ["Yes", "No", "Prefer not to answer"];
  const { api } = load("");
  assert.equal(api.matchOption(options, "Yes"), 0);
  assert.equal(api.matchOption(["Yes, I am authorized", "No"], "yes"), 0);
  assert.equal(api.matchOption(options, "visible minority"), -1, "'no' is inside 'minority'");
});

// --- writing ----------------------------------------------------------------

test("text fields are set through the native setter and fire input+change", async () => {
  const { api, win } = load(`<label for="a">First Name</label><input id="a">`);
  const events = [];
  win.document.getElementById("a").addEventListener("input", () => events.push("input"));
  win.document.getElementById("a").addEventListener("change", () => events.push("change"));

  const [field] = api.fillable();
  const result = await api.applyFills([
    { field_id: field.id, value: "Aakash", action: "fill", confidence: "high" },
  ]);

  assert.equal(win.document.getElementById("a").value, "Aakash");
  assert.deepEqual(events, ["input", "change"], "React only notices dispatched events");
  assert.equal(Object.keys(result.filled).length, 1);
});

test("a select is moved to the matching option", async () => {
  const { api, win } = load(`
    <label for="s">Country</label>
    <select id="s"><option>United States</option><option>Canada</option></select>
  `);
  const [field] = api.fillable();
  await api.applyFills([{ field_id: field.id, value: "Canada", action: "select", confidence: "high" }]);
  assert.equal(win.document.getElementById("s").value, "Canada");
});

test("a pre-filled wrong value is recorded as a correction, not a fill", async () => {
  // Workday parses your resume first and usually gets something wrong; the
  // diff against the profile is the whole point of the adapter.
  const { api } = load(`<label for="a">First Name</label><input id="a" value="Akash">`);
  const [field] = api.fillable();
  const result = await api.applyFills([
    { field_id: field.id, value: "Aakash", action: "fill", confidence: "high" },
  ]);
  assert.deepEqual(plain(result.corrected), { [field.id]: "Akash -> Aakash" });
  assert.deepEqual(plain(result.filled), {});
});

test("a value with no matching option is reported as failed, not forced", async () => {
  const { api, win } = load(`
    <label for="s">Gender</label><select id="s"><option>Alpha</option><option>Beta</option></select>
  `);
  const [field] = api.fillable();
  const result = await api.applyFills([
    { field_id: field.id, value: "Man", action: "select", confidence: "medium" },
  ]);
  assert.equal(result.failed.length, 1);
  assert.equal(win.document.getElementById("s").value, "Alpha", "left where it was");
});

test("it never touches a submit button", async () => {
  const { api, win } = load(`
    <label for="a">First Name</label><input id="a">
    <button type="submit" id="go">Submit application</button>
  `);
  let clicked = false;
  win.document.getElementById("go").addEventListener("click", () => { clicked = true; });

  const fields = api.fillable();
  assert.ok(!fields.some((f) => f.id === "go"), "a submit button is not a fillable field");
  await api.applyFills(fields.map((f) => ({
    field_id: f.id, value: "x", action: "fill", confidence: "high",
  })));
  assert.equal(clicked, false);
});

test("filled fields are outlined so you can see what was touched", async () => {
  const { api, win } = load(`<label for="a">First Name</label><input id="a">`);
  const [field] = api.fillable();
  await api.applyFills([{ field_id: field.id, value: "Aakash", action: "fill", confidence: "high" }]);
  assert.equal(win.document.getElementById("a").getAttribute("data-jt-state"), "filled");
});

test("skipped and unmatched required fields are flagged for review", async () => {
  const { api, win } = load(`<label for="a">Why this company?</label><textarea id="a" required></textarea>`);
  const [field] = api.fillable();
  await api.applyFills([{ field_id: field.id, value: null, action: "skip", confidence: "low" }]);
  assert.equal(win.document.getElementById("a").getAttribute("data-jt-state"), "review");
});

// --- Workday and confirmation ----------------------------------------------

test("workday state reports the step and whether the last application can be reused", () => {
  const { api } = load(`
    <div data-automation-id="progressBarActiveStep">My Experience</div>
    <button data-automation-id="useMyLastApplication">Use My Last Application</button>
  `, "https://rbc.wd3.myworkdayjobs.com/en-US/rbc/job/1/apply");
  const state = api.workdayState();
  assert.equal(state.step, "My Experience");
  assert.equal(state.tenant, "rbc");
  assert.equal(state.canReuseLastApplication, true);
});

test("confirmation phrases are detected, and ordinary pages are not", () => {
  assert.equal(load("<p>Thank you for applying to Cohere.</p>").api.confirmationSeen(), true);
  assert.equal(load("<p>Your application has been submitted.</p>").api.confirmationSeen(), true);
  assert.equal(load("<p>Apply now for this role.</p>").api.confirmationSeen(), false);
});
