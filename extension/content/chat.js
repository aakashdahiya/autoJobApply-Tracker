// Drives a claude.ai tab: paste the prompt, wait for the reply, read it back.
//
// This is the most fragile thing in the extension and it is worth saying why.
// claude.ai is someone else's single-page app; its markup is not an interface
// anyone promised to keep stable, and a class name can change between a Tuesday
// and a Wednesday. So nothing here assumes one selector. Each target has a list
// of strategies, ordered from the most stable hook to the most cosmetic, and if
// every one misses, this reports exactly which step failed rather than
// returning something plausible.
//
// That last part is the whole design. A scraper that silently returns the wrong
// text here would put invented work history on a resume you send to employers.
// So the failure mode is a clear error and a fall back to pasting by hand — the
// panel always offers that — and the fact bank on the server rejects anything
// that does not trace to a real bullet even when this script does succeed.

(() => {
  if (window.__jobTrackerChat) return;
  window.__jobTrackerChat = true;

  // --- finding things -------------------------------------------------------

  const COMPOSER = [
    'div[contenteditable="true"].ProseMirror',
    '[data-testid="chat-input"] div[contenteditable="true"]',
    'div[contenteditable="true"][role="textbox"]',
    'div[contenteditable="true"]',
    "textarea",
  ];

  const SEND = [
    'button[aria-label="Send message"]',
    'button[aria-label="Send Message"]',
    'button[data-testid="send-button"]',
    'button[type="submit"]',
  ];

  // Assistant turns. The streaming flag is the useful one: it is how the app
  // tells itself a reply is still arriving.
  const MESSAGE = [
    '[data-is-streaming] .font-claude-message',
    "[data-is-streaming]",
    ".font-claude-message",
    '[data-testid="conversation-turn"]',
    '[data-message-author-role="assistant"]',
  ];

  function find(selectors, root) {
    const scope = root || document;
    for (const selector of selectors) {
      const nodes = scope.querySelectorAll(selector);
      if (nodes.length) return nodes[nodes.length - 1];
    }
    return null;
  }

  function findAll(selectors) {
    for (const selector of selectors) {
      const nodes = document.querySelectorAll(selector);
      if (nodes.length) return [...nodes];
    }
    return [];
  }

  // --- reading the reply ----------------------------------------------------

  // Code blocks matter here: the answer is a fenced JSON block, and taking
  // `textContent` off the whole turn loses the fence. Rebuilding it keeps the
  // parser on the server working against the same shape it expects.
  // textContent, minus the controls the app renders inside the block.
  function codeText(node) {
    const copy = node.cloneNode(true);
    for (const junk of copy.querySelectorAll("button, svg")) junk.remove();
    return copy.textContent || "";
  }

  function messageText(node) {
    if (!node) return "";
    const parts = [];
    const walk = (element) => {
      for (const child of element.childNodes) {
        if (child.nodeType === 3) {
          parts.push(child.nodeValue);
          continue;
        }
        if (child.nodeType !== 1) continue;
        const tag = child.tagName.toLowerCase();
        if (tag === "pre") {
          // The rendered block carries a Copy button and a language label in
          // the same subtree. Taking textContent off the <pre> would paste
          // "Copy" into the JSON, so read the <code> when there is one.
          const code = child.querySelector("code");
          parts.push("\n```\n" + codeText(code || child).trim() + "\n```\n");
          continue;
        }
        if (tag === "button" || tag === "svg") continue; // copy buttons, icons
        walk(child);
        if (tag === "p" || tag === "li" || tag === "div") parts.push("\n");
      }
    };
    walk(node);
    return parts.join("").replace(/\n{3,}/g, "\n\n").trim();
  }

  function isStreaming() {
    const flagged = document.querySelector("[data-is-streaming]");
    if (flagged) {
      const value = flagged.getAttribute("data-is-streaming");
      if (value === "true") return true;
      if (value === "false") return false;
    }
    // No flag to read: a visible stop control means it is still going.
    return Boolean(
      document.querySelector(
        'button[aria-label="Stop response"], button[aria-label="Stop generating"]'
      )
    );
  }

  window.__jobTrackerChatInternals = { messageText, isStreaming, find, findAll };

  // --- writing the prompt ---------------------------------------------------

  function insertPrompt(composer, text) {
    composer.focus();
    if (composer.tagName === "TEXTAREA") {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLTextAreaElement.prototype,
        "value"
      ).set;
      setter.call(composer, text);
      composer.dispatchEvent(new Event("input", { bubbles: true }));
      return true;
    }
    // ProseMirror keeps its own model, so writing innerHTML desynchronises it.
    // An insertText command goes through the editor's own handling.
    const range = document.createRange();
    range.selectNodeContents(composer);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    const ok = document.execCommand("insertText", false, text);
    composer.dispatchEvent(new InputEvent("input", { bubbles: true }));
    return ok || (composer.textContent || "").includes(text.slice(0, 40));
  }

  function submit(composer) {
    const button = find(SEND);
    if (button && !button.disabled) {
      button.click();
      return true;
    }
    const enter = {
      key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true,
    };
    composer.dispatchEvent(new KeyboardEvent("keydown", enter));
    composer.dispatchEvent(new KeyboardEvent("keyup", enter));
    return true;
  }

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  // Done means: not streaming, and the text has stopped changing. The second
  // half matters because the streaming flag can clear a beat before the last
  // chunk lands, and a JSON block truncated one character early parses as
  // nothing at all.
  async function waitForReply({ timeout = 180000, quiet = 1200, before = 0 } = {}) {
    const deadline = Date.now() + timeout;
    let text = "";
    let stableSince = 0;

    while (Date.now() < deadline) {
      await sleep(300);
      const turns = findAll(MESSAGE);
      if (turns.length <= before) continue;

      const current = messageText(turns[turns.length - 1]);
      if (current && current === text) {
        if (!stableSince) stableSince = Date.now();
        if (!isStreaming() && Date.now() - stableSince >= quiet) return text;
      } else {
        text = current;
        stableSince = 0;
      }
    }
    if (text) return text; // timed out mid-stream; the server will judge it
    throw new Error("timed out waiting for a reply");
  }

  async function runPrompt(prompt) {
    const composer = find(COMPOSER);
    if (!composer) {
      throw new Error(
        "could not find the message box on claude.ai — paste the prompt yourself"
      );
    }

    const before = findAll(MESSAGE).length;
    if (!insertPrompt(composer, prompt)) {
      throw new Error("could not type into the message box — paste the prompt yourself");
    }
    await sleep(150);
    submit(composer);

    return waitForReply({ before });
  }

  chrome.runtime.onMessage.addListener((message, _sender, respond) => {
    if (message.type !== "run-chat-prompt") return false;
    runPrompt(message.prompt).then(
      (reply) => respond({ ok: true, reply }),
      (error) => respond({ ok: false, error: String((error && error.message) || error) })
    );
    return true;
  });
})();
