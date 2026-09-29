// Reading Claude's reply back out of claude.ai.
//
// This runs against markup shaped like the real thing rather than the real
// thing, which is the honest limit of what can be tested offline: it pins the
// logic (code fences survive, streaming is respected, a half-written reply is
// not taken as final) but cannot pin selectors against a page that may change.
// That is exactly why the script fails loudly instead of guessing, and why the
// server validates every bullet regardless of what this returns.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";

const SCRIPT = readFileSync(new URL("../content/chat.js", import.meta.url), "utf8");

function load(html) {
  const dom = new JSDOM(`<html><body>${html}</body></html>`, {
    url: "https://claude.ai/chat/abc",
    runScripts: "outside-only",
  });
  dom.window.chrome = { runtime: { onMessage: { addListener: () => {} } } };
  dom.window.eval(SCRIPT);
  return { dom, api: dom.window.__jobTrackerChatInternals };
}

const turn = (inner, streaming) =>
  `<div data-is-streaming="${streaming ? "true" : "false"}">
     <div class="font-claude-message">${inner}</div>
   </div>`;

test("a fenced code block survives being read out of the page", () => {
  const { dom, api } = load(
    turn(`<p>Here's the tailored version:</p><pre><code>{"bullets": [{"fact_id": "f1", "bullet": "Shipped it."}]}</code></pre><p>Let me know!</p>`)
  );
  const text = api.messageText(dom.window.document.querySelector(".font-claude-message"));

  assert.match(text, /```/, "the fence is rebuilt, because the parser looks for it");
  assert.match(text, /"fact_id": "f1"/);
  assert.match(text, /Here's the tailored version/);
});

test("copy buttons and icons inside the message are not read as content", () => {
  const { dom, api } = load(
    turn(`<pre><button>Copy</button><svg><path/></svg><code>{"bullets": []}</code></pre>`)
  );
  const text = api.messageText(dom.window.document.querySelector(".font-claude-message"));
  assert.doesNotMatch(text, /Copy/);
  assert.match(text, /"bullets"/);
});

test("a streaming reply is reported as still streaming", () => {
  const { api } = load(turn("<p>Here's the…</p>", true));
  assert.equal(api.isStreaming(), true);
});

test("a finished reply is not", () => {
  const { api } = load(turn("<p>Done.</p>", false));
  assert.equal(api.isStreaming(), false);
});

test("a visible stop button counts as streaming when there is no flag", () => {
  const { api } = load(
    `<div class="font-claude-message"><p>writing…</p></div>
     <button aria-label="Stop response">stop</button>`
  );
  assert.equal(api.isStreaming(), true);
});

test("the last turn is the one read, not the first", () => {
  const { api } = load(turn("<p>older</p>") + turn("<p>newest</p>"));
  const node = api.find([".font-claude-message"]);
  assert.match(api.messageText(node), /newest/);
});

test("paragraphs stay separated rather than running together", () => {
  const { dom, api } = load(turn("<p>First line.</p><p>Second line.</p>"));
  const text = api.messageText(dom.window.document.querySelector(".font-claude-message"));
  assert.doesNotMatch(text, /First line.Second line/);
});

test("an empty page yields no message rather than throwing", () => {
  const { api } = load("<div>nothing here</div>");
  assert.equal(api.messageText(api.find([".font-claude-message"])), "");
});
