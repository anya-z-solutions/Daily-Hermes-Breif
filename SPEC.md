# Daily Hermes Brief: specification

This records every decision made while designing the brief. Build from it. Where something is listed under "Open decisions", do not guess: ask the owner.

## Purpose and principles

- The brief is read by AI agents that work offline. It is dense, factual context, not a narrative for humans.
- Specific information, not judgement calls. Give dates and figures and let the reader decide what "soon" or "worth it" means.
- Purely informational. Nothing in the brief triggers an action automatically.
- Never force content. If a part has nothing that qualifies, it says so plainly.
- Keep token use low: scripts do the mechanical work (fetching, diffing), the model only does the research and writing that needs judgement.

## Source

Nous Portal's public model catalogue: `https://inference-api.nousresearch.com/v1/models`. No authentication needed to read it. It aggregates Nous Research's own Hermes models and a broad third-party catalogue.

### Facts about the data, learned from a live fetch

- **No promo flag exists in the API.** A discount shows as a `pricing.original` object inside `pricing`, present only when the current price is below list. Discounted = `pricing.original` exists.
- **Prices are strings** (for example `"0.0000001200"`, USD per token). Cast before arithmetic.
- **`hugging_face_id` has three states:** a real ID, `null`, or an empty string `""`. Treat `""` as `null`.
- **Variants:** most models appear several times, for example `model-x`, `model-x:batch`, `model-x:free`. They share a `canonical_slug`. A new variant of an existing model is not a new model.
- **`expiration_date`** exists on some models (for example `"2026-11-11"`); mostly `null`.
- Other useful fields: `context_length`, `architecture.modality`, `reasoning` (effort levels), `knowledge_cutoff`, `top_provider.max_completion_tokens`, `supported_parameters`, `description`.
- No benchmark fields exist. Benchmarks always need an outside source.

## Brief structure

One brief per night, three parts, in this order:

1. Part 1: Nous Portal catalogue changes
2. Part 2: Model of the day
3. Part 3: AI security news of the day

Tiers used throughout: **skip** (logged, never shown), **note** (worth knowing), **act** (worth acting on).

## Part 1: Nous Portal catalogue changes (built)

Implemented in `daily_hermes_brief_part1.py`. Tested against simulated catalogues.

Reported changes:

| Change | Tier |
|---|---|
| New model (keyed on base model; variants of existing models don't count) | act |
| Expiry date newly set, or changed | act |
| Discount started (`pricing.original` appears) | note |
| Discount ended (`pricing.original` disappears) | note |
| Price increase above 20% on input or output price | note |

Not reported: removed models (owner's decision), price changes on `:batch`/`:free` variants (they follow their base model), cosmetic fields.

Every night Part 1 also includes an **expiry roll call**: every model with a non-null `expiration_date`, ID and date only, no interpretation.

Failure handling:

- Portal unreachable, timed out, invalid JSON: record that the catalogue could not be retrieved, with the check time. No comparison.
- HTTP 429: wait, retry once, then record as a failure.
- Empty list, or model count more than 20% below the last snapshot: treat as a partial response. No comparison, and the snapshot is not saved as the new baseline.
- Unparseable models: list them; the catalogue format may have changed.
- Diff against the last *good* snapshot. If that is more than one day old, say so. Always state the date compared against.
- First run: say it is a baseline.
- Dates are UTC; the check time is recorded with its offset.
- Run lock prevents overlapping runs.

## Part 2: Model of the day (not built)

### Selection, in order

1. **New model.** If Part 1 found a new model tonight, it is the pick. If several, take the one with the highest list price. (Tie rule proposed, not confirmed.)
2. **Discount.** Otherwise take every base model currently discounted (`pricing.original` present), with no minimum percentage. Rank by absolute USD saved at the reference usage volume (see open decisions). Remove any model featured in the last N runs. Take the top one.
3. **Tie on value:** prefer the newer model version (for example Gemini 3.8 Flash over 3.7 Flash with an identical saving).
4. **Nothing qualifies:** write "No model of the day."

Work in USD throughout. No currency conversion.

### Exclusion log

Keep a run log, one line per run: date and model ID featured. Exclude models featured in the last N *runs*, not calendar days, so missed nights don't shift the window. A brand-new model can't be in the log, so the exclusion only matters for discount picks.

### Research, in order. Climb the full chain; don't stop early

Always first, no lookup: everything in tonight's snapshot (pricing, original price, context, modalities, reasoning, knowledge cutoff, max output, expiry).

**Branch A: `hugging_face_id` present**

1. Hugging Face model card via `https://huggingface.co/api/models/{id}`: tags, licence, base model, pipeline, README.
2. Linked paper or technical report, if the card references one. Don't search for one if absent.
3. Hugging Face Spaces built on the model: count and examples.
4. Hugging Face discussion tab.

**Branch B: no `hugging_face_id` (proprietary, or `null`/`""`), or Hugging Face returns 404**

1. The provider's own model or release page.
2. An independent benchmark aggregator (for example Artificial Analysis, BenchLM).
3. The provider's changelog or release notes.

**Last resort, either branch:** general web search, only for gaps.

### The model card contains

Facts only: what the model is, who made it, release date, positioning, benchmarks with their source, pricing with original price, and any stated end date for a discount (for example "introductory price expires 2026-12-31"). Do not include why the model was picked or whether it was featured before. Do not include community use-case chatter (dropped: too costly and unpredictable).

## Part 3: AI security news of the day (not built)

- **Scope:** AI security news published today. Focused on Hermes Agent but not limited to it: anything someone running a Hermes setup would want to know.
- **Up to three findings,** one paragraph each. If fewer than three qualify, include only those. If none, write "No security news today."
- **Priority:** actively exploited over disclosed-but-theoretical; Hermes-specific over general; CVSS severity breaks ties.
- **Each paragraph gives:** identifier (CVE or advisory ID), affected component and versions, category (for example CWE), severity, whether exploitation is confirmed, whether a patch exists, source, publication date.
- **Never include exploit mechanics** or proof-of-concept detail. Category level only.
- **Don't repeat:** keep a log of findings already reported and exclude them.

### Sources, in order

1. GitHub Security Advisories for `NousResearch/hermes-agent` and its dependencies.
2. NVD API (`https://services.nvd.nist.gov/rest/json/cves/2.0`), filtered by publication date and by Hermes Agent's dependency packages, then by LLM/agent keywords.
3. MITRE ATLAS as the vocabulary for classifying attack categories.
4. Vendor security pages (Anthropic, OpenAI, Google and others) for model-provider advisories.

A live search on 2026-09-22 found seven open CVEs against hermes-agent (for example CVE-2026-10223, CVE-2026-14628, CVE-2026-85105), several with public proof-of-concept code. This confirms the sources work and that Hermes-specific findings are frequent.

## Output and publishing

- The brief is saved locally first, into the folder the owner's agents read. GitHub is for sharing only.
- Published to the `briefs` branch of this repo as a dated archive, `briefs/YYYY-MM-DD.json`. Nothing is overwritten or removed. No "latest" file.
- `main` holds the code only.
- Runs nightly at 01:00 on the owner's Linux server (meadow-server), on a local model. Time is not a constraint.
- The server needs a GitHub token or deploy key limited to this repo, with push rights to `briefs` only.

## Open decisions (ask the owner before building)

1. **Reference usage volume** for ranking discounts by value: the monthly token amount, and input/output split, the saving is calculated against.
2. **Exclusion window N** for model of the day. 60 was proposed, never confirmed.
3. **Tie rule** when several new models appear on one night.
4. **News sources for Part 3.** "Published today" may need AI security news sources beyond the structured databases. General web search was deliberately cut for cost and unpredictability.
5. **One file per night with all three parts,** or a file per part. Part 1 currently writes `digests/YYYY-MM-DD-part1.json`; this needs aligning with `briefs/YYYY-MM-DD.json`.
6. **JSON, Markdown, or both** for the published brief.
7. **Failure behaviour** for Parts 2 and 3 and for the GitHub push (proposed: record the failure in the brief, skip that part, never hang).
