# Definition of done

The owner does not review this project's work. A task is therefore done when the agent doing it has met every check below and recorded the result, not when someone approves it. Decisions the owner would otherwise make are taken by the agent under the rule in `DECISIONS.md` (prefer the option that is less likely to break, then the one that uses fewer tokens) and written down there.

## A task is done when

1. **It is built to `SPEC.md`.** Where the SPEC left a decision open, the agent has made it, recorded the reasoning in `DECISIONS.md`, and updated the SPEC to match. No placeholder values are left in code.
2. **Its output matches the brief schema in `SPEC.md`.** Field names and status values are the ones listed there, so readers can rely on them every night.
3. **Its tests are committed and pass.** They use Python's built-in `unittest`, run with `python3 -m unittest discover -s tests`, and need no network. They cover the edge cases that apply to the part: no new models, no discounts, everything excluded, portal down, Hugging Face 404, sources down, repeats.
4. **Tests leave real state alone.** Tests write only to temporary folders. No test touches the `briefs` branch or a real archive.
5. **It has been run against the live sources.** At least one dry run against the live Nous Portal catalogue and live security sources has completed, and its output has been read for errors and for any exploit detail.
6. **Failures are recorded, never hung on.** Every network call has a timeout. A failed source produces a note in the brief and the run carries on. A failed part is marked failed and the other parts are still written.
7. **It reads like Part 1.** Python standard library only, plain functions, failure notes in plain language.
8. **The docs are current.** `README.md`, `SPEC.md` and `DECISIONS.md` describe what was built, in the CLAUDE.md writing style.
9. **It is committed and pushed** to the agent's working branch. Generated briefs and state are never committed to a code branch.

## The project is done when

1. One entry point (`daily_hermes_brief.py`) runs all three parts and writes one brief per night, as `briefs/YYYY-MM-DD.md` and `briefs/YYYY-MM-DD.json`.
2. `publish_brief.py` has published at least one brief to the `briefs` branch.
3. A nightly schedule runs `publish_brief.py` without anyone present.
4. A second night's brief has been published with Part 1 status `ok`, which proves the snapshot carried over and the diff ran. Until that has happened the project is working but not proven.

## Checking it

```
python3 -m unittest discover -s tests              # all tests, offline
python3 daily_hermes_brief.py --dry-run            # tonight's brief from live sources, writes nothing
```
