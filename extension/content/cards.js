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
      // The one hook worth trusting: a job link is how the product routes, so
      // it outlives every class name around it.
      link: 'a[href*="/jobs/view/"]',
      cards: [
        "li[data-occludable-job-id]",
        "div[data-job-id]",
        "li.scaffold-layout__list-item",
        "li.jobs-search-results__list-item",
        "div.job-card-container",
        "div.job-card-job-posting-card-wrapper",
      ],
      titles: [
        ".job-card-list__title--link",
        ".job-card-list__title",
        "a.job-card-container__link",
        ".artdeco-entity-lockup__title",
        "h3",
      ],
      companies: [
        ".job-card-container__primary-description",
        ".artdeco-entity-lockup__subtitle",
        ".job-card-container__company-name",
        "h4",
      ],
      locations: [
        ".job-card-container__metadata-item",
        ".artdeco-entity-lockup__caption",
        ".job-card-container__metadata-wrapper",
      ],
      idAttrs: ["data-occludable-job-id", "data-job-id"],
      idUrl: (id) => `https://www.linkedin.com/jobs/view/${id}/`,
      anchor: (card) =>
        card.querySelector(".job-card-container__metadata-wrapper") ||
        card.querySelector(".artdeco-entity-lockup__content") ||
        card,
    },
    {
      name: "indeed",
      matches: (host) => host.includes("indeed.com"),
      link: 'a[href*="jk="], a[data-jk], a.jcs-JobTitle',
      cards: ["div.job_seen_beacon", "div[data-jk]", "td.resultContent", "li div[data-jk]"],
      titles: ["h2.jobTitle span[title]", "h2.jobTitle", "a.jcs-JobTitle", "h2"],
      companies: [
        '[data-testid="company-name"]',
        "span.companyName",
        ".company_location [data-testid='company-name']",
      ],
      locations: [
        '[data-testid="text-location"]',
        "div.companyLocation",
        ".company_location [data-testid='text-location']",
      ],
      idAttrs: ["data-jk"],
      idUrl: (id) => `https://${location.hostname}/viewjob?jk=${id}`,
      anchor: (card) => card.querySelector("h2.jobTitle") || card,
    },
  ];

  // --- finding the repeating unit -----------------------------------------

  // Climb from a job link until the parent holds more than one of them: that
  // parent is the list, so the node below it is one card. Works out the card
  // boundary from the page's own structure rather than from a class name,
  // which is the part that keeps changing.
  function cardFor(link, pattern) {
    let node = link;
    for (let depth = 0; depth < 10 && node.parentElement; depth += 1) {
      const parent = node.parentElement;
      if (parent.querySelectorAll(pattern).length > 1) return node;
      node = parent;
    }
    return node;
  }

  function cardsIn(root, site) {
    const found = new Set();

    // 1. Anchor-driven, the durable path.
    if (site.link) {
      const scope = root.querySelectorAll ? root : document;
      const links = [
        ...(root.matches && root.matches(site.link) ? [root] : []),
        ...scope.querySelectorAll(site.link),
      ];
      for (const link of links) found.add(cardFor(link, site.link));
    }

    // 2. Known containers, for a card whose link has not rendered yet.
    const selector = site.cards.join(",");
    for (const node of [
      ...(root.matches && root.matches(selector) ? [root] : []),
      ...(root.querySelectorAll ? root.querySelectorAll(selector) : []),
    ]) {
      found.add(node);
    }

    // Drop any candidate that contains another: only the innermost is a card.
    const cards = [...found];
    return cards.filter((card) => !cards.some((other) => other !== card && card.contains(other)));
  }

  // --- reading one ---------------------------------------------------------

  // Last resort, when every class name has been renamed: read the card the way
  // you do. Both sites stack title, then company, then location.
  //
  // Lines come from the element tree, not from `innerText`: innerText depends
  // on layout, which makes it both slow and untestable, and the block
  // structure is what actually separates the fields here.
  const BLOCK = new Set([
    "div", "p", "li", "ul", "ol", "section", "article", "header", "footer",
    "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td", "th", "dl", "dt", "dd", "br",
  ]);

  const NOISE =
    /^(easy apply|promoted|viewed|new|save|saved|applied|·|actively reviewing applicants|be an early applicant|\d+ (school )?alumni work here|\d+ (days?|weeks?|hours?|months?) ago)$/i;

  function textLines(card) {
    const lines = [];
    const push = (value) => {
      const line = clean(value);
      if (!line || line.length > 140) return;
      if (NOISE.test(line)) return;
      if (lines[lines.length - 1] === line) return; // repeated for screen readers
      lines.push(line);
    };

    const walk = (element) => {
      let buffer = "";
      for (const child of element.childNodes) {
        if (child.nodeType === 3) {
          buffer += child.nodeValue;
          continue;
        }
        if (child.nodeType !== 1) continue;
        const tag = child.tagName.toLowerCase();
        if (tag === "button" || tag === "svg" || tag === "style" || tag === "script") continue;
        if (BLOCK.has(tag)) {
          push(buffer);
          buffer = "";
          walk(child);
        } else {
          buffer += child.textContent || "";
        }
      }
      push(buffer);
    };

    walk(card);
    return lines;
  }

  function cardUrl(card, site) {
    const link = card.matches(site.link) ? card : card.querySelector(site.link);
    const href = link && link.getAttribute("href");
    if (href) {
      try {
        return new URL(href, location.origin).href;
      } catch {
        /* fall through to the id */
      }
    }
    // Both sites key a card by its job id, which rebuilds the URL even when the
    // anchor has not rendered.
    for (const attr of site.idAttrs) {
      const holder = card.matches(`[${attr}]`) ? card : card.querySelector(`[${attr}]`)
        || card.closest(`[${attr}]`);
      const id = holder && holder.getAttribute(attr);
      if (id) return site.idUrl(id);
    }
    return "";
  }

  // Exposed for the tests: reading a card is the brittle part, and it is pure.
  function readCard(card, siteName) {
    const site = SITES.find((s) => s.name === siteName);
    if (!site || !card) return null;

    let title = firstText(card, site.titles);
    let company = firstText(card, site.companies);
    let place = firstText(card, site.locations);

    // Every class name renamed at once: fall back to reading the card as laid
    // out. Only for the fields still missing, so a partial rename degrades
    // rather than throwing everything away.
    if (!title || !company) {
      const lines = textLines(card);
      title = title || lines[0] || "";
      company = company || lines.find((line) => line !== title) || "";
      place = place || lines.find((line) => line !== title && line !== company) || "";
    }

    const payload = {
      title,
      company,
      locations: place ? [place] : [],
      apply_url: cardUrl(card, site) || location.href,
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
    for (const card of cardsIn(root, site)) {
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
