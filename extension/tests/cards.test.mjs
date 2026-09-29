// Reading a results card is the most brittle thing in the extension: LinkedIn
// and Indeed rename classes without notice, and a card that loses its title
// saves nothing while looking like it worked. So the readers get exercised
// against real card markup, including the layouts that lost their nice hooks.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";

const SCRIPT = readFileSync(new URL("../content/cards.js", import.meta.url), "utf8");

function load(html, url) {
  const dom = new JSDOM(`<html><body>${html}</body></html>`, {
    url,
    runScripts: "outside-only",
  });
  // The script needs a chrome global to exist; the button click path is not
  // what these tests exercise.
  dom.window.chrome = { runtime: { sendMessage: async () => ({ ok: true }) } };
  dom.window.eval(SCRIPT);
  return dom;
}

function read(html, url, site) {
  const dom = load(html, url);
  const card = dom.window.document.querySelector("[data-card]");
  return JSON.parse(JSON.stringify(dom.window.__jobTrackerReadCard(card, site)));
}

const LINKEDIN = "https://www.linkedin.com/jobs/search/?keywords=python";
const INDEED = "https://ca.indeed.com/jobs?q=python";

// --- LinkedIn ---------------------------------------------------------------

test("reads a LinkedIn card", () => {
  const job = read(
    `<li data-card data-occludable-job-id="4012345678">
       <div class="artdeco-entity-lockup__content">
         <a class="job-card-container__link" href="/jobs/view/4012345678/">
           <span class="job-card-list__title">Senior AI Engineer</span>
         </a>
         <div class="artdeco-entity-lockup__subtitle">Cohere</div>
         <div class="job-card-container__metadata-item">Toronto, ON (Hybrid)</div>
       </div>
     </li>`,
    LINKEDIN,
    "linkedin"
  );

  assert.equal(job.ok, true);
  assert.equal(job.title, "Senior AI Engineer");
  assert.equal(job.company, "Cohere");
  assert.deepEqual(job.locations, ["Toronto, ON (Hybrid)"]);
  assert.equal(job.apply_url, "https://www.linkedin.com/jobs/view/4012345678/");
  assert.equal(job.source, "linkedin");
});

test("rebuilds the LinkedIn url from the job id when the anchor is missing", () => {
  const job = read(
    `<li data-card data-occludable-job-id="4012345678">
       <div class="artdeco-entity-lockup__content">
         <span class="job-card-list__title">Backend Engineer</span>
         <div class="artdeco-entity-lockup__subtitle">Shopify</div>
       </div>
     </li>`,
    LINKEDIN,
    "linkedin"
  );

  assert.equal(job.apply_url, "https://www.linkedin.com/jobs/view/4012345678/");
  assert.equal(job.ok, true, "a card with no anchor is still savable");
});

test("a LinkedIn card with no company is not saved silently", () => {
  const job = read(
    `<li data-card data-occludable-job-id="1"><span class="job-card-list__title">Engineer</span></li>`,
    LINKEDIN,
    "linkedin"
  );
  assert.equal(job.ok, false, "no company means the save would be junk");
});

// --- Indeed -----------------------------------------------------------------

test("reads an Indeed card", () => {
  const job = read(
    `<div data-card class="job_seen_beacon" data-jk="abc123def456">
       <h2 class="jobTitle"><a href="/viewjob?jk=abc123def456"><span title="Python Developer">Python Developer</span></a></h2>
       <span data-testid="company-name">Wealthsimple</span>
       <div data-testid="text-location">Toronto, ON</div>
     </div>`,
    INDEED,
    "indeed"
  );

  assert.equal(job.ok, true);
  assert.equal(job.title, "Python Developer");
  assert.equal(job.company, "Wealthsimple");
  assert.deepEqual(job.locations, ["Toronto, ON"]);
  assert.equal(job.apply_url, "https://ca.indeed.com/viewjob?jk=abc123def456");
});

test("rebuilds the Indeed url from data-jk when the anchor has no href", () => {
  const job = read(
    `<div data-card class="job_seen_beacon" data-jk="xyz789">
       <h2 class="jobTitle">Data Engineer</h2>
       <span data-testid="company-name">Clio</span>
     </div>`,
    INDEED,
    "indeed"
  );
  assert.equal(job.apply_url, "https://ca.indeed.com/viewjob?jk=xyz789");
});

test("keeps the country domain rather than sending you to indeed.com", () => {
  const job = read(
    `<div data-card class="job_seen_beacon" data-jk="q1"><h2 class="jobTitle">Dev</h2>
     <span data-testid="company-name">Jobber</span></div>`,
    "https://ca.indeed.com/jobs?q=python",
    "indeed"
  );
  assert.match(job.apply_url, /^https:\/\/ca\.indeed\.com\//);
});

// --- injection behaviour ----------------------------------------------------

test("injects exactly one button per card", () => {
  const dom = load(
    `<div class="job_seen_beacon" data-jk="a"><h2 class="jobTitle">A</h2></div>
     <div class="job_seen_beacon" data-jk="b"><h2 class="jobTitle">B</h2></div>`,
    INDEED
  );
  assert.equal(dom.window.document.querySelectorAll(".job-tracker-star").length, 2);
});

test("re-running the script does not double up the buttons", () => {
  const dom = load(
    `<div class="job_seen_beacon" data-jk="a"><h2 class="jobTitle">A</h2></div>`,
    INDEED
  );
  dom.window.eval(SCRIPT);
  dom.window.eval(SCRIPT);
  assert.equal(dom.window.document.querySelectorAll(".job-tracker-star").length, 1);
});

test("cards added by infinite scroll get a button too", async () => {
  const dom = load(
    `<div id="list"><div class="job_seen_beacon" data-jk="a"><h2 class="jobTitle">A</h2></div></div>`,
    INDEED
  );
  const { document } = dom.window;

  const added = document.createElement("div");
  added.className = "job_seen_beacon";
  added.setAttribute("data-jk", "b");
  added.innerHTML = `<h2 class="jobTitle">B</h2>`;
  document.getElementById("list").appendChild(added);

  // The observer batches through a microtask, then jsdom delivers on a macrotask.
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(document.querySelectorAll(".job-tracker-star").length, 2);
});

test("does nothing on a site it does not know", () => {
  const dom = load(
    `<div class="job_seen_beacon" data-jk="a"><h2 class="jobTitle">A</h2></div>`,
    "https://example.com/jobs"
  );
  assert.equal(dom.window.document.querySelectorAll(".job-tracker-star").length, 0);
});

// --- surviving a rename -----------------------------------------------------
//
// The first version of this matched LinkedIn's class names and found nothing on
// a real results page, because those names had moved. These fix the behaviour
// that actually matters: the card is located from the job link, which is the
// product's own routing and does not get renamed.

const LINKEDIN_RENAMED = `
  <ul class="totally-new-list-class">
    <li class="whatever-they-call-it-now">
      <div><a href="/jobs/view/4012345678/"><strong>AI Engineer</strong></a>
      <div>Sun Life</div><div>Toronto, ON</div></div>
    </li>
    <li class="whatever-they-call-it-now">
      <div><a href="/jobs/view/4099999999/"><strong>Python Developer</strong></a>
      <div>Lorven Technologies Inc.</div><div>Toronto, ON</div></div>
    </li>
  </ul>`;

test("finds LinkedIn cards when every class name has changed", () => {
  const dom = load(LINKEDIN_RENAMED, LINKEDIN);
  assert.equal(
    dom.window.document.querySelectorAll(".job-tracker-star").length,
    2,
    "cards are located from the job link, not from class names"
  );
});

test("reads a renamed LinkedIn card by its layout", () => {
  const dom = load(LINKEDIN_RENAMED, LINKEDIN);
  const link = dom.window.document.querySelector('a[href*="/jobs/view/4012345678"]');
  const card = link.closest("li");
  const job = JSON.parse(JSON.stringify(dom.window.__jobTrackerReadCard(card, "linkedin")));

  assert.equal(job.ok, true);
  assert.equal(job.title, "AI Engineer");
  assert.equal(job.company, "Sun Life");
  assert.equal(job.apply_url, "https://www.linkedin.com/jobs/view/4012345678/");
});

test("the list itself is never mistaken for a card", () => {
  const dom = load(LINKEDIN_RENAMED, LINKEDIN);
  const list = dom.window.document.querySelector("ul");
  assert.equal(
    list.getAttribute("data-job-tracker"),
    null,
    "a container holding several job links is the list, not a card"
  );
});

test("noise on the card is not read as the company", () => {
  const dom = load(
    `<li><div><a href="/jobs/view/1"><strong>AI Engineer</strong></a>
     <div>Easy Apply</div><div>Promoted</div><div>Guidepoint</div>
     <div>Greater Toronto Area, Canada (Hybrid)</div></div></li>`,
    LINKEDIN
  );
  const card = dom.window.document.querySelector("li");
  const job = JSON.parse(JSON.stringify(dom.window.__jobTrackerReadCard(card, "linkedin")));
  assert.equal(job.company, "Guidepoint", "LinkedIn's badges are chrome, not the employer");
});

test("finds Indeed cards when the classes have changed", () => {
  const dom = load(
    `<ul>
       <li><a href="/viewjob?jk=aaa111"><span>Python Developer</span></a><div>Wealthsimple</div></li>
       <li><a href="/viewjob?jk=bbb222"><span>Data Engineer</span></a><div>Clio</div></li>
     </ul>`,
    INDEED
  );
  assert.equal(dom.window.document.querySelectorAll(".job-tracker-star").length, 2);
});
