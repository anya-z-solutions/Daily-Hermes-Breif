# Daily Hermes Brief: specification

This records every decision made while designing and building the brief. Build from it. The reasoning behind decisions taken during the build is in `DECISIONS.md`; what counts as finished is in `DEFINITION_OF_DONE.md`.

## Purpose and principles

- The brief is read by AI agents that work offline. It is dense, factual context, not a narrative for humans.
- Specific information, not judgement calls. Give dates and figures and let the reader decide what "soon" or "worth it" means.
- Purely informational. Nothing in the brief triggers an action automatically.
- Never force content. If a part has nothing that qualifies, it says so plainly.
- Keep token use low. The nightly run uses no language model: every line is assembled by code from structured source fields (see `DECISIONS.md`).

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

Implemented in `daily_hermes_brief_part1.py`. Tested in `tests/test_part1.py`.

Reported changes:

| Change | Tier |
|---|---|
| New model (keyed on base model; variants of existing models don't count) | act |
| Expiry date newly set, or changed | act |
| Discount started (`pricing.original` appears) | note |
| Discount ended (`pricing.original` disappears) | note |
| Price increase above 20% on input or output price | note |

Not reported: removed models (owner's decision), price changes on `:batch`/`:free`/`:US` variants (they follow their base model), floating `~vendor/x-latest` aliases (pointers to another model, never new models), a variant repeating its base model's expiry change, cosmetic fields. A new model is reported under its base ID, never a variant.

Every night Part 1 also includes an **expiry roll call**: every model with a non-null `expiration_date`, ID and date only, no interpretation.

Failure handling:

- Portal unreachable, timed out, invalid JSON: record that the catalogue could not be retrieved, with the check time. No comparison.
- HTTP 429: wait, retry once, then record as a failure.
- Empty list, or model count more than 20% below the last snapshot: treat as a partial response. No comparison, and the snapshot is not saved as the new baseline.
- Unparseable models: list them; the catalogue format may have changed.
- Diff against the last *good* snapshot. If that is more than one day old, say so. Always state the date compared against.
- First run: say it is a baseline.
- The brief and its snapshot are dated by UK calendar date (Europe/London), so the midnight UK run carries that day's date. All times are UTC and recorded with their offset.
- Run lock prevents overlapping runs.

## Part 2: Model of the day (built)

Implemented in `daily_hermes_brief_part2.py`. Tested in `tests/test_part2.py`.

### Selection, in order

1. **New model.** If Part 1 found a new model tonight, it is the pick. If several, take the one with the highest list price at the reference volume (original price if it launched discounted), then the newest, then the ID.
2. **Discount.** Otherwise take every base model currently discounted (`pricing.original` present), with no minimum percentage. Rank by absolute USD saved at the reference usage volume: 10 million input and 2 million output tokens a month. Remove any model featured in the last 14 runs. Take the top one. Variants and `~` aliases are never picked.
3. **Tie on value:** prefer the newer model, by the catalogue's `created` date (for example Gemini 3.8 Flash over 3.7 Flash with an identical saving), then the ID.
4. **Nothing qualifies, or Part 1's catalogue was unusable:** write "No model of the day."

Work in USD throughout. No currency conversion.

### Exclusion log

The run log is the archive of earlier briefs: each brief records the model it featured. Exclude models featured in the last 14 *runs* (briefs), not calendar days, so missed nights don't shift the window. A brand-new model can't be in the log, so the exclusion only matters for discount picks.

### Research, in order. Climb the full chain; don't stop early

Always first, no lookup: everything in tonight's snapshot (pricing, original price, context, modalities, reasoning, knowledge cutoff, max output, expiry).

**Branch A: `hugging_face_id` present**

1. Hugging Face model card via `https://huggingface.co/api/models/{id}`: tags, licence, base model, pipeline, README.
2. Linked paper or technical report, if the card references one. Don't search for one if absent.
3. Hugging Face Spaces built on the model: count and examples.
4. Hugging Face discussion tab.

Benchmarks come from the card's `model-index`, labelled as self-reported by the publisher.

**Branch B: no `hugging_face_id` (proprietary, or `null`/`""`), or Hugging Face returns 401 or 404**

The card gives the catalogue facts and says no Hugging Face record or benchmark figures were found. Provider pages, benchmark aggregators, changelogs and web search are not consulted: they are unstructured and would need a language model to read (see `DECISIONS.md`).

### The model card contains

Facts only: what the model is, who made it, release date, positioning, benchmarks with their source, pricing with original price, and any stated end date for a discount (for example "introductory price expires 2026-12-31"). Do not include why the model was picked or whether it was featured before. Do not include community use-case chatter (dropped: too costly and unpredictable).

## Part 3: AI security news of the day (built)

Implemented in `daily_hermes_brief_part3.py`. Tested in `tests/test_part3.py`.

Part 3 gives an offline agent the evolving threat picture for a Hermes setup, so it can judge what posture to take. It has three sections, in this order.

**Hermes Agent security status (repeated every night).** The latest hermes-agent release on PyPI and its date; every known advisory against hermes-agent from OSV, with GHSA and PYSEC duplicates merged; which advisories affect the latest release; and the minimum version that clears every advisory with a fixed release. Each advisory gives its ID and CVE, the version that fixes it (or that no fix exists), its CWE category, severity, the CVSS vector in plain words (attack vector, complexity, privileges, user interaction, confidentiality, integrity and availability impact), publication date and OSV link. Exploit-maturity metrics are left out.

**Threat picture (repeated every night).** Measured, not described. For each of "prompt injection", "LLM", "AI agent" and "Model Context Protocol": CVEs published in the last three 30-day windows (whole UTC days), the percentage change on the previous window, and the exact NVD API query that returns the newest count, so a person can check it. Then CISA's Known Exploited Vulnerabilities list: how many entries are AI or agent software (matched by product name, so a floor), how many were added in the last 90 days and the 90 days before, how many are classed CWE-1427 (prompt injection), and the three most recent AI entries with date, CWE, NVD link and the vendor reference CISA gives.

**New advisories.**

- **Scope:** advisories published in the 72 hours before the run and not reported in an earlier brief. Focused on Hermes Agent but not limited to it: anything someone running a Hermes setup would want to know.
- **Up to three findings,** one paragraph each. If fewer than three qualify, include only those. If none, write "No security news today."
- **Priority:** confirmed exploited (listed in CISA KEV) first; then Hermes Agent itself; then a dependency Hermes Agent pins; then general AI software; then severity; then the most recent.
- **Each paragraph gives:** identifier (CVE or advisory ID), affected component and versions, category (for example CWE), severity, whether exploitation is confirmed, whether a patch exists, source, publication date.
- **Never include exploit mechanics** or proof-of-concept detail. Category level only. Paragraphs are built from structured fields only; titles, summaries and descriptions from sources are never copied.
- **Don't repeat:** IDs and aliases reported in the last 30 briefs are excluded.

### Sources, in order

1. OSV (`https://api.osv.dev`), which carries GitHub Security Advisories and PyPI advisories, for `hermes-agent` and each dependency in its `pyproject.toml` at the version it pins.
2. NVD API (`https://services.nvd.nist.gov/rest/json/cves/2.0`), filtered by publication date and the keywords "Hermes Agent", "large language model", "LLM", "AI agent", "Model Context Protocol" and "prompt injection".
3. CVE.org records (`https://cveawg.mitre.org/api/cve/`) for affected product, versions and CWE names.
4. CISA Known Exploited Vulnerabilities for confirmed exploitation and the exploited-software counts.
5. PyPI (`https://pypi.org/pypi/hermes-agent/json`) for the latest hermes-agent release.

No source is searched for news articles. Each count links to the query that produced it instead, and CISA entries carry the vendor reference CISA lists.

MITRE ATLAS, vendor security pages and web search are not used (see `DECISIONS.md`).

A live search on 2026-09-22 found seven open CVEs against hermes-agent (for example CVE-2026-10223, CVE-2026-14628, CVE-2026-85105), several with public proof-of-concept code. On 2026-10-20 OSV listed 10 distinct advisories against hermes-agent (20 records, as each appears under both a GHSA and a PYSEC ID); 2 of them affect the latest release, 0.19.0. These IDs are not used as test fixtures.

## Output and publishing

- `daily_hermes_brief.py` runs all three parts and writes one brief per night into an archive folder: `briefs/YYYY-MM-DD.md` (the brief to read) and `briefs/YYYY-MM-DD.json` (the same facts as data), plus `state/snapshots/YYYY-MM-DD.json` (the compact catalogue tomorrow's diff needs).
- `publish_brief.py` runs it against a checkout of the `briefs` branch and pushes the new files. The branch is the published archive and the state for the next run. Nothing on it is overwritten or removed. No "latest" file.
- The brief's date is the UK calendar date at run time.
- `main` holds the code only.
- For now Claude runs `publish_brief.py` nightly at 00:00 Europe/London from a scheduled task. The owner's server (meadow-server) can run the same script later; it needs a GitHub token or deploy key limited to this repo, with push rights to `briefs` only.

## Brief schema (version 1)

Top level: `brief` ("Daily Hermes Brief"), `schema_version` (1), `date` (YYYY-MM-DD), `generated_at_utc`, `conventions`, `parts` (a list of three, in order).

Every part has `part` (1, 2 or 3), `title`, `status` and `notes` (a list of plain-language strings). A part that crashed has `status` "error" and a `text` line saying it was skipped.

| Part | Status values | Other fields |
|---|---|---|
| 1 | `ok`, `baseline`, `fetch_failed`, `suspect_response` | `source`, `checked_at_utc`, `compared_against`, `model_count`, `changes`, `expiry_rollcall`, `unparseable_models` |
| 2 | `ok`, `none`, `skipped` | `model` (the card, or null), `text` ("No model of the day." when there is no card) |
| 3 | `ok`, `partial`, `none`, `failed` | `hermes_status`, `threat_picture`, `window_start_utc`, `window_end_utc`, `findings`, `text`, `sources_checked` |

Prices are USD per million tokens throughout. Each change in Part 1 has `type`, `tier`, `id` and `name`, plus fields for its type. Each Part 3 finding has `id`, `aliases`, `scope` (`hermes`, `dependency` or `general`), `affected`, `cwe`, `severity`, `cvss`, `fixed_in`, `exploited` (KEV date, false, or null when the list could not be checked), `published`, `sources`, `found_via` and `paragraph`.

`hermes_status` (null if OSV could not be read) has `latest_release`, `latest_release_date`, `advisories_total`, `not_fixed_in_latest_release` (IDs), `minimum_version_clearing_fixed_advisories` and `advisories`, each with `id`, `cve`, `cwe`, `category`, `severity`, `cvss_vector`, `access_and_impact`, `fixed_in`, `last_affected`, `affects_latest_release`, `published` and `url`. `threat_picture` has `nvd_cves_published` (per keyword: `keyword`, `windows` of `start`, `end`, `count`, `check_url`, newest first, and `change_pct_vs_previous_window`) and `exploited_ai_software` (`ai_entries_total`, `kev_entries_total`, `ai_added_last_90_days`, `ai_added_previous_90_days`, `prompt_injection_entries`, `most_recent`, `check_url`).

## Resolved decisions

The seven open decisions were resolved during the build. `DECISIONS.md` gives the reasoning for each.

1. Reference usage volume: 10 million input and 2 million output tokens a month.
2. Exclusion window: 14 runs.
3. Tie rule for several new models: highest list price, then newest, then ID.
4. Part 3 sources: OSV, NVD, CVE.org and CISA KEV only; 72-hour window with repeats filtered.
5. One file per night with all three parts.
6. Both: Markdown is the brief, JSON holds the same facts.
7. Failures are recorded in the brief, the part is skipped, nothing hangs, nothing is overwritten.
