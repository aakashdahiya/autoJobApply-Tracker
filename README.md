# autoJobApply-Tracker

A semi-automated job application system for **Canadian AI, tech and Python roles**: discover
roles, tailor a resume to each one, assist-fill the application form, and track every
outcome — with email triage feeding status back in automatically.

**Current state:** design phase. No code yet. Start with [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md),
then [`docs/ROADMAP.md`](docs/ROADMAP.md) for the build order.

## The one-line version

You browse or the crawler finds jobs → the system scores fit and tailors a resume from a
verified fact bank → the extension fills the application form and you press submit →
Gmail triage watches for replies and moves the application through its states → you get one
digest a day and an instant ping for anything time-sensitive.

## Design principles

1. **Human presses submit.** The system does everything up to the submit button. This is
   what keeps accounts un-banned and applications non-embarrassing.
2. **Never invent a qualification.** Tailoring reorders, re-weights, and rephrases facts
   that are already true. Every rendered bullet traces back to a `fact_id`.
3. **One source of truth per thing.** Resume content lives in `profile.yaml`. Application
   state lives in the database. Google Sheets and Docs are *views*, not storage.
4. **Useful on day three.** The tracker and the capture button ship before any LLM feature.
   Tracking is most of the value; tailoring is the multiplier.
