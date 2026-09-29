// A save button on every job card on LinkedIn and Indeed.
//
// You are browsing a results page anyway; the cheapest possible capture is one
// click on the card you are already looking at. So this injects a star into
// each card, and clicking it saves the posting with its apply link.
//
// What a card can and cannot give you: title, company, location and the job's
// own URL are all there. The description is not — it lives on the detail page.
// So a starred job is saved immediately, and the background worker then fetches
// the detail page to fill the description in. Scoring and tailoring both need
// that description, so a card save that could not be enriched shows up in the
// panel as still needing it rather than being passed off as ready to apply.
//
// Both sites re-render their lists constantly (virtualised scrolling, filter
// changes, infinite scroll), so injection is idempotent and driven by a
// MutationObserver rather than done once on load.

(() => {
  if (window.__jobTrackerCards) return;
  window.__jobTrackerCards = true;

  const MARK = "data-job-tracker";

  const clean = (value) => (value || "").replace(/\s+/g, " ").trim();

  const firstText = (root, selectors) => {
    for (const selector of selectors) {
      const node = root.querySelector(selector);
      const value = clean(node && node.textContent);
      if (value) return value;
    }
    return "";
  };

  // --- site definitions ---------------------------------------------------
  //
  // Several selectors per field on purpose: LinkedIn renames its classes often,
  // and a card that loses its title is a card that silently saves nothing. The
  // stable hooks come first (data attributes), the class names are the fallback.

  const SITES = [
    {
      name: "linkedin",
      matches: (host) => host.includes("linkedin.com"),
      cards: [
        "li[data-occludable-job-id]",
        "div[data-job-id]",
        "li.jobs-search-results__list-item",
        "div.job-card-container",
      ],
      read: (card) => ({
        title: firstText(card, [
          ".job-card-list__title--link",
          ".job-card-list__title",
          "a.job-card-container__link",
          ".artdeco-entity-lockup__title",
          "h3",
        ]),
        company: firstText(card, [
          ".job-card-container__primary-description",
          ".artdeco-entity-lockup__subtitle",
          ".job-card-container__company-name",
          "h4",
        ]),
        location: firstText(card, [
          ".job-card-container__metadata-item",
          ".artdeco-entity-lockup__caption",
          ".job-card-container__metadata-wrapper",
        ]),
        url: cardUrl(card, "a.job-card-container__link, a.job-card-list__title--link, a[href*='/jobs/view/']"),
      }),
      anchor: (card) =>
        card.querySelector(".job-card-container__metadata-wrapper") ||
        card.querySelector(".artdeco-entity-lockup__content") ||
        card,
    },
    {
      name: "indeed",
      matches: (host) => host.includes("indeed.com"),
      cards: ["div.job_seen_beacon", "div[data-jk]", "td.resultContent"],
      read: (card) => ({
        title: firstText(card, [
          "h2.jobTitle span[title]",
          "h2.jobTitle",
          "a.jcs-JobTitle",
          "h2",
        ]),
        company: firstText(card, [
          '[data-testid="company-name"]',
          "span.companyName",
          ".company_location [data-testid='company-name']",
        ]),
        location: firstText(card, [
          '[data-testid="text-location"]',
          "div.companyLocation",
          ".company_location [data-testid='text-location']",
        ]),
        url: cardUrl(card, "h2.jobTitle a, a.jcs-JobTitle, a[data-jk]"),
      }),
      anchor: (card) => card.querySelector("h2.jobTitle") || card,
    },
  ];

  function cardUrl(card, selector) {
    const link = card.querySelector(selector);
    if (link && link.getAttribute("href")) {
      try {
        return new URL(link.getAttribute("href"), location.origin).href;
      } catch {
        return "";
      }
    }
    // Indeed keys every card by `data-jk`, which is enough to rebuild the URL
    // even when the anchor is missing.
    const jk = card.getAttribute("data-jk") || (card.closest("[data-jk]") || {}).getAttribute?.("data-jk");
    if (jk && location.hostname.includes("indeed.com")) {
      return `https://${location.hostname}/viewjob?jk=${jk}`;
    }
    const jobId = card.getAttribute("data-occludable-job-id") || card.getAttribute("data-job-id");
    if (jobId && location.hostname.includes("linkedin.com")) {
      return `https://www.linkedin.com/jobs/view/${jobId}/`;
    }
    return "";
  }

  // Exposed for the tests: reading a card is the brittle part, and it is pure.
  function readCard(card, siteName) {
    const site = SITES.find((s) => s.name === siteName);
    if (!site) return null;
    const read = site.read(card);
    const payload = {
      title: read.title,
      company: read.company,
      locations: read.location ? [read.location] : [],
      apply_url: read.url || location.href,
      source: site.name,
      ats_type: site.name,
    };
    payload.ok = Boolean(payload.title && payload.company);
    return payload;
  }

  window.__jobTrackerReadCard = readCard;

  const site = SITES.find((s) => s.matches(location.hostname));
  if (!site) return;

  // --- the button ---------------------------------------------------------

  function button(card) {
    const element = document.createElement("button");
    element.type = "button";
    element.className = "job-tracker-star";
    element.textContent = "☆ Save";
    element.title = "Save this job to your tracker";
    element.setAttribute("aria-label", "Save this job to your tracker");
    Object.assign(element.style, {
      all: "unset",
      cursor: "pointer",
      display: "inline-block",
      font: "600 12px/1.4 system-ui, sans-serif",
      color: "#0a66c2",
      background: "#eef3f8",
      border: "1px solid #0a66c2",
      borderRadius: "12px",
      padding: "2px 8px",
      margin: "4px 0 0",
    });

    element.addEventListener("click", async (event) => {
      // The whole card is usually a link on both sites.
      event.preventDefault();
      event.stopPropagation();
      if (element.disabled) return;

      const payload = readCard(card, site.name);
      if (!payload || !payload.ok) {
        state(element, "error", "Could not read this card — open it and press Ctrl+Shift+S");
        return;
      }

      state(element, "saving");
      try {
        const answer = await chrome.runtime.sendMessage({ type: "save-card", payload });
        if (!answer || !answer.ok) throw new Error((answer && answer.error) || "save failed");
        state(element, "saved", answer.duplicate ? "Already saved" : "Saved");
      } catch (error) {
        state(element, "error", String((error && error.message) || error));
      }
    });
    return element;
  }

  function state(element, kind, message) {
    const styles = {
      saving: { textContent: "… saving", background: "#eef3f8", color: "#0a66c2" },
      saved: { textContent: "★ Saved", background: "#0a66c2", color: "#ffffff" },
      error: { textContent: "! retry", background: "#fff1f0", color: "#b42318" },
    }[kind];
    element.textContent = styles.textContent;
    element.style.background = styles.background;
    element.style.color = styles.color;
    element.disabled = kind === "saved" || kind === "saving";
    if (message) element.title = message;
  }

  function inject(root) {
    const selector = site.cards.join(",");
    const cards = [
      ...(root.matches && root.matches(selector) ? [root] : []),
      ...(root.querySelectorAll ? root.querySelectorAll(selector) : []),
    ];
    for (const card of cards) {
      if (card.getAttribute(MARK)) continue;
      card.setAttribute(MARK, "1");
      const anchor = site.anchor(card) || card;
      anchor.appendChild(button(card));
    }
  }

  inject(document.body);

  // Both sites swap list contents without a navigation, so watch for it. Nodes
  // are batched through a microtask: LinkedIn can replace hundreds of rows at
  // once, and injecting per mutation record would thrash the page.
  let queued = false;
  const pending = new Set();
  const observer = new MutationObserver((records) => {
    for (const record of records) {
      for (const node of record.addedNodes) {
        if (node.nodeType === 1) pending.add(node);
      }
    }
    if (queued || !pending.size) return;
    queued = true;
    Promise.resolve().then(() => {
      queued = false;
      const nodes = [...pending];
      pending.clear();
      for (const node of nodes) {
        if (node.isConnected) inject(node);
      }
    });
  });
  observer.observe(document.body, { childList: true, subtree: true });
})();
