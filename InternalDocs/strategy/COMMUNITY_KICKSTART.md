# Agentic FinSearch — Community Kickstart Plan (v2)

**Date:** 2026-05-26
**Status:** Draft — awaiting audience decision (Step 0) before execution
**Owner:** FlyMiss
**Supersedes:** `COMMUNITY_KICKSTART_LEGACY.md` (v1, acquisition-lens draft, kept for provenance)
**Related:** `POSITIONING_STRATEGY.md` (Approach C ecosystem), `ARKSIM_USER_STORY.md` (benchmark evidence), central-db `projects/finsearch/project.md`
**Source inspiration:** Aberdour, M. (2007) "Achieving Quality in Open Source Software," *IEEE Software*; Bahamdain, S.S. (2015) "Open Source Software (OSS) Quality Assurance: A Survey Paper," *Procedia Computer Science* 56. Both in `Materials/`.

> **What changed from v1 → v2.** v1 framed the Discord through an *acquisition* lens (find the right people, onboard, engage). v2 keeps all of that operational substance but reframes the community as a *quality-production engine* — because for FinSearch, quality (numerical accuracy, zero hallucination, XBRL verification) **is** the moat, and in open source the community is the mechanism that produces quality. New material: Section 2 (Quality Engine framing + cathedral-rigor caution), the roles-as-progression-ladder rewrite in Section 6, Section 7 (Defect Lifecycle), and an onion-health metric in Section 10.

---

## 1. Strategic Context

Agentic FinSearch is transitioning from a research artifact (Columbia SecureFinAI Lab, ICAIF paper, JPM Award) to a self-sustaining product. Approach C (Columbia-anchored ecosystem) is the chosen strategy: build proof infrastructure, not features. The community is one of four resource flywheels — students, data providers, cloud sponsors, companies — not a consumer growth funnel.

**Current state:**
- Discord exists, basic channels, internal team members only
- Chrome extension is listed on Web Store but set to "Unlisted" visibility (deep link works, public search does not surface it)
- User manual exists but is stamped "INTERNAL USE ONLY"
- ICAIF paper published, JPM Faculty Research Award won, NYT + XBRL International press coverage exists
- Layer 1 (numbers + ratios validation) shipped; 91.7% accuracy vs Perplexity 41.7% on the 24-question benchmark

**Constraints:**
- Yahoo Finance ToS prohibits commercial data redistribution
- Columbia IP ownership unclarified — blocks monetization paths
- LLM token burn rate is high — every active user is a real cost against a lab budget
- "Need to acquire first group of loyal users/testers" is an open issue

**Implication for community building:** Optimize for signal density (right people producing evidence) over headcount. Most consumer-SaaS community advice does not apply.

---

## 2. Community as a Quality Engine (Framing)

The OSS-quality literature (Aberdour 2007; Bahamdain 2015) makes one claim that should reorganize how we think about this work: **in open source, the community is not a marketing channel bolted onto the product — it is the infrastructure that produces software quality.** Aberdour's central finding is that "creating a sustainable community should be a project's key objective" and that "the system and the community must coevolve."

This matters more for FinSearch than for a typical project because **quality is our entire moat.** Our differentiator is not features — it is 91.7% vs 41.7%, zero hallucination, XBRL-verified numbers. So community-building and the core mission are the same activity, not competing priorities.

### 2.1 The Onion Model

A sustainable OSS community is concentric, with members migrating *inward* over time via earned merit:

```
        ┌─────────────────────────────┐
        │           Users              │   use the product
        │   ┌─────────────────────┐    │
        │   │    Bug reporters     │    │   test it, report wrong answers
        │   │  ┌───────────────┐   │    │
        │   │  │ Contributing  │   │    │   add features / MCP servers, fix bugs
        │   │  │  developers   │   │    │
        │   │  │  ┌─────────┐  │   │    │
        │   │  │  │  Core   │  │   │    │   small; owns roadmap, integrates code,
        │   │  │  │  team   │  │   │    │   maintains the release cycle
        │   │  │  └─────────┘  │   │    │
        │   │  └───────────────┘   │    │
        │   └─────────────────────┘    │
        └─────────────────────────────┘
```

This is the **same shape** as the Approach C ecosystem diagram in `POSITIONING_STRATEGY.md`, but organized by *role in producing quality* rather than by *commercial relationship*. The two axes compose: a member has a contribution-role (onion layer) **and** a commercial-relationship (Community / Academic / Data Partner / Sponsor). Progression inward is itself the engagement mechanic — "social status is determined not by what you control but by what you give away" (Aberdour, citing Raymond).

### 2.2 Why this is unusually literal for FinSearch

Both papers report that in OSS, **the user base performs the bulk of system testing** — finding more bugs across more real-world scenarios than any internal team or test suite can ("given enough eyeballs, all bugs are shallow"; Apache was tested almost exclusively by its users).

Two facts make this directly load-bearing for us:

1. **CI tests are disabled** (`RUN_TESTS=false` — see `project.md` workflow gaps). A community of financial bug-reporters is a partial substitute for formal test infrastructure we have not built, *and* a feedstock for the QA benchmark and ArkSim regression work that anchors our positioning.
2. **The product already ships a Validate button + per-claim verification UI.** A user who clicks Validate and sees a wrong number is performing black-box testing and producing a defect report in one action. So "I found a question where FinSearch was wrong" should be the **highest-status contribution** in the community — it directly improves the headline metric. Make finding accuracy bugs prestigious, never embarrassing.

### 2.3 What we deliberately KEEP from cathedral-style development

The papers are honest about what OSS typically *drops* vs. closed-source (Aberdour Table 1): documented methodology, formal risk assessment, measurable goals, early defect discovery, planning. Bahamdain lists OSS cons including **"no single responsibility for problems"** and **"version proliferation."**

For a financial-accuracy product positioned as **certification infrastructure**, where a wrong number is a liability and the professor explicitly wants certification-grade rigor, "no single responsibility for problems" is a *disqualifying* property, not an acceptable tradeoff. FinSearch must be a **hybrid**:

| Harness from the bazaar (community) | Retain from the cathedral (lab) |
|---|---|
| Many testers, many real scenarios | A documented, public roadmap |
| Fast bug-finding via the Validate button | The QA benchmark as a formal quality gate |
| Contributor energy on MCP servers | Measurable accuracy goals, tracked |
| Peer review by outsiders | A single accountable owner per release |
| Rapid release cycle | Regression suites (ArkSim) — sponsor-funded |

> **Operating principle:** the community produces *coverage*; the lab produces *rigor*. Neither alone yields trustworthy financial AI. Aberdour notes the highest-quality OSS projects (e.g., Mozilla) kept dedicated test teams and nightly regression precisely because sponsorship allowed it — which is exactly what the Approach C sponsor tier is meant to fund.

---

## 3. Step 0 — Audience Decision (BLOCKER)

Pick one primary audience before opening the Discord. Channel structure, content cadence, and recruiting channels all derive from this choice.

| Framing | Primary members | Hook | Unique asset |
|---|---|---|---|
| **A. Verified Numbers** | Equity analysts, finance students | 91.7% benchmark | XBRL pipeline, SEC EDGAR MCP |
| **B. Quant Screener** | Crypto / technical traders | NL exchange-wide scans | TradingView MCP |
| **C. Build on FinSearch** | MCP authors, capstone teams | Plug-in architecture | Open MCP layer |
| **D. Trusted Financial AI** | Compliance officers, fiduciaries, value investors | Air-gapped + zero hallucination + Columbia/JPM/NYT credibility | Air-gapped deploy + Buffet Agent + Columbia anchor |

**Recommendation:** D primary, A as on-ramp. B and C become sub-channels later when 3+ members organically push them.

**Decision required:** Lock the audience before any other step.

---

## 4. Pre-Launch Blockers

All of these must ship before flipping the Chrome Web Store to public or opening the Discord beyond the internal team.

- [ ] **Produce a public user manual.** Strip "INTERNAL USE ONLY," remove stale `FinGPT` / `FinGPT-Light` references that conflict with the FinSearch rebrand, host as a Sphinx page at `agenticfinsearch.org/docs/manual` or similar. ~1 day. *(The papers independently validate this as the top blocker: Mozilla had high modularity but no contributors until it shipped docs, tutorials, and tooling — modularity alone does not attract people.)*
- [ ] **Resolve the model-name inconsistency.** Backend config keys still expose `FinGPT` / `FinGPT-Light` in the model dropdown. Coordinated frontend + backend + docs rollout per the central-db open issue. New users currently see old names in the UI.
- [ ] **Build a "Press & Recognition" page.** Lead with JPM Faculty Award (2025), NYT coverage, XBRL International coverage, ICAIF 2024 paper, named PIs (Prof. Xiaodong Wang, Dr. Xiao-Yang "Yanglet" Liu). Link prominently from landing page and Discord `#about`.
- [ ] **Build the "Build a FinSearch MCP Server" contributor guide.** This is the bridge that converts the modularity we already have (MCP architecture) into actual contributing developers — the papers identify this as the single highest-leverage move for growing the onion's inner layers. Targeted at capstone teams and external developers.
- [ ] **Package the FinSearch vs. Perplexity side-by-side asset.** The Nvidia September 2025 question screenshot already exists in the internal manual; polish, add 2–3 more from the QA benchmark, produce a single shareable image set. This is the hero asset for every social post.
- [ ] **Designate one community-facing contact.** Three named PIs = great credentials, terrible for "who do I ping." One human's name + Discord handle in `#welcome`. (Directly addresses the "no single responsibility" OSS failure mode.)
- [ ] **Draft pinned posts** for `#welcome`, `#announcements`, and `#about`. (See section 6.)
- [ ] **Pre-seed 3–5 starter posts in each channel.** Empty channels signal "this is dead."
- [ ] **Adopt the Contributor Covenant** verbatim as Code of Conduct. Pin in `#welcome`.
- [ ] **Pin a financial-advice disclaimer** in `#welcome` and `#help`.
- [ ] **Flip the Chrome Web Store listing from Unlisted to Public.** Single toggle, do this LAST after everything above ships.

---

## 5. Launch Sequence

| Week | Focus | Deliverable |
|---|---|---|
| 1 | Lock audience (Step 0); build public manual; fix model-name inconsistency | Public manual live |
| 2 | Press & Recognition page; MCP contributor guide; side-by-side asset; designate contact | Landing page updated |
| 3 | Draft and pin all Discord posts; pre-seed channels; set up onion roles + GitHub issue labels | Discord internally ready |
| 4 | Recruit 10 people you already know (Columbia students, paper co-authors, FINOS contacts) into Discord pre-public | Pre-seeded conversation visible |
| 5 | Flip Chrome Web Store to Public; soft launch via FINOS Slack + XBRL International + 1 targeted subreddit | First public joiners |
| 6 | Hard launch: Show HN post (~9am ET weekday), Twitter/X thread, paper update post | Public visibility peak |

**Critical:** Do not do weeks 5 and 6 simultaneously. Sequential lets you fix problems before the bigger crowd arrives.

---

## 6. Discord Structure

Start narrow. Expand only on demand.

```
INFO
  #welcome           — rules, intro template, link to Chrome extension, disclaimer
  #announcements     — read-only; releases, paper updates, demos
  #changelog         — read-only; shipped fixes (closes the bug-report → fix loop visibly)
  #about             — Columbia anchor, JPM Award, NYT/XBRL Intl press, team, paper
  #roadmap           — public read-only roadmap mirror

CONVERSATION
  #general           — anything
  #help              — install issues, "I got this answer, is it right?" (defect detection)
  #show-and-tell     — user-posted use cases, screenshots, prompts

OPEN ONLY WHEN 3+ MEMBERS ASK
  #mcp-developers, #research-papers, #feature-requests, #buffet-agent
```

### Roles as a progression ladder (not flat badges)

The papers are explicit that *advancement is the reward* — meritocratic movement inward is what motivates volunteers. Make the onion layers visible, earned, and announced:

| Role | Onion layer | Earned by |
|---|---|---|
| `@user` | Users | joining |
| `@tester` | Bug reporters | filing ≥1 *verified* accuracy/bug report |
| `@contributor` | Contributing developers | shipping a merged fix or an MCP server |
| `@core` | Core team | sustained high-quality contribution + roadmap trust |
| `@founding-member` | (cross-cutting badge) | first 100 joiners; retire role after #100 |
| `@student` | (cross-cutting) | Columbia / academic affiliation |
| `@sponsor` | (cross-cutting) | sponsor tier |

Promotions get a public shout-out in `#announcements`. "Sign your own work" — credit contributors and MCP authors by name everywhere.

---

## 7. The Defect Lifecycle (Discord + GitHub)

Bahamdain's three-process QA framework (his Fig. 2) is the operational loop that turns the Discord from a chat room into a quality apparatus. Instrument it explicitly:

1. **Defect Detection** — a user hits a wrong number (via the Validate button or normal use) and either ignores it, files it, or asks in `#help` (most common). Path: `#help` → triaged into a GitHub Issue.
2. **Defect Verification** — a maintainer reads the report, reproduces it, and labels the GitHub Issue `open` / `in-progress` / `duplicate` / `invalid`, then assigns an **owner** (single responsibility — the cathedral discipline from §2.3). Verified accuracy bugs also become candidate benchmark/ArkSim cases.
3. **Solution Verification** — the owner self-reviews, opens a PR, gets outside peer-review, merges, and the fix lands in `#changelog`. If the fix introduces a new defect, it re-enters step 2.

The visible, fast loop (report → labeled issue → fix → changelog) is itself the retention mechanism: contributors stay engaged when they see their reports turn into shipped fixes quickly (Aberdour: rapid release cycles keep reviewers motivated).

---

## 8. First-100 Onboarding Playbook

The single most important phase. Do not skip.

- DM every new joiner within 24 hours. Three sentences: welcome / what brought you here / what would you ask FinSearch first.
- Log every response in a Google Sheet (member, source, persona, first question). This becomes the most valuable artifact for the next 6 months.
- Reply to every question in the server for the first month. Even "I don't know yet." Silence kills momentum.
- Turn every confused user into a docs entry. Question in `#help` → pinned message or manual update.
- **Actively nudge the onion inward:** when someone files a good bug, thank them publicly and invite them to the next rung ("want to take a crack at the fix?"). Conversion from user → tester → contributor does not happen by itself.

This is a 90-day investment, not a permanent commitment. After ~100 active members, the community starts running itself.

---

## 9. Acquisition Channels

| Channel | Why it fits | Lead content |
|---|---|---|
| FINOS Slack + mailing list | Natural OSS home; project already references FINOS | Intro post + offer to give a talk |
| XBRL International member network | Already covered FinSearch; aligned audience | Member-newsletter intro at Discord launch |
| Columbia CS / DSI / SecureFinAI student channels | Most committable contributors | "Build an MCP server as a capstone" |
| r/ValueInvesting, r/Bogleheads, r/SecurityAnalysis | Audience D match; Buffet Agent hook | Buffet Agent + accuracy story |
| r/algotrading, r/CFA | Audience A match | 91.7% vs 41.7% benchmark |
| FinTwit (Twitter/X) | Equity analysts live here | Nvidia side-by-side video |
| Hacker News | Loves OSS with academic anchor | "Show HN: A financial AI that proves its numbers (Columbia, ICAIF paper)" — submit once, ~9am ET weekday |
| r/ChatGPT, r/LocalLLaMA tool comparison threads | Side-by-side demos do well | Same benchmark, different framing |

**Do not:** pay for ads, post in unrelated subs, cold-DM on LinkedIn, "growth hack." Audience is sophisticated and will smell it.

---

## 10. Metrics That Matter

Track these weekly in a sheet. Ignore everything else for the first 6 months.

1. **New joiners** (raw growth)
2. **Weekly active members posting at least once** (engagement — target >20% of total)
3. **Questions answered within 4 hours** (responsiveness — target >80%)
4. **Onion health / inward migration** — count of members who advanced a rung this month (user→tester, tester→contributor). This is the coevolution health metric; a community that grows in headcount but never promotes anyone inward is stalling, not scaling.

Bonus quality signal worth tracking once the loop runs: **verified accuracy bugs caught by the community** that became benchmark cases. This is the direct measure of community-as-QA paying off.

**Vanity metrics to ignore:** total member count, message volume, Discord analytics dashboard.

---

## 11. Governance Basics

- 5 short rules: be kind / no financial-advice spam / no referral links / English in public channels / lab and research questions welcome.
- Moderation: FlyMiss + one teammate as mods. Discord AutoMod for invite-spam and slurs. No third-party bots until actually needed.
- Disclaimer: "FinSearch outputs are not financial advice." Pinned in `#welcome` and `#help`.
- Code of Conduct: Contributor Covenant verbatim. Do not reinvent.

---

## PROPOSED AMENDMENT (2026-06-10) — News Heartbeat & #market-news [pending ratification]

**Status:** Proposed — per the source-of-truth rule at the end of this document, this amendment resolves the conflict between the new News Heartbeat service and the current text. Existing section numbering is untouched; on ratification, fold A–D into §6, §11, and the Weekly cadence in place.

### A. Channel addition (amends §6, INFO category)

Add one channel to the INFO category:

```
INFO
  #market-news       — read-only; daily automated Yahoo Finance news digest
                       (short summaries + attributed links out), posted by the
                       Agentic FinSearch bot at 11:00 UTC
```

Members read-only; only the bot posts. Rationale in this plan's own terms: "Silence kills momentum" (§8), "Empty channels signal 'this is dead'" and the pre-seed requirement (§4) — plus the channel doubles as a daily public demo of the product's retrieval → aggregation → summarization pipeline. Tone is audience-facing standard: factual, sourced, no hype. The §6 lifecycle rule (open on demand, close after 30 days of no activity) does not apply to `#market-news` while the heartbeat runs; if the heartbeat is paused (see D), review the channel under the normal monthly rule.

### B. Bot policy clarification (amends §11)

"No third-party bots until actually needed" **stands unchanged.** The Agentic FinSearch bot is **first-party product surface** — the product posting its own output under its own name — not a third-party moderation bot, and it is "actually needed" as the content engine of `#market-news`. Moderation remains Discord AutoMod only; the bot moderates nothing, posts via REST only (no gateway connection, no privileged intents), and the pinned disclaimer "FinSearch outputs are not financial advice." applies to its output.

### C. Cadence addition (amends Weekly recurring actions)

- [ ] **Daily (automated):** `#market-news` digest at 11:00 UTC, posted by the Agentic FinSearch bot.

This *complements* the manual Mon/Wed/Fri human cadence — it does not replace it, and automated posts never count toward the human-touch weekly items.

### D. ToS guardrail (extends the existing Yahoo trigger)

The digest is short summaries + attribution + links **out** to Yahoo — no republication of article text — consistent with the Yahoo Finance ToS constraint (§1). The existing trigger-based action "Yahoo Finance changes ToS or breaks scraping" gains one clause: **pause the heartbeat** under the same contingency (it ships with a dry-run switch for exactly this), alongside pausing member acquisition and communicating transparently in `#announcements`.

### E. Implementation pointer

Design + runbook: `Docs/superpowers/specs/2026-06-10-news-heartbeat-design.md` and `Heartbeat/DISCORD_BOT_SETUP.md`.

---

## 12. Open Strategic Questions

These need resolution before or shortly after launch. Flag to professor / team.

- **Columbia IP ownership** — blocks all monetization paths including sponsor tier. Talk to tech transfer office.
- **Whether to open a sponsor tier formally** before or after first 100 members.
- **Buffet Agent positioning** — is it a sub-product, a marketing hook, or a research demo? Affects whether it gets its own channel.
- **Bilingual community?** Lab has Chinese-language presence (Materials includes Chinese-titled PDFs); decision on whether Discord supports CN/EN or stays EN-only.
- **OSS maturity scoring** — audience D (compliance/enterprise) evaluates OSS by rubrics like OSMM / Business Readiness Rating (Aberdour sidebar). Scoring well on one becomes a trust artifact, like the benchmark. Long-term: decide whether to pursue a formal maturity rating.

---

---

# Long-Term & Recurring Actions

**Read this section every Monday. The kickstart succeeds or fails on whether these stay alive.**

## Weekly (every week, indefinitely)

- [ ] **Monday:** Post one announcement (release note, paper update, demo, sponsor news) in `#announcements`.
- [ ] **Wednesday:** Post one discussion prompt in `#general` ("How are you using SEC EDGAR retrieval?", "What ratio would you want next in the Validate button?").
- [ ] **Friday:** Post one "build in public" item (commit highlight, axiom of the week, benchmark update) in `#announcements` or `#show-and-tell`.
- [ ] Update the weekly metrics row in the tracking sheet: joiners / active / response time / inward migrations.
- [ ] Review `#help` for any unanswered questions older than 24 hours.
- [ ] Run the defect lifecycle: triage new `#help` reports into labeled GitHub Issues; post any shipped fixes to `#changelog`.
- [ ] DM every new joiner from the past 7 days who has not been onboarded.

## Monthly

- [ ] **First Monday of the month:** publish a "month in review" post in `#announcements` — shipped features, benchmark deltas, new contributors, sponsor news, upcoming.
- [ ] **Recognize the onion:** publicly shout out members who advanced a rung and the accuracy bugs caught by the community this month. Recognition is the volunteer's pay.
- [ ] Review the onboarding sheet for persona patterns. If a new persona is emerging (e.g., "5+ joiners this month are crypto traders"), evaluate spinning off a sub-channel.
- [ ] Review whether any channel deserves to be opened (3+ members organically pushing a topic) or closed (no activity for 30 days).
- [ ] Audit pinned posts in `#welcome` and `#about` for accuracy. Stale credentials or outdated links erode trust.
- [ ] Re-run the FinSearch vs. Perplexity benchmark on 3–5 fresh questions and post the result. Keeps the moat visible and gives content for the month.

## Quarterly

- [ ] Re-audit acquisition channels. Which ones produced active members vs. lurkers? Cut what is not working.
- [ ] Update the public roadmap mirror in `#roadmap`. The roadmap as a trust signal only works if it is actually current.
- [ ] **Promote and review the core:** assess who has earned `@contributor` / `@core`, and whether any contributor is overloaded (the papers warn the core team becomes overburdened without enough contributing developers).
- [ ] Solicit testimonials from the most active members. These feed into the Press & Recognition page and sponsor pitches.
- [ ] Office hours with Prof. Liu (or designated PI) — 1 hour, open invite to Discord, recorded if possible. Most powerful single trust-builder for academic-anchored projects.
- [ ] Refresh the Buffet Agent (or whichever fine-tuned model is live) — even a small update is a content peg and signals the project is alive.

## Semi-Annual

- [ ] Re-run ArkSim with current MCP configuration. Update `ARKSIM_USER_STORY.md` with new before/after numbers. Post the delta publicly.
- [ ] Fold community-caught accuracy bugs into the formal benchmark / regression suite (this is the bazaar→cathedral handoff from §2.3 made concrete).
- [ ] Submit FinSearch to one academic conference, one industry conference, or one OSS event. Every appearance is a recruiting funnel.
- [ ] Revisit this kickstart doc. Update what is stale. Archive what is done.

## Annual

- [ ] Re-evaluate the audience choice from Step 0. Is the primary framing still right, or has the membership shifted enough to warrant a re-pick?
- [ ] Apply or re-apply for cloud credits (AWS Activate, Google for Education, Azure for Research). One day of paperwork = a year of free hosting.
- [ ] Pursue or renew FINOS membership / standing for OSS governance credibility.
- [ ] Consider an OSS maturity self-assessment (OSMM / Business Readiness Rating) as a trust artifact for enterprise/audience-D evaluation.
- [ ] Member survey: 5 questions max, posted in `#general` and announced. Use results to prioritize the next year.

## Trigger-Based (do when the trigger fires, not on a calendar)

- [ ] **Member count crosses 100:** retire `@founding-member` role; transition from manual DM onboarding to a welcome bot + lighter-touch personal follow-up on a sample.
- [ ] **Member count crosses 500:** formalize a moderator role beyond the founding team; recruit 2 community moderators from active members (promote from the onion, don't appoint outsiders).
- [ ] **First sponsor signed:** open private `#sponsors` channel; schedule quarterly office hours for them with the PI; route sponsor funding toward the regression/test infrastructure the community can't fully cover.
- [ ] **First external MCP server shipped by a community member:** spotlight in `#announcements`; promote the author to `@contributor`; open `#mcp-developers` if not yet open; consider an "MCP Showcase" recurring monthly post.
- [ ] **First negative press / public criticism:** respond within 24 hours, in public, factually, without defensiveness. The response is more important than the criticism itself.
- [ ] **First viral moment (HN front page, viral tweet, etc.):** double moderation coverage for 72 hours; have the team available; expect a member-count spike and a quality dip; over-invest in onboarding the new cohort.
- [ ] **Yahoo Finance changes ToS or breaks scraping:** pause all member-acquisition activity, communicate transparently in `#announcements`, accelerate the alternative data source roadmap.
- [ ] **Columbia IP ownership resolved:** revisit monetization paths in `POSITIONING_STRATEGY.md` and update community-facing language around sponsor tiers.

---

*This document is the source of truth for community operations. When it conflicts with anything else, update this document — do not let the conflict persist. The v1 acquisition-lens draft is preserved at `COMMUNITY_KICKSTART_LEGACY.md`.*
