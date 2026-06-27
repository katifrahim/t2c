# Claude Code Guidelines

## Commit Messages

- Do NOT include `Co-Authored-By: Claude` or any AI attribution lines in commit messages.

## Git Pull Protocol

- Before every `git pull`: run `git fetch origin` first, then `git diff <branch> origin/<branch>` to check for differences. If the local branch has commits not present on remote, inform the user and ask for permission before pulling.

## Branching

- Before creating a new branch: first `git checkout main`, then follow the Git Pull Protocol above, then `git checkout -b <branch>`. Never branch off a feature branch unless explicitly asked.
- After the user merges a PR and confirms it, delete the branch locally with `git branch -d <branch>`.

## CI Tests

- Before creating a PR that includes changes to `mcp_server/src/t2c_mcp.py`, ask the user if they want to run CI tests. If yes, add the label `run-ci` to the PR (`--label "run-ci"`).

## Branch Hygiene

- Occasionally run `git branch` to check all local non-main branches. For each, verify if it has been pushed, PR'd, and merged into `origin/main`. If yes, delete it locally with `git branch -d <branch>`.

## Learning & Self-Improvement

- Whenever a mistake is made or a user preference is learned, document it in this file immediately to prevent repeating it.
