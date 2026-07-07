# News → Signals Pipeline — Research Benchmark & Spec Comparison

**Date:** 2026-07-06
**Companion to:** `2026-07-06-news-to-signals-pipeline-design.md` (the design spec) and `tmp-signals-review.txt` (the real prototype batch: 135 stories → 10 tickers, 9 neutral, MSFT +0.50).
**Purpose:** Independent deep research into how production news→sentiment→signal pipelines work (industry + academia + open source), compared against our spec and prototype, to directly guide the `news_signals.py` implementation.

**Evidence base & method.** Three research passes:
1. **Industry + academia** — a fan-out/verify workflow: 5 search angles → 24 sources fetched → 99 falsifiable claims extracted → 3-vote adversarial verification (68/75 top claims survived, 61 at high confidence). Sources: RavenPack (7 docs), Refinitiv/LSEG MarketPsych, 8 arXiv papers, PMC, Wharton-WRDS, plus production practitioners (Feedly, NewsCatcher, AWS-FSI). *(The workflow's only failed step was the cosmetic final JSON-merge; all verified claims were recovered from the transcript journal — the science is intact.)*
2. **Open source, code-level** — 8 repos read directly (not web-searched): TradingAgents (91k★), OpenBB (70k★), FinGPT/FinNLP (our AI4Finance upstream), FinnewsHunter, ProsusAI/finBERT, Stocksent, StockSentimentTrading.
3. **Direct GitHub discovery** — high-star/high-potential landscape sweep.

---

## 0. TL;DR — the one-paragraph answer

The prototype's 9-of-10 `0.00` batch is **not a scorer bug; it is the base rate meeting a missing gate.** Academia measures ~80% of financial messages as neutral and Feedly measures ~80% of incoming news as near-duplicates, so a pipeline that scores *every ticker-tagged story* will be dominated by roundups where the ticker is mentioned but is not the subject. **Every serious industry/academic pipeline gates on three axes before scoring — source tier, entity-as-subject relevance, and novelty/dedup — and we currently have only the first (and only partially).** Adding an **entity-as-subject gate** is the single highest-leverage change and directly fixes the sample. On calibration, our continuous `[-1,1]` score and per-batch snapshot are **well-precedented and correct for a short-horizon consumer**; the only tweak worth considering is widening the neutral band from ±0.15 toward ±0.20 (which one production study chose after systematic testing) and possibly making it asymmetric. On injection, our corroboration damper is a real but weak *aggregation-level* rail; the cheap, high-ROI addition is **datamarking/spotlighting** at the prompt (independently shown to cut injection success from >50% to <2%). **Notably, our spec is already more hardened than the entire OSS field — including the 91k-star flagship — on provenance and failure handling.** We are behind only the proprietary/academic leaders, and only on gating.

---

## 1. Scorecard: our spec vs. the field

| Dimension | Our spec (§) | Industry / academia norm | OSS field (8 repos) | Verdict |
|---|---|---|---|---|
| **Score type** | Continuous `[-1,1]` (§4.2) | Continuous beats 3-class for trading (RoBERTa-regression 50.6% return vs negative-alpha for classifiers) | Mixed: finBERT continuous P(+)−P(−); FinGPT/FinNLP 3-class; TradingAgents 0–10 | ✅ **Ahead of most OSS; matches best practice** |
| **Neutral deadband** | ±0.15 label threshold (§4.2, `SIGNALS_THRESHOLD`) | ±0.20 (40/60 on 0–100) chosen after systematic test; ±0.10 too noisy; tuned asymmetric thresholds ~doubled F1 | FinnewsHunter ±0.1; TradingAgents soft/qualitative; most: none | 🟡 **Reasonable; ±0.20 has direct backing; consider asymmetry** |
| **Aggregation** | Per-batch snapshot, 24 h window, "nothing merges" (D4, §3) | Horizon-driven: short→snapshot/aggressive decay; long→EWMA (90-day half-life) / 90-day SMA | Snapshot everywhere in serving paths; FinnewsHunter has EMA(0.9) but only in offline feature-builder | ✅ **Correct for short-horizon consumer; decay is the documented upgrade path** |
| **Source tiering** | Editorial-score gate `≥2.0` (§4.1, §5) | Exclude PR/promo/robo → curated whitelist (MarketPsych 4k of 25k); authority-weighted ranking | Only OpenBB exposes an `is_spam` passthrough; rest none | 🟡 **Partial — necessary but volume-relevance, not subject-relevance** |
| **Entity-as-subject relevance** | ❌ none (ticker-tagged is enough) | Universal: RavenPack Relevance>90 (study uses =100); NER sentence-isolation; drop article if ticker not subject; KG entity-linking | ❌ **none** in any of the 8 repos (OpenBB's `business_relevance` 0–1 passthrough is the only trace) | 🔴 **THE gap — explains the sample** |
| **Novelty / dedup** | ❌ deferred as "seam debt" (§8); within-batch `n_articles` only | Universal: Event-Similarity-Day novelty; LSH/MinHash near-dup collapse (Feedly, NewsCatcher, crackingwalnuts) | ❌ none (OpenBB URL-exact dedup only; ~10% recall in practice) | 🔴 **Gap — inflates `n_articles`, dilutes context** |
| **Provenance defense** | guid-join + per-ticker membership check (§6.3, §7.3) | Not addressed in the literature (they own their pipeline end-to-end) | ❌ **none** in any OSS repo | 🟢 **We lead the field here** |
| **Injection defense** | "feed text is data" + corroboration damper (§4.3, §7.3) | Layered: spotlighting/datamarking (>50%→<2% ASR); instruction hierarchy; LDD; SecAlign fine-tune (ASR 8%) | Only TradingAgents datamarks (delimiter tags, no "untrusted" language); rest raw-interpolate | 🟡 **Ahead of OSS; behind SOTA — add datamarking** |
| **Failure/atomicity** | fail-closed, atomic write, degraded artifact, canary (§6) | (out of scope for research sources) | No OSS repo has comparable rigor; FinnewsHunter has two un-reconciled score scales writing one column | 🟢 **We lead the field** |

---

## 2. The prototype sample, explained by the evidence

| Observation in `tmp-signals-review.txt` | What the research says is happening |
|---|---|
| 9 of 10 tickers score exactly `0.00` | **Base rate.** ~80–82% of financial messages are neutral (arXiv 2306.02136); genuine directional signal is "relatively scarce." Compounded by roundup dilution (below). |
| AAPL and GOOGL share the **identical** representative story/guid ("Company News for July 6, 2026") | **Distraction effect.** A multi-ticker roundup mentions many symbols; none is the subject. Glasserman & Lin (2023, via arXiv 2403.04427) found *anonymizing* company names actually *improves* trading performance because irrelevant mentions dilute signal — "distraction effect has a more significant impact than look-ahead bias." |
| AMZN rep = a *rotation* piece; JPM rep = a *Zacks earnings preview*; NVDA rep = a *"this AI stock joined the Dow"* listicle | Textbook mention-not-subject candidates. The scorer correctly returns ~0 because there is no ticker-specific claim to score. |
| `n_articles` = 20 for GOOGL/NVDA/MSFT | **Near-dup inflation.** Feedly: ~80% of incoming articles are near-duplicates; exact-URL dedup catches only ~10%. Our `n_articles` counts ticker-tagged copies, not distinct stories — so the corroboration signal is partly illusory. |
| **MSFT +0.50** — the one non-neutral, from "Microsoft Stock Outlook Remains Upbeat…" (Barchart) | **The control that proves the thesis.** This is the one candidate where the ticker *is the subject*. Subject-stories score; mention-stories don't. Fix the gate and more tickers look like MSFT. |
| `tickers_capped: 10`, `candidates_selected: 30` (= 10×3) | Every scored ticker hit the per-ticker cap of 3 — so the LLM's context for each was 3 *roundups*, not 3 *subject stories*. The cap is spending budget on dilutive candidates. |

**Conclusion:** the sample is behaving exactly as the literature predicts for a pipeline with source-relevance gating but no subject-relevance or novelty gating.

---

## 3. Q1 — Candidate-quality gating (the priority)

### What the leaders do (all three axes, before the scorer sees anything)

**Source tier / whitelist**
- **MarketPsych (LSEG):** explicitly *excludes press releases, corporate websites, promotional content, and robot-generated "robo news,"* curating 25,000+ sources down to a **~4,000-source whitelist**; additionally classifies authors and removes "ideologues, promoters, robots, spammers." → *lseg.com MarketPsych ESG whitepaper*
- **RavenPack:** ingests a *tiered* universe (premium newswires → 9,200 online publications → 15,300 blogs as distinct tiers). → *wrds-www.wharton.upenn.edu/documents/1395/RavenPack.pdf*
- **Practitioner:** source `authority_score ∈ [0,1]` weighted 0.25 in ranking, because ranking on recency alone "surfaces low-authority noise ahead of authoritative outlets." → *crackingwalnuts.com*

**Entity-as-subject relevance (the missing axis)**
- **RavenPack:** relevance is a *first-class, separate analytic*. CSS is only "applicable" when company Relevance > 90; the cited study restricts to **Relevance = 100 (very significant) — the ticker must be the central subject.** A separate "Event Relevance" scores event prominence within the document. → *ravenpack.com/research/composite-sentiment-score*
- **MarketPsych:** 100,000+ entity-name list (aliases/tickers: "IBM", "Big Blue", "$IBM"), human-reviewed monthly, with AI disambiguation (maps "Amazon" the company vs. the river). → *lseg.com*
- **Bigdata.com (RavenPack):** Knowledge-Graph entity-linking filters to documents "explicitly linked to the canonical representation" of the entity — semantically-similar text about a *different* company is excluded. → *medium.com/ravenpack*
- **Academia — the directly-comparable pipeline (arXiv 2606.12210):** a **three-stage pre-scoring filter**: (1) title-based dedup collapsing the same story across outlets (keeping a `coverage_count`), (2) **article-level entity matching that DROPS any article not mentioning the target company/ticker in headline or body**, (3) sentence-level zero-shot NLI relevance (DeBERTa) that strips boilerplate/subscription-prompts/disclaimers and discards sentences scoring < 0.5.
- **Academia — NER isolation (arXiv 2507.03350):** a BERT+CRF NER isolates *only the sentences that mention the specific asset* and scores sentiment on those sentences only — "a company merely mentioned in passing does not receive the whole article's sentiment." Ticker attribution by most-frequent company-ticker match + title-position bonus → 98.15% accuracy.
- **Academia — anti-rehash training (arXiv 2105.12825):** deliberately trains on *hundreds of near-duplicate non-event articles* ("Apple announces a repurchase" vs. "Apple announces the *completion* of the previously-announced repurchase") so the model learns not to trade the rehash; restricts collection to original-sourced newswires (PRNewswire, Businesswire, GlobeNewswire).

**Novelty / dedup**
- **RavenPack:** "Event Similarity Day" scores days since a similar event (up to 365) so stale/repeated stories are suppressed; **filtering to novel + highly relevant events raised the Information Ratio by up to 50%** — gating is a deliberate alpha lever, not cosmetics.
- **Feedly:** LSH clusters articles >80% similar; **measured 80% of articles are near-duplicates** ("cluster 1/5, ignore 4/5"). → *feedly.com/engineering*
- **NewsCatcher:** two-stage — cosine >0.95 on embeddings, then Levenshtein 0.97 (title) / 0.92 (body); keeps one "parent" per cluster chosen by domain credibility + author reputation + recency; 7-day lookback. → *newscatcherapi.com*
- **crackingwalnuts:** exact title/URL matching has only ~10% recall; production uses MinHash+LSH, Jaccard > 0.4.

### What OSS does (near-nothing — we're already ahead here)
- **TradingAgents (91k★):** *no* upstream gating; relies on the vendor (Alpha Vantage) relevance score and "defers filtering to the LLM's judgment at read time."
- **OpenBB (70k★):** pure passthrough; the one reusable idea is exposing **`business_relevance ∈ [0,1]` as a queryable filter *separate* from sentiment polarity** — i.e., splitting "is this about the entity" from "what it says about the entity."
- **FinNLP / FinGPT (our upstream):** no gating; every fetched article scored.
- **StockSentimentTrading:** a *live* example of our exact bug — scores "DBS launches chatbot via Facebook Messenger" as Facebook sentiment, and scores two near-identical "Irish data authority probes Facebook" stories separately.

### Recommendations (ranked)
- **[P0] Add an entity-as-subject gate before the LLM.** Cheapest effective form: require the ticker **or a company-name/alias token in the headline**, plus a **roundup/listicle title-pattern blocklist** (`"Company News for"`, `"... are part of ... Earnings Preview"`, `"Stocks to Watch"`, `"N stocks that…"`, `"Company News Roundup"`). This alone drops the AAPL/GOOGL/AMZN/JPM/NVDA roundups and lets subject-stories through. Track a `candidates_dropped_not_subject` diagnostic.
- **[P0/P1] Collapse near-duplicates within the batch** before computing `n_articles`. A normalized-title match (or MinHash if you want robustness) so 20 copies of one roundup count as one story. This makes the corroboration damper *meaningful* (right now `n_articles≥2` can be satisfied by dupes).
- **[P1] Prefer subject-stories inside the per-ticker cap.** When capping at 3, rank entity-subject candidates above mentions, and prefer novel over rehash — don't spend the cap on roundups.
- **[P2] Harden source tiering:** extend the editorial-score gate with an explicit exclusion of PR-wire/promotional/robo-generated sources (MarketPsych's exact move), and consider a small curated allowlist for tier-1 outlets.
- **Keep** the editorial-score gate — it's a valid *first* filter; it is simply necessary-not-sufficient. Layer subject-relevance on top.

> **Design note (your input matters):** the entity-subject gate can live (a) as a *deterministic pre-LLM heuristic* (cheap, reduces context dilution — the stated goal), or (b) as an *LLM-returned `is_subject`/relevance field* dropped post-hoc (more accurate, but the roundup still consumes context and cost). Given the spec's cost discipline (D4/D6) and the "reduce dilution before the model sees it" motivation, the pre-LLM heuristic is the recommended default, with the LLM field as an optional second-line filter.

---

## 4. Q2 — Score calibration & aggregation

### Neutral / deadband width
- **arXiv 2507.03350** systematically tested 45/55, 40/60, 35/65 on a 0–100 scale and **adopted 40–60 = ±0.20 on `[-1,1]`**: 45/55 (~±0.10) caused "excessive signal activations"; 35/65 too few. This is the single most direct data point for our knob.
- **arXiv 2606.12210** used *tuned, asymmetric* thresholds on multi-label NLI scores (`τ_pos = 0.56`, `τ_neg = 0.26`) and adding this deadband **nearly doubled macro-F1 (0.187 → 0.335)** by recovering neutral abstentions that argmax loses.
- **RavenPack CSS / MarketPsych:** neutral = midpoint of a 0–100 (or blank/NA when no relevant variables), i.e. a neutral *point*, not a wide band.
- **OSS:** FinnewsHunter uses ±0.1 (downstream at query time); TradingAgents uses a soft qualitative band ("Neutral only when all sources are genuinely silent").
- **Asymmetry evidence:** negative sentiment carries higher information content and stronger return/volume correlation (Pearson 0.64 vs 0.55 positive); multiple papers weight negatives more (arXiv 2306.02136).

**Verdict:** ±0.15 is defensible but on the *sensitive* side; **±0.20 has direct empirical backing.** Because the label is *derived* and consumers re-derive from `score` (spec §4.2), the band is low-stakes — but if you want fewer false directional labels, widen to ±0.20, optionally **asymmetric** (e.g. bullish ≥ +0.20, bearish ≤ −0.15 to exploit the negativity asymmetry). *Note this barely affects the sample: roundups score a true 0.00, so no band change rescues them — Q1 is the lever, not Q2.*

### Snapshot vs. time-decay
The choice is **horizon-driven**, and both are production-real:
- **Snapshot / per-day mean** — arXiv 2507.03350 (average over articles between prev and current market-open → one score/asset/day); arXiv 2306.02136 (`Sₜ = (Pₜ−Nₜ)/Tₜ`, neutral in denominator pulls toward 0); arXiv 2105.12825 (per-event at publish minute). **FinnewsHunter's serving path is a plain SQL AVG snapshot.** Used for **short horizons (intraday–next-day).**
- **Exponential decay (EWMA)** — MarketPsych Core: 365-day EWMA, **half-life 90 days** ("trade-off between recency and volatility"); practitioner freshness: **6-hour half-life** for intraday. **RavenPack Sentiment Index:** 90-day *simple* MA of net pos−neg (market-level "momentum"). Used for **longer horizons.**
- **Horizon-tuned decay** — arXiv 2606.12210: `w_recency = λ^d`, **λ = 0.890 for a 1-day horizon up to 0.970 for 31-day** — short horizons discount old news aggressively.

**Verdict:** Our **per-batch snapshot with a 24 h rolling window is correct** for our consumer (ATL reads the latest artifact for paper/backtest, drops >48 h, no-lookahead — a short-horizon, next-decision consumer). It matches the dominant *academic short-horizon* pattern and mirrors FinnewsHunter's *serving* choice (which keeps its EMA only for offline ML features — exactly our split).

**Recommendations:**
- **[keep]** snapshot for v1. It is not a stopgap; it is the right shape for the current consumer.
- **[document]** the decay upgrade path with concrete params for when a longer-horizon consumer appears: EWMA with half-life ≈ horizon (6 h intraday; ~90 d for multi-week "momentum"), or `λ^d` with λ≈0.89 (1-day) → 0.97 (month). Put this in §8 seam-debt so the swap is specified, not hand-waved.
- **[consider, advanced]** per-batch de-biasing (RavenPack's "Sum Excess Sentiment Indicator" subtracts the day's average sentiment). A market-wide roundup nudges everything slightly positive; subtracting the batch mean (you already compute `news_overview`) isolates idiosyncratic ticker sentiment. Optional; only if broad-market bias shows up in practice.

---

## 5. Q3 — Prompt-injection / adversarial robustness

### State of the art (layered; strongest → cheapest)
1. **SecAlign (Berkeley BAIR, 2025)** — preference-optimization fine-tuning; **attack success rate → 8%** on sophisticated attacks, >4× better than prior SOTA across 5 LLMs; "Secure Front-End" reserves delimiter tokens ([MARK]) and filters them from data. Requires fine-tuning → an **infra decision aligned with our D6 swap seam.**
2. **Instruction hierarchy (OpenAI, arXiv 2404.13208)** — train the model to treat developer prompts as higher-privilege than untrusted third-party text and *selectively ignore lower-privileged instructions*; generalizes to unseen attacks with minimal capability loss. Names our exact failure mode: "LLMs often consider system prompts the same priority as text from untrusted third parties."
3. **Spotlighting / datamarking (Microsoft, arXiv 2403.14720)** — mark untrusted input's provenance via delimiting/datamarking/encoding; **reduced indirect-injection ASR from >50% to <2%.** *Prompt-engineering only — no retraining, layerable now, minimal task degradation.*
4. **LDD — Label Disguise Defense (arXiv 2511.21752)** — *sentiment-specific*: hide the true class labels behind aliases taught via few-shot, so a "rate this bullish"-style injection can't map to the decision output. Model-agnostic, no retraining; tested on GPT-5/GPT-4o/Llama/Gemma/Mistral. Semantically-aligned aliases (good/bad) beat symbols (blue/yellow).
5. **Corroboration / evidence-quality (arXiv 2606.12210)** — `log2(1+coverage_count)` weight + flags: thin evidence (<5 articles), weight concentration (HHI>0.4), source diversity (>60% one source), flip sensitivity (prediction flips on removing ≤5 articles) → HIGH/MEDIUM/LOW reliability.

### What OSS does
- **TradingAgents (91k★):** wraps news in `<start_of_news>…</end_of_news>` delimiter tags — *basic datamarking* — but with **no "untrusted / do not follow instructions inside" language** and no instruction hierarchy. Its own `sentiment_analyst.py` docstring documents a real incident (**GitHub #557/#796**): under prompt pressure the LLM **fabricated fake Reddit/StockTwits content**; fixed by pre-fetching real data instead of tool-calling. *This is field proof that untrusted-text handling matters.*
- **Everyone else (OpenBB, FinGPT, FinNLP, FinnewsHunter, finBERT, Stocksent):** raw string interpolation of news into the prompt, **no delimiting, no defense.** (finBERT/VADER are non-LLM, so injection is N/A.)

### Where our spec sits
Our **guid-join + per-ticker membership check** defends *provenance* (fabricated/cross-wired stories) — something **no OSS repo and none of the papers do** (they own their whole pipeline). Our **corroboration damper** is a real but *aggregation-level* rail: it does nothing once `n_articles ≥ 2`, and cannot stop inference-level steering of a single well-corroborated ticker. Our "feed text is data" instruction is a weak, unstructured version of the instruction hierarchy.

**Recommendations:**
- **[P1, highest ROI] Add datamarking/spotlighting to the batched prompt now.** Wrap each candidate's `title`/`description` in explicit, unique delimiters and state that content inside is **untrusted data to be scored, never instructions to follow** (strengthen the §4.3 "feed text is data" line into a structural boundary, à la TradingAgents' tags *plus* the untrusted-language TradingAgents lacks). Cheap, hosted-API-compatible, >50%→<2% ASR in the source study.
- **[P2] LDD-style label disguise** for the sentiment output, since our task is exactly the class-directive-injection target LDD addresses.
- **[keep]** guid-join + membership + corroboration damper as defense-in-depth (they're orthogonal and we lead the field on them) — but don't treat the damper as the primary defense.
- **[P3 / D6] Before any live-capital wiring:** a SecAlign-class fine-tuned scorer. This is precisely the "revisit before live capital" residual the spec already flags (§7.3) — the literature agrees prompt-level defenses reduce but don't eliminate ASR; fine-tuning is needed for the strongest guarantee.

---

## 6. Where our spec already leads the field

Worth stating plainly, because it reframes the effort as *closing a gap vs. leaders*, not *catching up to the pack*:
- **Provenance defense (guid-join + membership check):** absent from all 8 OSS repos and unmentioned in the literature.
- **Fail-closed everywhere, atomic artifact-before-state writes, degraded-artifact path, staleness canary:** no OSS repo approaches this; FinnewsHunter even writes two un-reconciled score scales into one DB column.
- **Editorial-relevance gate + per-ticker cap:** more candidate discipline than any OSS repo (TradingAgents, our own upstream FinGPT/FinNLP included) has.
- **Continuous `[-1,1]` score:** the empirically-superior representation for trading, vs. the 3-class labels our own upstream uses.

---

## 7. Prioritized implementation guidance (the punchline)

| Pri | Change | Fixes | Effort | Evidence |
|---|---|---|---|---|
| **P0** | **Entity-as-subject gate** (ticker/company in headline + roundup title blocklist) | The sample's 9× 0.00; AAPL/GOOGL shared roundup | Low (heuristic) | RavenPack Rel=100; arXiv 2606.12210 / 2507.03350; Glasserman-Lin |
| **P0/P1** | **Near-dup collapse within batch** before `n_articles` | Inflated `n_articles`; makes damper meaningful | Low–Med | Feedly (80% dupes), NewsCatcher, crackingwalnuts |
| **P1** | **Datamarking/spotlighting** in the LLM prompt | Injection residual (§7.3) | Low | Microsoft spotlighting (>50%→<2%); TradingAgents #557/#796 |
| **P1** | **Prefer subject/novel candidates inside the per-ticker cap** | Cap spent on roundups | Low | RavenPack novelty +50% IR |
| **P2** | Widen deadband to **±0.20** (optionally asymmetric) | Over-eager directional labels | Trivial (knob) | arXiv 2507.03350 (40/60); 2306.02136 (negativity) |
| **P2** | **LDD label disguise** for sentiment output | Class-directive injection | Low–Med | arXiv 2511.21752 |
| **P3** | Keep snapshot; **document EWMA/λ decay upgrade path** in §8 | Future long-horizon consumer | Doc-only | MarketPsych (90d half-life); 2606.12210 (λ 0.89–0.97) |
| **P3** | **SecAlign-class fine-tuned scorer** before live capital (D6) | Steered-score residual | High (infra) | BAIR SecAlign (ASR 8%) |

---

## 8. Sources

**Industry (primary/vendor):**
- RavenPack — Composite Sentiment Score: https://www.ravenpack.com/research/composite-sentiment-score/
- RavenPack — Sentiment Index / Sentiment Factor: https://www.ravenpack.com/research/introducing-ravenpack-sentiment-index · https://www.ravenpack.com/research/constructing-sentiment-factor
- RavenPack — Event Detection: https://www.ravenpack.com/blog/new-ravenpack-analytics-event-detection
- RavenPack Analytics methodology (WRDS/Wharton): https://wrds-www.wharton.upenn.edu/documents/1395/RavenPack.pdf
- RavenPack — "News Sentiment Everywhere" (SESI): https://www.researchgate.net/publication/351360244
- RavenPack Bigdata.com architecture: https://medium.com/ravenpack/architecting-bigdata-com-search-advanced-dimensions-and-synthesis-ddd09494c080
- Refinitiv/LSEG MarketPsych ESG Analytics whitepaper: https://www.lseg.com/content/dam/marketing/en_us/documents/white-papers/refinitiv-marketpsych-esg-analytics-whitepaper.pdf

**Academia (arXiv / PMC):**
- Can News Predict the Market? Limits of Zero-Shot Financial NLP (3-stage filter, NLI deadband, evidence flags): https://arxiv.org/pdf/2606.12210
- Backtesting Sentiment Signals (40/60 deadband, per-day snapshot, NER isolation): https://arxiv.org/pdf/2507.03350
- Trade the Event: Corporate Events Detection (per-event snapshot, anti-rehash, newswire tiering): https://arxiv.org/pdf/2105.12825
- FinBERT with Application in Predicting (`Sₜ=(P−N)/T`, negativity asymmetry): https://arxiv.org/abs/2306.02136
- Sentiment-Driven Prediction, Bayesian-Enhanced (continuous>3-class; VADER/BERT thresholds; cites Glasserman-Lin 2023): https://arxiv.org/abs/2403.04427
- The Instruction Hierarchy (OpenAI): https://arxiv.org/abs/2404.13208
- Spotlighting / datamarking (Microsoft): https://arxiv.org/abs/2403.14720
- Semantics as a Shield — Label Disguise Defense: https://arxiv.org/abs/2511.21752
- SecAlign / StruQ (Berkeley BAIR): https://bair.berkeley.edu/blog/2025/04/11/prompt-injection-defense/
- Event detection via hierarchical clustering (dedup/relevance): https://pmc.ncbi.nlm.nih.gov/articles/PMC8157256/

**Practitioner (dedup/relevance at scale):**
- Feedly Engineering — clustering & dedup (80% dupes, LSH): https://feedly.com/engineering/posts/reducing-clustering-latency
- NewsCatcher — articles deduplication: https://www.newscatcherapi.com/docs/news-api/guides-and-concepts/articles-deduplication
- AWS — near-real-time news clustering for FSI (DBSCAN): https://aws.amazon.com/blogs/industries/near-real-time-news-clustering-and-summarization-for-fsi/
- News aggregator system design (MinHash/LSH, authority score): https://crackingwalnuts.com/post/news-aggregator-system-design

**Open source (read at code level):**
- TauricResearch/TradingAgents (91k★) · OpenBB-finance/OpenBB (70k★) · AI4Finance-Foundation/FinGPT (20.8k★) · AI4Finance-Foundation/FinNLP (1.5k★) · DemonDamon/FinnewsHunter (1.5k★) · ProsusAI/finBERT (2.2k★) · Aryagm/Stocksent · jasonyip184/StockSentimentTrading
