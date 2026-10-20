# Instructions for Claude

You are continuing work on the Daily Hermes Brief. The owner designed it in a long conversation; everything decided is in `SPEC.md`.

## Before you start

1. Read `SPEC.md` in full. It is the source of truth.
2. Read `README.md` and `daily_hermes_brief_part1.py`. Part 1 is built and tested; match its style (Python standard library where possible, plain functions, clear failure notes in the output).
3. Check the "Open decisions" list in `SPEC.md`. Ask the owner about any that affect what you're about to build. Don't guess.

## Work order

1. Align output: one brief per night at `briefs/YYYY-MM-DD.json` once the owner confirms the file shape (open decision 5).
2. Build Part 2, model of the day.
3. Build Part 3, AI security news of the day.
4. Add a single entry point that runs all three parts and writes one brief.
5. Test each part against the live Nous Portal catalogue and live sources, and against simulated data for edge cases (no new models, no discounts, everything excluded, portal down, Hugging Face 404).

## Rules

- Facts, not judgement calls. Dates and figures, no "soon", no "worth it".
- Never include exploit mechanics or proof-of-concept detail in Part 3.
- Keep fetches lean to save tokens: the specific page or API call, not whole sites.
- Work on a branch and open a pull request for the owner to review. Don't push to `main` directly.
- Never write to the `briefs` branch; only the owner's server publishes there.
- Never send or post anything to anyone.

## Writing style for README and docs

UK English. Plain and direct. At most three em dashes per document. Prose over bullet points unless something is genuinely a list.
