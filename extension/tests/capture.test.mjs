// Runs the real content script against real DOMs. The adapters are the most
// brittle part of the system, so they get exercised rather than eyeballed.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";

const SCRIPT = readFileSync(new URL("../content/capture.js", import.meta.url), "utf8");

function capture(html, url = "https://example.com/jobs/1") {
  const dom = new JSDOM(html, { url, runScripts: "outside-only" });
  // Round-trip through JSON: objects from the jsdom realm are not
  // reference-equal to this realm's, and this is exactly what happens to the
  // payload on its way to the API anyway.
  return JSON.parse(JSON.stringify(dom.window.eval(SCRIPT)));
}

const jsonLd = (data) =>
  `<script type="application/ld+json">${JSON.stringify(data)}</script>`;

const POSTING = {
  "@context": "https://schema.org",
  "@type": "JobPosting",
  title: "Senior Python Engineer",
  hiringOrganization: { "@type": "Organization", name: "Cohere" },
  jobLocation: {
    "@type": "Place",
    address: { addressLocality: "Toronto", addressRegion: "ON" },
  },
  description: "<p>You will build <b>services</b>.</p>",
};

test("reads a schema.org JobPosting", () => {
  const job = capture(`<html><body>${jsonLd(POSTING)}</body></html>`);
  assert.equal(job.ok, true);
  assert.equal(job.title, "Senior Python Engineer");
  assert.equal(job.company, "Cohere");
  assert.deepEqual(job.locations, ["Toronto, ON"]);
  assert.equal(job.description, "You will build services.", "HTML tags stripped");
});

test("finds the posting inside an @graph array", () => {
  const graph = { "@context": "https://schema.org", "@graph": [{ "@type": "WebSite" }, POSTING] };
  assert.equal(capture(`<html><body>${jsonLd(graph)}</body></html>`).title,
    "Senior Python Engineer");
});

test("a remote posting with no address still gets a location", () => {
  const remote = { ...POSTING, jobLocation: undefined, jobLocationType: "TELECOMMUTE" };
  assert.deepEqual(capture(`<html><body>${jsonLd(remote)}</body></html>`).locations, ["Remote"]);
});

test("malformed JSON-LD does not sink the capture", () => {
  const html = `<html><head><meta property="og:title" content="Data Engineer at Shopify">
    </head><body><script type="application/ld+json">{ not json </script></body></html>`;
  const job = capture(html);
  assert.equal(job.ok, true);
  assert.equal(job.title, "Data Engineer");
  assert.equal(job.company, "Shopify");
});

test("a site adapter fills what JSON-LD lacks", () => {
  const html = `<html><body>
    <div class="posting-headline"><h2>Staff Backend Engineer</h2></div>
    <div class="posting-categories"><span class="location">Vancouver, BC</span></div>
    <div class="section-wrapper">Build the platform.</div>
    ${jsonLd({ ...POSTING, title: "", hiringOrganization: { name: "Clio" } })}
  </body></html>`;
  const job = capture(html, "https://jobs.lever.co/clio/abc");
  assert.equal(job.ats_type, "lever");
  assert.equal(job.title, "Staff Backend Engineer", "adapter wins over empty JSON-LD");
  assert.equal(job.company, "Clio", "company still comes from JSON-LD");
  assert.ok(job.locations.includes("Vancouver, BC"));
});

test("workday is read through its data-automation-id hooks", () => {
  const html = `<html><body>
    <h1 data-automation-id="jobPostingHeader">Machine Learning Engineer</h1>
    <div data-automation-id="locations">Toronto, Ontario</div>
    <div data-automation-id="jobPostingDescription">Join the AI team.</div>
    <meta property="og:site_name" content="RBC">
  </body></html>`;
  const job = capture(html, "https://rbc.wd3.myworkdayjobs.com/en-US/rbc/job/123");
  assert.equal(job.ats_type, "workday");
  assert.equal(job.title, "Machine Learning Engineer");
  assert.deepEqual(job.locations, ["Toronto, Ontario"]);
});

test("an adapter that throws degrades to JSON-LD instead of failing", () => {
  const html = `<html><body>${jsonLd(POSTING)}</body></html>`;
  const job = capture(html, "https://boards.greenhouse.io/cohere/jobs/1");
  assert.equal(job.ok, true);
  assert.equal(job.title, "Senior Python Engineer");
  assert.equal(job.ats_type, "greenhouse");
});

test("title and company split out of a plain page title", () => {
  const job = capture("<html><head><title>Backend Engineer - Jobber | Careers</title></head><body></body></html>");
  assert.equal(job.title, "Backend Engineer");
  assert.equal(job.company, "Jobber");
});

test("a page that is not a posting reports ok:false rather than guessing", () => {
  const job = capture("<html><head><title>Home</title></head><body><p>Hello</p></body></html>");
  assert.equal(job.ok, false);
});

test("duplicate locations collapse and the apply URL is always recorded", () => {
  const dup = { ...POSTING, jobLocation: [POSTING.jobLocation, POSTING.jobLocation] };
  const job = capture(`<html><body>${jsonLd(dup)}</body></html>`, "https://x.test/jobs/7");
  assert.deepEqual(job.locations, ["Toronto, ON"]);
  assert.equal(job.apply_url, "https://x.test/jobs/7");
});

test("a very long description is truncated before it is sent", () => {
  const long = { ...POSTING, description: "x".repeat(50000) };
  assert.equal(capture(`<html><body>${jsonLd(long)}</body></html>`).description.length, 20000);
});

test("canonical url is preferred when the page declares one", () => {
  const html = `<html><head><link rel="canonical" href="https://x.test/canonical">
    </head><body>${jsonLd(POSTING)}</body></html>`;
  assert.equal(capture(html, "https://x.test/jobs/7?utm=abc").canonical_url,
    "https://x.test/canonical");
});
