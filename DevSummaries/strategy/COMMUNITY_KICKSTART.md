# Agentic FinSearch — Community Kickstart Plan

**Date:** 2026-05-26
**Status:** Draft — awaiting audience decision (Step 0) before execution
**Owner:** FlyMiss
**Related:** `POSITIONING_STRATEGY.md` (Approach C ecosystem), `ARKSIM_USER_STORY.md` (benchmark evidence), central-db `projects/finsearch/project.md`

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

## 2. Step 0 — Audience Decision (BLOCKER)

Pick one primary audience before opening the Discord. Channel structure, content cadence, and recruiting channels all derive from this choice.

| Framing | Primary members | Hook | Unique asset |
|---|---|---|---|
| **A. Verified Numbers** | Equity analysts, finance students | 91.7% benchmark | XBRL pipeline, SEC EDGAR MCP |
| **B. Quant Screener** | Crypto / technical traders | NL exchange-wide scans | TradingView MCP |
| **C. Build on FinSearch** | MCP authors, capstone teams | Plug-in architecture | Open MCP layer |
| **D. Trusted Financial AI** | Compliance officers, fiduciaries, value investors | Air-gapped + zero hallucination + JPM/NYT credibility | Air-gapped deploy + Buffet Agent + Columbia anchor |

**Recommendation:** D primary, A as on-ramp. B and C become sub-channels later when 3+ members organically push them.

**Decision required:** Lock the audience before any other step.

---

## 3. Pre-Launch Blockers

All of these must ship before flipping the Chrome Web Store to public or opening the Discord beyond the internal team.

- [ ] **Produce a public user manual.** Strip "INTERNAL USE ONLY," remove stale `FinGPT` / `FinGPT-Light` references that conflict with the FinSearch rebrand, host as a Sphinx page at `agenticfinsearch.org/docs/manual` or similar. ~1 day.
- [ ] **Resolve the model-name inconsistency.** Backend config keys still expose `FinGPT` / `FinGPT-Light` in the model dropdown. Coordinated frontend + backend + docs rollout per the central-db open issue. New users currently see old names in the UI.
- [ ] **Build a "Press & Recognition" page.** Lead with JPM Faculty Award (2025), NYT coverage, XBRL International coverage, ICAIF 2024 paper, named PIs (Prof. Xiaodong Wang, Dr. Xiao-Yang "Yanglet" Liu). Link prominently from landing page and Discord `#about`.
- [ ] **Package the FinSearch vs. Perplexity side-by-side asset.** The Nvidia September 2025 question screenshot already exists in the internal manual; polish, add 2–3 more from the QA benchmark, produce a single shareable image set. This is the hero asset for every social post.
- [ ] **Designate one community-facing contact.** Three named PIs = great credentials, terrible for "who do I ping." One human's name + Discord handle in `#welcome`.
- [ ] **Draft pinned posts** for `#welcome`, `#announcements`, and `#about`. (See section 5.)
- [ ] **Pre-seed 3–5 starter posts in each channel.** Empty channels signal "this is dead."
- [ ] **Adopt the Contributor Covenant** verbatim as Code of Conduct. Pin in `#welcome`.
- [ ] **Pin a financial-advice disclaimer** in `#welcome` and `#help`.
- [ ] **Flip the Chrome Web Store listing from Unlisted to Public.** Single toggle, do this LAST after everything above ships.

---

## 4. Launch Sequence

| Week | Focus | Deliverable |
|---|---|---|
| 1 | Lock audience (Step 0); build public manual; fix model-name inconsistency | Public manual live |
| 2 | Press & Recognition page; side-by-side asset; designate contact | Landing page updated |
| 3 | Draft and pin all Discord posts; pre-seed channels | Discord internally ready |
| 4 | Recruit 10 people you already know (Columbia students, paper co-authors, FINOS contacts) into Discord pre-public | Pre-seeded conversation visible |
| 5 | Flip Chrome Web Store to Public; soft launch via FINOS Slack + XBRL International + 1 targeted subreddit | First public joiners |
| 6 | Hard launch: Show HN post (~9am ET weekday), Twitter/X thread, paper update post | Public visibility peak |

**Critical:** Do not do weeks 5 and 6 simultaneously. Sequential lets you fix problems before the bigger crowd arrives.

---

## 5. Discord Structure

Start narrow. Expand only on demand.

```
INFO
  #welcome           — rules, intro template, link to Chrome extension, disclaimer
  #announcements     — read-only; releases, paper updates, demos
  #about             — Columbia anchor, JPM Award, NYT/XBRL Intl press, team, paper
  #roadmap           — public read-only roadmap mirror

CONVERSATION
  #general           — anything
  #help              — install issues, "I got this answer, is it right?"
  #show-and-tell     — user-posted use cases, screenshots, prompts

OPEN ONLY WHEN 3+ MEMBERS ASK
  #mcp-developers, #research-papers, #feature-requests, #buffet-agent
```

**Roles (set up at launch):**
- `@team` — internal members
- `@founding-member` — first 100 joiners (give a badge, retire role after)
- `@student` — Columbia / academic affiliation
- `@mcp-author` — anyone who has shipped an MCP server
- `@sponsor` — for the future sponsor tier

Roles do real work: signal status (motivates contribution) and target pings without spamming everyone.

---

## 6. First-100 Onboarding Playbook

The single most important phase. Do not skip.

- DM every new joiner within 24 hours. Three sentences: welcome / what brought you here / what would you ask FinSearch first.
- Log every response in a Google Sheet (member, source, persona, first question). This becomes the most valuable artifact for the next 6 months.
- Reply to every question in the server for the first month. Even "I don't know yet." Silence kills momentum.
- Turn every confused user into a docs entry. Question in `#help` → pinned message or manual update.

This is a 90-day investment, not a permanent commitment. After ~100 active members, the community starts running itself.

---

## 7. Acquisition Channels

| Channel | Why it fits | Lead content |
|---|---|---|
| FINOS Slack + mailing list | Natural OSS home; project already references FINOS | Intro post + offer to give a talk |
| XBRL International member network | Already covered FinSearch; aligned audience | Member-newsletter intro at Discord launch |
| Columbia CS / DSI / SecureFinAI student channels | Most committable contributors | "Build an MCP server as a capstone" |
| r/ValueInvesting, r/Bogleheads, r/SecurityAnalysis | Audience D match; Buffet Agent hook | Buffet Agent + accuracy story |
| r/algotrading, r/CFA | Audience A match | 91.7% vs 41.7% benchmark |
| FinTwit (Twitter/X) | Equity analysts live here | Nvidia side-by-side video |
| Hacker News | Loves OSS with academic anchor | "Show HN: Financial AI that proves its numbers (Columbia, ICAIF paper)" — submit once, ~9am ET weekday |
| r/ChatGPT, r/LocalLLaMA tool comparison threads | Side-by-side demos do well | Same benchmark, different framing |

**Do not:** pay for ads, post in unrelated subs, cold-DM on LinkedIn, "growth hack." Audience is sophisticated and will smell it.

---

## 8. Metrics That Matter

Track three numbers weekly in a sheet. Ignore everything else for the first 6 months.

1. **New joiners** (raw growth)
2. **Weekly active members posting at least once** (engagement — target >20% of total)
3. **Questions answered within 4 hours** (responsiveness — target >80%)

**Vanity metrics to ignore:** total member count, message volume, Discord analytics dashboard.

---

## 9. Governance Basics

- 5 short rules: be kind / no financial-advice spam / no referral links / English in public channels / lab and research questions welcome.
- Moderation: FlyMiss + one teammate as mods. Discord AutoMod for invite-spam and slurs. No third-party bots until actually needed.
- Disclaimer: "FinSearch outputs are not financial advice." Pinned in `#welcome` and `#help`.
- Code of Conduct: Contributor Covenant verbatim. Do not reinvent.

---

## 10. Open Strategic Questions

These need resolution before or shortly after launch. Flag to professor / team.

- **Columbia IP ownership** — blocks all monetization paths including sponsor tier. Talk to tech transfer office.
- **Whether to open a sponsor tier formally** before or after first 100 members.
- **Buffet Agent positioning** — is it a sub-product, a marketing hook, or a research demo? Affects whether it gets its own channel.
- **Bilingual community?** Lab has Chinese-language presence (Materials includes Chinese-titled PDFs); decision on whether Discord supports CN/EN or stays EN-only.

---

---

# Long-Term & Recurring Actions

**Read this section every Monday. The kickstart succeeds or fails on whether these stay alive.**

## Weekly (every week, indefinitely)

- [ ] **Monday:** Post one announcement (release note, paper update, demo, sponsor news) in `#announcements`.
- [ ] **Wednesday:** Post one discussion prompt in `#general` ("How are you using SEC EDGAR retrieval?", "What ratio would you want next in the Validate button?").
- [ ] **Friday:** Post one "build in public" item (commit highlight, axiom of the week, benchmark update) in `#announcements` or `#show-and-tell`.
- [ ] Update the weekly metrics row in the tracking sheet: joiners / active / response time.
- [ ] Review `#help` for any unanswered questions older than 24 hours.
- [ ] DM every new joiner from the past 7 days who has not been onboarded.

## Monthly

- [ ] **First Monday of the month:** publish a "month in review" post in `#announcements` — shipped features, benchmark deltas, new contributors, sponsor news, upcoming.
- [ ] Review the onboarding sheet for persona patterns. If a new persona is emerging (e.g., "5+ joiners this month are crypto traders"), evaluate spinning off a sub-channel.
- [ ] Review whether any channel deserves to be opened (3+ members organically pushing a topic) or closed (no activity for 30 days).
- [ ] Audit pinned posts in `#welcome` and `#about` for accuracy. Stale credentials or outdated links erode trust.
- [ ] Re-run the FinSearch vs. Perplexity benchmark on 3–5 fresh questions and post the result. Keeps the moat visible and gives content for the month.

## Quarterly

- [ ] Re-audit acquisition channels. Which ones produced active members vs. lurkers? Cut what is not working.
- [ ] Update the public roadmap mirror in `#roadmap`. The roadmap as a trust signal only works if it is actually current.
- [ ] Solicit testimonials from the most active members. These feed into the Press & Recognition page and sponsor pitches.
- [ ] Office hours with Prof. Liu (or designated PI) — 1 hour, open invite to Discord, recorded if possible. Most powerful single trust-builder for academic-anchored projects.
- [ ] Refresh the Buffet Agent (or whichever fine-tuned model is live) — even a small update is a content peg and signals the project is alive.

## Semi-Annual

- [ ] Re-run ArkSim with current MCP configuration. Update `ARKSIM_USER_STORY.md` with new before/after numbers. Post the delta publicly.
- [ ] Submit FinSearch to one academic conference, one industry conference, or one OSS event. Every appearance is a recruiting funnel.
- [ ] Revisit this kickstart doc. Update what is stale. Archive what is done.

## Annual

- [ ] Re-evaluate the audience choice from Step 0. Is the primary framing still right, or has the membership shifted enough to warrant a re-pick?
- [ ] Apply or re-apply for cloud credits (AWS Activate, Google for Education, Azure for Research). One day of paperwork = a year of free hosting.
- [ ] Pursue or renew FINOS membership / standing for OSS governance credibility.
- [ ] Member survey: 5 questions max, posted in `#general` and announced. Use results to prioritize the next year.

## Trigger-Based (do when the trigger fires, not on a calendar)

- [ ] **Member count crosses 100:** retire `@founding-member` role; transition from manual DM onboarding to a welcome bot + lighter-touch personal follow-up on a sample.
- [ ] **Member count crosses 500:** formalize a moderator role beyond the founding team; recruit 2 community moderators from active members.
- [ ] **First sponsor signed:** open private `#sponsors` channel; schedule quarterly office hours for them with the PI.
- [ ] **First external MCP server shipped by a community member:** spotlight in `#announcements`; open `#mcp-developers` if not yet open; consider an "MCP Showcase" recurring monthly post.
- [ ] **First negative press / public criticism:** respond within 24 hours, in public, factually, without defensiveness. The response is more important than the criticism itself.
- [ ] **First viral moment (HN front page, viral tweet, etc.):** double moderation coverage for 72 hours; have the team available; expect a member-count spike and a quality dip; over-invest in onboarding the new cohort.
- [ ] **Yahoo Finance changes ToS or breaks scraping:** pause all member-acquisition activity, communicate transparently in `#announcements`, accelerate the alternative data source roadmap.
- [ ] **Columbia IP ownership resolved:** revisit monetization paths in `POSITIONING_STRATEGY.md` and update community-facing language around sponsor tiers.

---

*This document is the source of truth for community operations. When it conflicts with anything else, update this document — do not let the conflict persist.*
