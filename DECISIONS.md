# Decisions

The owner asked that open decisions be taken without waiting for them, using one rule: choose the option that is less likely to break, and among those the one that uses fewer tokens. Only after being stuck on the same problem more than twice should a question go back to the owner. None has been needed so far.

Each entry gives the decision, the evidence it rests on, and the reasoning. Figures come from a live fetch of the Nous Portal catalogue on 2026-10-20 (427 entries, 333 base models, 31 of them discounted).

## The overall approach

### No language model in the nightly run

Every line of the brief is assembled by code from named fields in the sources. Nothing is summarised or written by a model.

The SPEC expected a model to do the research and writing that needs judgement. In practice the sources chosen below are all structured (JSON APIs), so the facts the SPEC asks for can be read straight out of them. Doing it in code costs no tokens, gives the same output for the same input, can't invent a figure, and keeps working if a model or API key is unavailable. It is also the strongest guarantee that no exploit mechanics reach Part 3, because the code never copies descriptive text from a security source at all.

The cost is that sources which are only readable by a model are not consulted: provider release pages, benchmark aggregator pages and changelogs (the SPEC's Branch B for proprietary models). For those models the card gives the catalogue facts and says plainly that no Hugging Face record or benchmark figures were found. If richer cards for proprietary models matter later, the natural addition is one optional model call per night that reads one provider page and returns structured fields. It was left out for now because it adds the least reliable step to the run.

### Where state lives

The `briefs` branch holds both the published archive and the state the next run needs. Earlier briefs are the run log: Part 2's exclusion list is read from the last 14 briefs, and Part 3's already-reported list from the last 30. Part 1's snapshot is saved as `state/snapshots/YYYY-MM-DD.json`, cut down to the fields the diff uses (about 89 KB a night, roughly 32 MB a year).

The run happens in a fresh cloud session each night, which starts with an empty disk. Keeping state on the branch means any machine can pick up where the last run stopped. Deriving the logs from the briefs means there is one source of truth, so the logs can never disagree with what was published. Everything on the branch is dated and added, never edited, which keeps the SPEC's rule that nothing is overwritten.

### Running and publishing

Claude runs and publishes the brief for now, via a nightly Claude scheduled task that runs `publish_brief.py`. The script checks out `briefs` in a temporary worktree, runs the brief against it, commits only the new dated files and pushes, retrying with backoff. If tonight's brief already exists it does nothing, so a repeated or overlapping run is harmless. If a push fails, the local commit is kept and goes out with the next successful push.

The schedule is 00:00 Europe/London, as the owner asked on 2026-10-20 (it was 01:07). In BST midnight in London is 23:00 UTC on the previous day, so a brief dated by UTC would carry yesterday's date and collide with the brief already written for it. Briefs and snapshots are therefore dated by UK calendar date, with every time still given in UTC. If the system has no time zone data the date falls back to UTC.

## The SPEC's open decisions

### 1. Reference usage volume: 10 million input and 2 million output tokens a month

Discounts are ranked by the USD saved at this volume. Agent work reads far more than it writes, so input is weighted five to one. On the live catalogue the top four picks are identical at 10M:2M, 3M:1M, 10M:1M and 1M:1M, so the choice is not fragile: the ranking depends on the ratio, and plausible ratios agree. The saving is used only to choose the model. It is not printed on the card, because the SPEC excludes the reason for the pick.

### 2. Exclusion window: 14 runs

The SPEC's proposed 60 was tested against the live catalogue. With 31 discounted base models, a 60-run window would use up every candidate within about a month unless discounts change, after which most nights would read "No model of the day". Fourteen runs gives two weeks before a model can return while leaving more than half the pool available on any night. Runs are counted as briefs, so missed nights don't shift the window.

### 3. Tie rule for several new models: highest list price, then newest, then ID

"List price" means the cost at the reference volume using the original price when a model launches discounted, so a model introduced at a promotional price is ranked on its real price. If that ties, the model listed on the portal most recently wins (the catalogue's `created` field), then the alphabetical ID, so the result is always the same for the same input. The same "newest wins" rule settles ties between discounts, which handles the SPEC's Gemini 3.8 Flash against 3.7 Flash example: the live catalogue has exactly that tie, and 3.8 wins.

### 4. Part 3 sources: structured databases only

Part 3 reads four sources, all free and none needing a key:

| Source | Used for |
|---|---|
| OSV (`api.osv.dev`) | GitHub Security Advisories and PyPI advisories for hermes-agent and every dependency at the exact version it pins |
| NVD API | New CVEs matching six AI and agent keywords |
| CVE.org records | Affected vendor, product and versions, and CWE names, for the chosen findings |
| CISA Known Exploited Vulnerabilities | Whether exploitation is confirmed |

GitHub's advisory API is not reachable from the cloud session, and OSV carries the same GitHub advisories with no token, so OSV replaces it. Vendor security pages, MITRE ATLAS and general web search were left out. The vendor pages are unstructured and would need a model. ATLAS classification is a judgement call, while the CWE given by the source is a fact. Web search was already cut by the owner for cost and unpredictability.

"Published today" became "published in the 72 hours before the run, and not in an earlier brief". At 01:07 the current day is barely an hour old, so a literal "today" would almost always be empty. A 72-hour window catches advisories that databases index late and covers up to two missed nights. The repeat filter stops anything appearing twice.

Ranking follows the SPEC with one addition: confirmed exploited first, then Hermes Agent itself, then a dependency Hermes Agent pins, then general AI software, then severity, then the most recent. The dependency tier exists because the SPEC names dependencies as a source, and a flaw in a library Hermes ships matters more to a Hermes user than one in an unrelated AI project.

### 5. One file per night with all three parts

One file means one read for the agent, and a brief that is either fully there or not there at all. Part 1's separate `digests/` output has gone.

### 6. Both Markdown and JSON

`briefs/YYYY-MM-DD.md` is the brief. It is laid out for an agent to read in one pass, with fixed headings, one fact per line and no narrative, and a person can read it just as easily. Markdown takes far fewer tokens than the same facts in JSON, which matters because agents read it every day. `briefs/YYYY-MM-DD.json` holds the same facts as structured data for scripts. Both are generated from one dict in one step, so they can't disagree. The README tells agents to read the Markdown file.

### 7. Failure behaviour

Every fetch has a timeout and one retry on HTTP 429. A source that fails becomes a note in the brief, and the part carries on with what it has. A part that fails completely is marked `skipped`, `failed` or `error` and the other parts are still written. Part 2 is skipped whenever Part 1's catalogue is unusable, rather than choosing from a partial list. An existing brief is never overwritten. Publishing retries the push four times, then stops and reports the failure. Nothing waits indefinitely.

## Part 3's purpose, refined on 2026-10-20

The owner clarified that Part 3 is there to give an agent the evolving threat picture for Hermes setups, so it can judge its own security posture. For example: is prompt injection against agents happening, or still mostly theoretical? Individual advisories alone don't answer that, and the "never repeat" rule meant an agent reading only the newest brief never learnt about older advisories. Two standing sections were added, repeated every night, and the daily advisories kept below them.

**Hermes Agent security status.** One OSV request returns every advisory for hermes-agent and one PyPI request gives the latest release, so the brief can say which advisories affect the version an agent is likely to run, and which version to update to and why. OSV lists each advisory twice (GHSA and PYSEC); they are merged, preferring the GitHub record. On 2026-10-20 that gives 10 advisories, not 20; an earlier count in this project's notes double-counted them. Technical detail is given as the CWE category and the CVSS vector in plain words (attack vector, complexity, privileges, user interaction, impact). That says what kind of flaw it is and how it can be reached without describing how to exploit it, which keeps to the rule against exploit mechanics.

**Threat picture, measured.** The owner asked for "more often" and "growing concern" to be quantified and checkable. Each keyword gets CVE counts for three consecutive 30-day windows of whole UTC days and the percentage change on the previous window, with the exact NVD API query for the newest count; opening it returns `totalResults`, which matches the brief. Exploitation is measured from CISA's list: the number of AI and agent products on it, how many were added in the last 90 days against the 90 before, and how many entries are classed CWE-1427 (prompt injection). Matching uses a fixed list of AI product names, so the count is a floor and the brief says so. Each recent entry carries the vendor reference CISA gives.

No source is searched for news articles. Finding articles needs web search and a model to read them, both cut for cost and reliability; the check links and vendor references let a person verify every figure instead. The extra requests are 12 small NVD counts (one result per page), one OSV query and one PyPI request, which add about a minute to the run because of NVD's rate limit, and no tokens.

What the measures showed on 2026-10-20: CVEs mentioning prompt injection rose from 14 to 24 between the last two 30-day windows (+71%), and those mentioning Model Context Protocol from 11 to 30 to 43 across three. Twelve entries on CISA's exploited list are AI or agent software; none of its 1,748 entries is classed as prompt injection, and every recent AI entry is a conventional flaw such as missing authentication, code injection or server-side request forgery. The brief reports these figures without interpreting them.

## Hermes Agent status made readable, 2026-10-20

The owner's agent, running hermes-agent 0.21.5, reported that the status section was hard to read: two advisories were shown as "no fixed release, affects versions up to 0.19.0" under the heading "Affecting the latest release", and the latest release was given as 0.19.0. It could tell it was beyond the named versions but not what that meant.

Two faults sat behind that. First, the latest release came from PyPI, which stopped at 0.19.0 on 2026-07-20; GitHub releases carried on (`v0.22.2` on 2026-10-20, and tag `v2026.9.24` is package version 0.21.5). The latest release is now the highest stable `vX.Y.Z` tag on GitHub. Calendar tags (`v2026.9.24`) and canary builds (`+canary...`) are ignored. The plain git ref listing is about 10 MB because it includes every pull request, so the brief asks for tags only using git's protocol v2 `ls-refs` with a `refs/tags/v` prefix, which returns about 7 KB. PyPI's newest version is still reported, as a fact, and is the fallback if GitHub can't be read.

Second, an advisory with no fixed version was treated as either affecting the latest release or not, and "not" was filed under fixed. There are now three states: fixed; no fix named and the latest release is within the affected range; no fix named and the latest release is beyond it. The last is worded exactly as the data supports: the advisory neither names that release as affected nor confirms a fix. That is the uncertainty the agent described, now stated for it rather than left for it to work out.

The list became a summary and a table. The summary gives the counts and one sentence per unfixed advisory; the table gives one row per advisory with the affected range in comparison form (`< 0.18.0`, `<= 0.19.0`) so an agent can compare its own version directly. How a flaw is reached and its impact are split into short columns ("network, no login, no user action, low complexity" and "none/none/low") rather than one long sentence. The table is also fewer tokens than the previous list.

## Other decisions made while building

**Part 1 bugs fixed.** When a new model's `:free` variant came before the base model in the catalogue, the new-model record used the variant and reported a price of zero. It now uses the base ID. Entries such as `~anthropic/claude-opus-latest` are floating pointers to another model; they were counted as new models and could have been featured, and now they are not. A variant repeating its base model's expiry change is no longer reported twice. Dropped connections and similar network errors used to crash the run; they now count as a failed fetch. The new-model record now carries the list price, which Part 2's tie rule needs.

**Exploitation means a CISA KEV listing.** It is the only structured, authoritative "exploited in the wild" signal. The brief says "not confirmed" rather than "not exploited" when a CVE is absent from the list.

**Nothing descriptive is copied from security sources.** Part 3 paragraphs are built from identifiers, product names, version ranges, CWE IDs and names, CVSS figures, dates and URLs. Advisory titles, summaries and descriptions are never used, because they are where exploit mechanics usually appear. Each paragraph says which search found it, so the reader can see why an unfamiliar product counts as AI-related.

**The catalogue description is quoted.** On the model card it is labelled as the catalogue's description, so the provider's own words are not presented as the brief's judgement.

**Code stays on the working branch.** CLAUDE.md says not to push to `main`, and the owner allowed committing without review but did not mention merging. The nightly task runs the code from the working branch. Merging to `main` is a one-step change if wanted.
