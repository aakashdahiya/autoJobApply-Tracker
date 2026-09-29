// The description parser. A starred card has no description, so this is what
// decides whether the job can be scored and tailored at all — and it reads
// other people's HTML, in a service worker with no DOM.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";

const SCRIPT = readFileSync(new URL("../background.js", import.meta.url), "utf8");

function internals() {
  const dom = new JSDOM("<html><body></body></html>", {
    url: "https://example.com",
    runScripts: "outside-only",
  });
  // The worker registers listeners at load; stub only what it touches.
  const noop = () => {};
  dom.window.chrome = {
    runtime: { onMessage: { addListener: noop }, onInstalled: { addListener: noop }, sendMessage: noop },
    commands: { onCommand: { addListener: noop } },
    action: { onClicked: { addListener: noop } },
    tabs: { onUpdated: { addListener: noop }, onRemoved: { addListener: noop } },
    storage: { session: {}, local: {} },
    sidePanel: { setPanelBehavior: () => Promise.resolve() },
    notifications: { create: noop },
  };
  dom.window.eval(SCRIPT);
  return dom.window.__jobTrackerInternals;
}

const ld = (data) =>
  `<script type="application/ld+json">${JSON.stringify(data)}</script>`;

test("reads the description out of a schema.org JobPosting", () => {
  const { descriptionFromHtml } = internals();
  const html = `<html><head>${ld({
    "@type": "JobPosting",
    title: "AI Engineer",
    description: "<p>Build <b>RAG</b> pipelines in Python.</p>",
  })}</head><body></body></html>`;

  assert.equal(descriptionFromHtml(html), "Build RAG pipelines in Python.");
});

test("finds the posting inside an @graph", () => {
  const { descriptionFromHtml } = internals();
  const html = ld({
    "@graph": [{ "@type": "WebSite" }, { "@type": "JobPosting", description: "Python and FastAPI." }],
  });
  assert.equal(descriptionFromHtml(html), "Python and FastAPI.");
});

test("a malformed block does not stop the next one", () => {
  const { descriptionFromHtml } = internals();
  const html =
    `<script type="application/ld+json">{ not json </script>` +
    ld({ "@type": "JobPosting", description: "Still found." });
  assert.equal(descriptionFromHtml(html), "Still found.");
});

test("falls back to Indeed's description container", () => {
  const { descriptionFromHtml } = internals();
  const html = `<div id="jobDescriptionText"><p>You will own retrieval.</p></div>`;
  assert.equal(descriptionFromHtml(html), "You will own retrieval.");
});

test("falls back to LinkedIn's description container", () => {
  const { descriptionFromHtml } = internals();
  const html = `<div class="jobs-description__content x"><span>Ship Python services.</span></div>`;
  assert.equal(descriptionFromHtml(html), "Ship Python services.");
});

test("a page with no description returns empty rather than junk", () => {
  const { descriptionFromHtml } = internals();
  assert.equal(descriptionFromHtml("<html><body><h1>Sign in</h1></body></html>"), "");
});

test("a login wall is not mistaken for a description", () => {
  const { descriptionFromHtml } = internals();
  // The job is saved either way; what matters is not claiming a description.
  assert.equal(descriptionFromHtml(`<div id="login">Please sign in to continue</div>`), "");
});

test("entities and whitespace are normalised", () => {
  const { stripTags } = internals();
  assert.equal(stripTags("<p>R&amp;D   team</p>\n<p>2&nbsp;years</p>"), "R&D team 2 years");
});

test("the description is capped before it is sent", () => {
  const { descriptionFromHtml } = internals();
  const html = ld({ "@type": "JobPosting", description: "x".repeat(50000) });
  assert.equal(descriptionFromHtml(html).length, 20000);
});

test("the upload is named the same whichever resume it is", () => {
  // The internal filename carries the company and the shape. Sending that to
  // the employer advertises that you keep a per-company variant.
  const { resumeFilename } = internals();
  const server = { resume_filename: "Aakash_Dahiya_Resume.pdf" };

  assert.equal(resumeFilename(server, "ai_engineer"), "Aakash_Dahiya_Resume.pdf");
  assert.equal(resumeFilename(server, "backend_python"), "Aakash_Dahiya_Resume.pdf");
});

test("a server that sent no filename still produces a sane one", () => {
  const { resumeFilename } = internals();
  assert.equal(resumeFilename({}, "ai_engineer"), "resume-ai_engineer.pdf");
  assert.equal(resumeFilename(null, null), "resume.pdf");
});
