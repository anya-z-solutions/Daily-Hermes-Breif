# Daily Hermes Brief

A nightly brief for AI agents that work offline. It records what has changed in Nous Portal's model catalogue, features one model in depth, and reports the day's AI security news that matters to anyone running Hermes Agent. Agents without live web access read it as a plain file instead of running searches they can't make.

This is an independent project. It is not affiliated with or endorsed by Nous Research.

## Why this exists

Agents running air-gapped, or without live web access, still need to know what has changed: a new model released, a discount worth knowing about, a vulnerability disclosed against the framework they run on. The brief gives them that as a small, dated file.

It is purely informational. It reports facts and dates; the reader decides what to do with them. Where a choice can't be avoided, such as which model to feature, the brief applies a fixed, explainable rule rather than an opinion, so the same logic produces the same kind of answer every night.

## What's in each day's brief

Each brief is dated and has three parts.

**Part 1: Nous Portal catalogue changes.** What is different since the last good snapshot: new models, models newly carrying an expiry date or with a changed one, discounts starting or ending, and price rises of more than 20%. Each change is tiered *note* (worth knowing) or *act* (worth acting on); minor changes are skipped. Part 1 also lists every model currently carrying an expiry date, dates only, every night. It states which date it was compared against, and if the portal could not be reached it says so, with the time it was checked.

**Part 2: Model of the day.** One model, researched in depth. A newly released model always wins. Otherwise the currently discounted model with the highest value saved is chosen, excluding any model featured in recent runs. The card draws on the catalogue itself, then Hugging Face for open-weight models or the provider's own pages for proprietary ones. If nothing qualifies, the brief says so.

**Part 3: AI security news of the day.** Up to three findings published that day, focused on Hermes Agent but not limited to it: anything someone running a Hermes setup would want to know. One paragraph each, with the category, severity and whether it is confirmed as exploited. Exploit mechanics are never included. If there is no security news, the brief says so.

## Where the briefs are

The code lives on the `main` branch. The briefs are published on the separate `briefs` branch as a dated archive, one file per night, for example `briefs/2026-10-21.json`. Nothing is overwritten or removed.

## Reading the briefs with an agent

Have your agent read only the newest file each day. Reading one brief costs roughly 2,000 to 4,000 tokens; re-reading the whole archive every day would cost more as it grows, and adds nothing. Parts 2 and 3 are evergreen research and stay useful later, so the archive is worth keeping for when your agent needs to look something up.

## Running it yourself

The script needs Python 3 and nothing else. It runs on Linux, macOS and Windows.

```
python3 daily_hermes_brief_part1.py --dry-run   # print tonight's result, write nothing
python3 daily_hermes_brief_part1.py             # normal run
```

## Source

Nous Portal's public model catalogue: `https://inference-api.nousresearch.com/v1/models`

## Status

Part 1 is built and tested. Parts 2 and 3 are specified in [SPEC.md](SPEC.md) and not yet built.

## Licence

MIT
