// Runs in the page on demand, reads the posting, returns it to the service
// worker. Never mutates the page and never touches a network request of its
// own: this is your own browser session, reading what you are already looking
// at. That is what keeps the whole approach inside every job board's terms.
//
// Strategy, in order:
//   1. schema.org JobPosting JSON-LD. Most boards emit it, and it is the
//      cleanest, least brittle source there is.
//   2. A site-specific adapter for the fields JSON-LD missed or got wrong.
//   3. Open Graph and <title>, so an unknown careers page still captures.

(() => {
  const text = (selector, root = document) => {
    const node = root.querySelector(selector);
    return node ? node.textContent.replace(/\s+/g, " ").trim() : "";
  };

  const meta = (property) => {
    const node =
      document.querySelector(`meta[property="${property}"]`) ||
      document.querySelector(`meta[name="${property}"]`);
    return node ? node.content.trim() : "";
  };

  // Tags become a space so block elements do not weld words together, then
  // the space before punctuation is tidied away again — otherwise every
  // JSON-LD description arrives as "...build services ." and that text later
  // feeds keyword extraction.
  const stripTags = (html) =>
    html
      ? html
          .replace(/<[^>]+>/g, " ")
          .replace(/\s+/g, " ")
          .replace(/\s+([.,;:!?)\]])/g, "$1")
          .replace(/([(\[])\s+/g, "$1")
          .trim()
      : "";

  // --- 1. JSON-LD --------------------------------------------------------
  const fromJsonLd = () => {
    const blocks = document.querySelectorAll('script[type="application/ld+json"]');
    for (const block of blocks) {
      let parsed;
      try {
        parsed = JSON.parse(block.textContent);
      } catch {
        continue; // a malformed block is not a reason to give up on the page
      }
      const candidates = Array.isArray(parsed) ? parsed : [parsed, ...(parsed["@graph"] || [])];
      for (const item of candidates) {
        if (!item || item["@type"] !== "JobPosting") continue;

        const org = item.hiringOrganization || {};
        const places = []
          .concat(item.jobLocation || [])
          .map((place) => {
            const address = (place && place.address) || {};
            return [address.addressLocality, address.addressRegion]
              .filter(Boolean)
              .join(", ");
          })
          .filter(Boolean);

        if (item.jobLocationType === "TELECOMMUTE" && !places.length) {
          places.push("Remote");
        }

        const salary = item.baseSalary && item.baseSalary.value;
        return {
          title: (item.title || "").trim(),
          company: (typeof org === "string" ? org : org.name || "").trim(),
          locations: places,
          description: stripTags(item.description || ""),
          salary: salary && (salary.minValue || salary.maxValue)
            ? [salary.minValue, salary.maxValue].filter(Boolean).join("–")
            : "",
          source: "json-ld",
        };
      }
    }
    return null;
  };

  // --- 2. Site adapters --------------------------------------------------
  const ADAPTERS = [
    {
      ats: "greenhouse",
      matches: (h) => h.includes("greenhouse.io") || !!document.querySelector("#grnhse_app"),
      read: () => ({
        title: text(".app-title") || text("h1"),
        company: text(".company-name").replace(/^at\s+/i, "") || text(".company-header h1"),
        locations: [text(".location") || text(".job__location")],
        description: text("#content") || text(".job__description"),
      }),
    },
    {
      ats: "lever",
      matches: (h) => h.includes("lever.co"),
      read: () => ({
        title: text(".posting-headline h2") || text("h2"),
        company: text(".main-header-logo img")
          || (document.querySelector(".main-header-logo img") || {}).alt
          || "",
        locations: [text(".posting-categories .location") || text(".workplaceTypes")],
        description: text(".section-wrapper"),
      }),
    },
    {
      ats: "ashby",
      matches: (h) => h.includes("ashbyhq.com"),
      read: () => ({
        title: text("h1"),
        locations: [text('[class*="location"]')],
        description: text('[class*="descriptionText"]') || text("main"),
      }),
    },
    {
      ats: "workday",
      matches: (h) => h.includes("myworkdayjobs.com"),
      read: () => ({
        title: text('[data-automation-id="jobPostingHeader"]') || text("h1"),
        locations: [text('[data-automation-id="locations"]')],
        description: text('[data-automation-id="jobPostingDescription"]'),
      }),
    },
    {
      ats: "smartrecruiters",
      matches: (h) => h.includes("smartrecruiters.com"),
      read: () => ({
        title: text("h1"),
        locations: [text('[itemprop="jobLocation"]') || text(".job-location")],
        description: text('[itemprop="description"]') || text("main"),
      }),
    },
    {
      ats: "workable",
      matches: (h) => h.includes("workable.com"),
      read: () => ({
        title: text("h1"),
        locations: [text('[data-ui="job-location"]')],
        description: text('[data-ui="job-description"]') || text("main"),
      }),
    },
    {
      ats: "linkedin",
      matches: (h) => h.includes("linkedin.com"),
      read: () => ({
        title: text(".top-card-layout__title") || text(".job-details-jobs-unified-top-card__job-title") || text("h1"),
        company: text(".topcard__org-name-link") || text(".job-details-jobs-unified-top-card__company-name"),
        locations: [text(".topcard__flavor--bullet") || text(".job-details-jobs-unified-top-card__bullet")],
        description: text(".description__text") || text(".jobs-description__content"),
      }),
    },
    {
      ats: "indeed",
      matches: (h) => h.includes("indeed.com"),
      read: () => ({
        title: text('[data-testid="jobsearch-JobInfoHeader-title"]') || text("h1"),
        company: text('[data-testid="inlineHeader-companyName"]') || text('[data-company-name]'),
        locations: [text('[data-testid="inlineHeader-companyLocation"]')],
        description: text("#jobDescriptionText"),
      }),
    },
  ];

  // --- 3. Last resort ----------------------------------------------------
  const fromPage = () => {
    // "Senior Python Engineer - Cohere" / "Data Engineer at Shopify | Jobs"
    const raw = (meta("og:title") || document.title || "").split("|")[0];
    const split = raw.split(/\s+[-–—]\s+|\s+\bat\b\s+/i);
    return {
      title: (split[0] || "").trim(),
      company: (split[1] || meta("og:site_name") || "").trim(),
      locations: [],
      description: meta("og:description") || text("main") || "",
    };
  };

  const host = location.hostname;
  const adapter = ADAPTERS.find((a) => {
    try {
      return a.matches(host);
    } catch {
      return false;
    }
  });

  let fromAdapter = {};
  if (adapter) {
    try {
      fromAdapter = adapter.read() || {};
    } catch {
      fromAdapter = {}; // a changed selector degrades to JSON-LD, never throws
    }
  }

  const jsonLd = fromJsonLd() || {};
  const page = fromPage();

  const pick = (...values) => values.find((v) => v && String(v).trim()) || "";
  const locations = []
    .concat(fromAdapter.locations || [], jsonLd.locations || [], page.locations || [])
    .map((l) => (l || "").trim())
    .filter(Boolean);

  const payload = {
    title: pick(fromAdapter.title, jsonLd.title, page.title),
    company: pick(fromAdapter.company, jsonLd.company, page.company),
    locations: [...new Set(locations)],
    description: pick(fromAdapter.description, jsonLd.description, page.description).slice(0, 20000),
    salary: pick(jsonLd.salary),
    apply_url: location.href,
    canonical_url: (document.querySelector('link[rel="canonical"]') || {}).href || location.href,
    ats_type: adapter ? adapter.ats : null,
    source: adapter ? adapter.ats : jsonLd.title ? "json-ld" : "page",
  };

  payload.ok = Boolean(payload.title && payload.company);
  return payload;
})();
