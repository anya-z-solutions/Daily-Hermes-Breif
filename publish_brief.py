#!/usr/bin/env python3
"""
Daily Hermes Brief: run tonight's brief and publish it to the `briefs` branch.

The `briefs` branch is both the published archive and the state the next run
needs (earlier briefs and catalogue snapshots), so a fresh machine or a fresh
cloud session can pick up exactly where the last run stopped.

Steps: check out `briefs` in a temporary worktree, run daily_hermes_brief.py
against it, commit only the new dated files, push, remove the worktree.
Nothing on the branch is ever modified or deleted. If tonight's brief already
exists the script does nothing.

Usage:
    python3 publish_brief.py              # run, commit, push
    python3 publish_brief.py --no-push    # run and commit locally only
"""

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

import daily_hermes_brief

REPO = Path(__file__).resolve().parent
WORKTREE = REPO / ".briefs-worktree"
BRANCH = "briefs"
PUSH_WAITS = (2, 4, 8, 16)

BRANCH_README = """# Daily Hermes Brief archive

One brief per night. Agents should read only the newest file in `briefs/`:

- `briefs/YYYY-MM-DD.md`: the brief.
- `briefs/YYYY-MM-DD.json`: the same facts as structured data.

`state/snapshots/` holds the compact catalogue each run compares against. Agents can ignore it.

Files are added, never changed or removed. The code is on the `main` branch.
"""


def git(*args, cwd=REPO, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, text=True, capture_output=True)


def with_retries(*args, cwd=REPO):
    """Run a network git command, retrying with backoff. Returns the final CompletedProcess."""
    result = git(*args, cwd=cwd, check=False)
    for wait in PUSH_WAITS:
        if result.returncode == 0:
            break
        time.sleep(wait)
        result = git(*args, cwd=cwd, check=False)
    return result


def remove_worktree():
    if WORKTREE.exists():
        git("worktree", "remove", "--force", str(WORKTREE), check=False)
        shutil.rmtree(WORKTREE, ignore_errors=True)
    git("worktree", "prune", check=False)


def prepare_worktree():
    remove_worktree()
    fetched = with_retries("fetch", "origin", BRANCH)
    remote_exists = git("rev-parse", "--verify", "--quiet", f"origin/{BRANCH}", check=False).returncode == 0
    local_exists = git("rev-parse", "--verify", "--quiet", BRANCH, check=False).returncode == 0
    if remote_exists:
        ahead = local_exists and git("merge-base", "--is-ancestor", f"origin/{BRANCH}", BRANCH,
                                     check=False).returncode == 0
        if ahead:      # keep local briefs whose push failed on an earlier night
            git("worktree", "add", "--force", str(WORKTREE), BRANCH)
        else:
            git("worktree", "add", "--force", "-B", BRANCH, str(WORKTREE), f"origin/{BRANCH}")
        return
    if fetched.returncode != 0 and "couldn't find remote ref" not in fetched.stderr:
        raise RuntimeError(f"Could not fetch the {BRANCH} branch: {fetched.stderr.strip()}")
    if local_exists:   # never pushed yet, but earlier local runs exist
        git("worktree", "add", "--force", str(WORKTREE), BRANCH)
        return
    # First ever run: start an empty branch with no code history.
    git("worktree", "add", "--force", "--detach", str(WORKTREE))
    git("checkout", "--orphan", BRANCH, cwd=WORKTREE)
    git("rm", "-rfq", ".", cwd=WORKTREE, check=False)
    for leftover in WORKTREE.iterdir():
        if leftover.name != ".git":
            shutil.rmtree(leftover) if leftover.is_dir() else leftover.unlink()
    (WORKTREE / "README.md").write_text(BRANCH_README, encoding="utf-8")


def unpushed_briefs():
    """Commits on the local briefs branch that origin doesn't have yet, e.g. after a failed push."""
    has_remote = git("rev-parse", "--verify", "--quiet", f"origin/{BRANCH}", check=False).returncode == 0
    span = f"origin/{BRANCH}..HEAD" if has_remote else "HEAD"
    log = git("log", "--format=%s", span, cwd=WORKTREE, check=False)
    return [line for line in log.stdout.splitlines() if line.strip()][::-1]


def main():
    parser = argparse.ArgumentParser(description="Run and publish the Daily Hermes Brief")
    parser.add_argument("--no-push", action="store_true", help="commit locally; do not push")
    args = parser.parse_args()

    try:
        prepare_worktree()
        written = daily_hermes_brief.run_nightly(WORKTREE)
        if written is None:
            return 1
        if written:
            paths = [str(p.relative_to(WORKTREE)) for p in written]
            if (WORKTREE / "README.md").exists():
                paths.append("README.md")
            git("add", "--", *paths, cwd=WORKTREE)
            git("commit", "-q", "-m", f"Brief {written[0].stem}", cwd=WORKTREE)
        waiting = unpushed_briefs()
        if not waiting:
            return 0                          # already published; nothing to do
        if args.no_push:
            print(f"Committed {', '.join(waiting)} to {BRANCH} locally; not pushed.")
            return 0
        pushed = with_retries("push", "-u", "origin", BRANCH, cwd=WORKTREE)
        if pushed.returncode != 0:
            print(f"Push failed: {pushed.stderr.strip()}", file=sys.stderr)
            return 1
        print(f"Published {', '.join(waiting)} to the {BRANCH} branch.")
        return 0
    except (subprocess.CalledProcessError, RuntimeError, OSError) as e:
        detail = getattr(e, "stderr", "") or str(e)
        print(f"Publishing failed: {detail.strip()}", file=sys.stderr)
        return 1
    finally:
        remove_worktree()


if __name__ == "__main__":
    sys.exit(main())
