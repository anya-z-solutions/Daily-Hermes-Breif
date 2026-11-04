# Instructions for Claude

You are continuing work on the Daily Hermes Brief: a nightly brief, readable by a person but written for offline AI agents, that gives them up-to-date facts about the Nous Portal model catalogue, one model in depth, and AI security news.

## Before you start

1. Read `SPEC.md` in full. It is the source of truth, including the brief schema.
2. Read `DEFINITION_OF_DONE.md`. Work is finished when it meets every check there; the owner does not review it.
3. Read `DECISIONS.md`. Don't reopen a decision recorded there without new evidence; if you have some, record the new decision and its reasoning in the same file.
4. Match the existing code: Python standard library only, plain functions, every network call through `brief_common.get_json` or `get_text`, clear failure notes in the output.

## Making decisions

Don't wait for the owner. Choose the option that is less likely to break, and among those the one that uses fewer tokens. Write the decision and the evidence for it in `DECISIONS.md`. Only if you are stuck on the same problem more than twice, ask the owner.

## Files and who changes them

| File | Contents |
|---|---|
| `daily_hermes_brief_part1.py`, `_part2.py`, `_part3.py` | One part each. A change to one part touches only its own module and its own test file. |
| `brief_common.py` | Fetching, time and formatting helpers shared by all parts. |
| `daily_hermes_brief.py` | Runs the three parts, renders Markdown, writes the brief. Owns the schema in practice: change it and `SPEC.md` together. |
| `publish_brief.py` | Runs the brief against the `briefs` branch and pushes it. |
| `tests/` | `python3 -m unittest discover -s tests`. Offline; fakes every source. |

If several agents work at once, give each its own branch and one part, and leave `daily_hermes_brief.py`, `SPEC.md` and `README.md` to a single agent.

## Rules

- Facts, not judgement calls. Dates and figures, no "soon", no "worth it".
- Never include exploit mechanics or proof-of-concept detail in Part 3. Never copy titles, summaries or descriptions from security sources; build paragraphs from structured fields only.
- Keep fetches lean: the specific API call, not whole sites.
- Tests never touch the network, a real archive or the `briefs` branch.
- Push code only to your working branch. Don't push to `main` directly.
- Write to the `briefs` branch only by running `publish_brief.py`. Never edit, rename or delete a file already on that branch.
- Apart from that, never send or post anything to anyone.

## Writing style for README and docs

UK English. Plain and direct. At most three em dashes per document. Prose over bullet points unless something is genuinely a list.
