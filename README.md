# Daily Hermes Brief

A nightly brief, written for AI agents that work offline but just as readable by a person. It records what changed in Nous Portal's model catalogue, describes one model in depth, and sets out the security picture for anyone running Hermes Agent.

This is an independent project. It is not affiliated with or endorsed by Nous Research.

## The components

The brief is a handful of small Python scripts that use only the standard library, so there is nothing to install beyond Python itself.

| File | What it does |
|---|---|
| `daily_hermes_brief_part1.py` | Part 1: compares tonight's Nous Portal catalogue with the last saved copy |
| `daily_hermes_brief_part2.py` | Part 2: picks the model of the day and builds its card |
| `daily_hermes_brief_part3.py` | Part 3: Hermes Agent's security status, the threat picture and new advisories |
| `brief_common.py` | Fetching, dates and formatting shared by all three parts |
| `daily_hermes_brief.py` | Runs the three parts and writes one brief, as Markdown and JSON |
| `publish_brief.py` | Runs the brief against the `briefs` branch and pushes the new files |
| `tests/` | Offline tests for every part, using saved sample data |

Each night produces three files in an archive folder: `briefs/YYYY-MM-DD.md` (the brief), `briefs/YYYY-MM-DD.json` (the same facts as data) and `state/snapshots/YYYY-MM-DD.json` (a compact copy of the catalogue for tomorrow's comparison).

## The approach

The scripts fetch a short list of public, structured sources: the Nous Portal catalogue, Hugging Face, arXiv, OSV, NVD, CVE.org, GitHub release tags, PyPI and CISA's list of exploited vulnerabilities. They compare tonight's data with what earlier briefs recorded and assemble every line of the brief from named fields in that data. No language model is involved at any point. Earlier briefs double as the run log, so the archive is all the state the next run needs.

## Why it is designed this way

**Facts, not opinions.** The reader is an agent that will act on what it reads, so the brief gives dates, figures and sources and leaves the judgement to the reader. Where a choice can't be avoided, such as which model to feature, a fixed rule decides, so the same data always gives the same answer.

**No AI in the run.** Because every source is structured, code can read the facts directly. That costs no tokens, gives the same output for the same input, can't invent a figure, and keeps working if no model or API key is available.

**No exploit detail, by construction.** Security paragraphs are built from identifiers, version ranges, categories, scores and dates. Advisory titles and descriptions are never copied, because that is where attack details usually appear.

**Resilient by default.** Every request has a time limit. A source that fails becomes a note in the brief and the rest carries on. A brief is never overwritten, and a failed publish is retried on the next run.

**Cheap to read.** Agents read the brief every day, so it is plain Markdown with fixed headings and one fact per line, which takes far fewer tokens than the same facts in JSON.

The reasoning behind each choice, with the evidence for it, is in [DECISIONS.md](DECISIONS.md).

## What each brief contains

**Part 1: Nous Portal catalogue changes.** What changed since the last saved copy: new models, new or changed expiry dates, discounts starting or ending, and price rises above 20%. Each change is marked *act* (worth acting on) or *note* (worth knowing). It also lists every model that has an expiry date, and states the date it compared against.

**Part 2: Model of the day.** A newly listed model always wins. Otherwise it picks the discounted model with the largest USD saving, skipping any featured in the last 14 briefs. The card gives pricing, context window, modalities and dates from the catalogue, plus licence, base model, linked papers, self-reported benchmarks and community figures from Hugging Face for open-weight models.

**Part 3: AI security news.** First, Hermes Agent's status: the latest release, and every known advisory with its category, how it can be reached and the version that fixes it, set out in a table an agent can check its own version against. Second, a measured threat picture: CVE counts for prompt injection, LLM, AI agent and Model Context Protocol flaws over three 30-day windows, each with a link that reproduces the count, and how many AI products are on CISA's exploited list. Third, up to three new advisories from the last 72 hours.

Have your agent read only the newest `.md` file each day. The older briefs are an archive to look things up in. The JSON's layout is in [SPEC.md](SPEC.md).

## Setting it up

Both options produce the same brief. Option 1 keeps everything on your own computer. Option 2 needs no computer left switched on and publishes to GitHub.

### Option 1: on your own computer

You need Python 3.8 or later. It runs on Windows, macOS and Linux. No accounts or API keys are needed.

1. **Install Python** from [python.org](https://www.python.org/downloads/) if `python3 --version` (or `python --version` on Windows) doesn't show 3.8 or later.
2. **Download the project.** On GitHub, choose **Code**, then **Download ZIP**, and unzip it into a folder you'll keep, such as `Documents/daily-hermes-brief`.
3. **Check it works.** Open a terminal in that folder and run `python3 daily_hermes_brief.py --dry-run` (on Windows, type `python` instead of `python3` here and below). After about a minute it prints tonight's brief and saves nothing.
4. **Write a real brief.** Run `python3 daily_hermes_brief.py --archive briefs-archive`. The brief appears in `briefs-archive/briefs/`. Point your agent at that folder.
5. **Run it every night.**
   - On macOS or Linux, run `crontab -e` and add this line, using your own folder path:
     `5 1 * * * cd /path/to/daily-hermes-brief && python3 daily_hermes_brief.py --archive briefs-archive >> brief.log 2>&1`
   - On Windows, open **Task Scheduler** and choose **Create Basic Task**. Set it to run **Daily** at 01:05. For the action, choose **Start a program**: the program is `python`, the arguments are `daily_hermes_brief.py --archive briefs-archive`, and "Start in" is your project folder.

The computer has to be on at that time. If it misses a night, the next brief says how many days it covers.

### Option 2: on GitHub, with no computer needed

GitHub Actions runs the brief every night on GitHub's machines and publishes it to a `briefs` branch in your copy of the project. You need a free GitHub account.

1. **Make your own copy.** On this project's GitHub page, choose **Fork**, then **Create fork**.
2. **Turn on Actions.** In your fork, open the **Actions** tab and choose **I understand my workflows, go ahead and enable them**. GitHub switches scheduled workflows off in new forks until you do this.
3. **Allow it to publish.** Go to **Settings**, then **Actions**, then **General**. Under **Workflow permissions**, choose **Read and write permissions** and save.
4. **Run it once by hand.** In **Actions**, choose **Nightly brief** in the list on the left, then **Run workflow**. After a couple of minutes a green tick shows it worked, and a new `briefs` branch holds the first brief.
5. **Leave it.** From then on it runs every night at 00:05 UTC. Your agents can read the newest file from the `briefs` branch, or you can download it from GitHub.

The schedule is in `.github/workflows/nightly-brief.yml` if you want a different time. GitHub may pause scheduled workflows in a repository that has seen no activity for 60 days; if that happens, open the Actions tab and switch it back on.

## For developers

```
python3 -m unittest discover -s tests            # run the tests, offline
python3 daily_hermes_brief.py --dry-run          # print tonight's brief, write nothing
python3 daily_hermes_brief.py --archive DIR      # write tonight's brief into DIR
python3 publish_brief.py                         # run against the briefs branch and push
```

Each part can be run on its own (`daily_hermes_brief_part1.py`, `_part2.py`, `_part3.py`) to print just that part. A run takes under a minute, most of it pauses to stay inside NVD's rate limit. CISA's exploited list is cached in `~/.cache/daily-hermes-brief` (set `HERMES_BRIEF_CACHE` to move it) and downloaded again only when it has changed.

[SPEC.md](SPEC.md) describes what the brief must do, [DECISIONS.md](DECISIONS.md) why it is built this way, and [DEFINITION_OF_DONE.md](DEFINITION_OF_DONE.md) what counts as finished.

## Licence

MIT
