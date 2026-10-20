# Daily Hermes Brief

A nightly brief for AI agents that work offline. It records what has changed in Nous Portal's model catalogue, features one model in depth, and reports the day's AI security news that matters to anyone running Hermes Agent. Agents without live web access read it as a plain file instead of running searches they can't make.

This is an independent project. It is not affiliated with or endorsed by Nous Research.

## Why this exists

Agents running air-gapped, or without live web access, still need to know what has changed: a new model released, a discount worth knowing about, a vulnerability disclosed against the framework they run on. The brief gives them that as a small, dated file.

It is purely informational. It reports facts and dates; the reader decides what to do with them. Where a choice can't be avoided, such as which model to feature, the brief applies a fixed, explainable rule rather than an opinion, so the same logic produces the same kind of answer every night.

## What's in each day's brief

Each brief is dated and has three parts.

**Part 1: Nous Portal catalogue changes.** What is different since the last good snapshot: new models, models newly carrying an expiry date or with a changed one, discounts starting or ending, and price rises of more than 20%. Each change is tiered *note* (worth knowing) or *act* (worth acting on); minor changes are skipped. Part 1 also lists every model currently carrying an expiry date, dates only, every night. It states which date it was compared against, and if the portal could not be reached it says so, with the time it was checked.

**Part 2: Model of the day.** One model, described in depth. A newly listed model always wins. Otherwise the currently discounted model with the largest USD saving is chosen, excluding any model featured in the last 14 briefs. The card draws on the catalogue itself, then Hugging Face for open-weight models: licence, base model, linked papers, self-reported benchmarks, Spaces and discussion counts. If nothing qualifies, the brief says so.

**Part 3: AI security news of the day.** Up to three advisories published in the last 72 hours and not reported before, focused on Hermes Agent and the libraries it pins but not limited to them. One paragraph each, with the affected versions, category, severity, whether exploitation is confirmed and whether a fix exists. The paragraphs are built from structured fields only, so exploit mechanics are never included. If there is no security news, the brief says so.

## Where the briefs are

The briefs are published on the separate `briefs` branch as a dated archive, one brief per night, for example `briefs/2026-10-21.md`. Each has a `.json` twin with the same facts as structured data. Nothing is overwritten or removed.

## Reading the briefs with an agent

Have your agent read only the newest `.md` file each day. Re-reading the whole archive every day would cost more as it grows, and adds nothing. Parts 2 and 3 stay useful later, so the archive is worth keeping for when your agent needs to look something up. Scripts that want fields rather than text can read the `.json` file instead; its shape is in [SPEC.md](SPEC.md).

## Running it yourself

The scripts need Python 3.8 or later and nothing else. They run on Linux, macOS and Windows. No API keys are needed.

```
python3 daily_hermes_brief.py --dry-run          # print tonight's brief, write nothing
python3 daily_hermes_brief.py --archive DIR      # write tonight's brief into DIR/briefs
python3 publish_brief.py                         # run against the briefs branch and push
python3 -m unittest discover -s tests            # run the tests, offline
```

Each part can also be run on its own for checking (`daily_hermes_brief_part1.py`, `_part2.py`, `_part3.py`); those print and write nothing. Part 3 pauses between NVD requests to stay inside NVD's rate limit, so a full run takes about a minute.

## Sources

Nous Portal's public model catalogue (`https://inference-api.nousresearch.com/v1/models`), Hugging Face, arXiv, OSV, NVD, CVE.org and CISA's Known Exploited Vulnerabilities list. All are public and need no authentication.

## Status

All three parts are built and tested, and briefs are published nightly. [SPEC.md](SPEC.md) describes what the brief does, [DECISIONS.md](DECISIONS.md) explains the choices made while building it, and [DEFINITION_OF_DONE.md](DEFINITION_OF_DONE.md) sets out what finished means.

## Licence

MIT
