---
name: project-operations
description: >-
  Use whenever asked to check status, run a sweep, produce a sitrep, or
  unblock work across the ProtoMaker portfolio. Describes how to read a
  ProtoMaker workspace's state with the read-only filesystem tools and decide
  whether work is flowing, stalled, or blocked — and what to do about it.
tools:
  - list_projects
  - read_file
  - list_dir
  - find_files
  - search_files
  - run_command
  - check_inbox
  - schedule_task
---

# Keeping a ProtoMaker portfolio flowing

You monitor and unblock — you never edit code. Everything below is *reading*
state and *acting* by nudging / dispatching / escalating.

## A ProtoMaker workspace

Each managed project is a git repo that is also a ProtoMaker workspace:

- **Board** — `.beads/issues.jsonl` (one JSON object per line). Read it with
  `read_file <project> .beads/issues.jsonl`. Each issue has `status`
  (`open` / `in_progress` / `blocked` / `closed`), `priority`, `issue_type`,
  `title`, `labels`, `dependencies`. The board is the source of truth for what
  work exists and where it's stuck.
- **Feature pipeline** — `.automaker/features/` (queued/active features),
  `.automaker/projects/` (project specs). `find_files <project> ".automaker/**"`
  to enumerate; `read_file` the ones that matter.
- **Git / PRs / CI** — `run_command <project> "git status"`,
  `run_command <project> "git log --oneline -10"`,
  `run_command <project> "gh pr list --state open"`,
  `run_command <project> "gh pr checks <n>"`. (Read-only commands only.)

Start a sweep with `list_projects` to see the portfolio + which are read-only.

## Deciding: flowing / stalled / blocked

For each project:

- **✓ flowing** — open/in_progress issues have recent commit or PR activity;
  PRs are progressing; CI green.
- **⚠ stalled** — open work with **no recent activity** (no commits/PR movement
  in N days), a PR idle past N days, **red CI**, or a dirty tree on the main
  branch. Stalls are *capacity/attention* problems.
- **⛔ blocked** — an issue explicitly `blocked`, a feature waiting on a
  dependency / decision / human, or a dependency edge pointing at unfinished
  work. Blockers are *decision/dependency* problems.

Never fabricate: if a workspace can't be read (missing `.beads`, git error),
report it as "unknown — couldn't read state" rather than guessing.

## Acting (smallest unblock first)

1. **Coordinate, don't collide.** Before touching a feature, check whether a
   ProtoMaker agent already owns it (board `status: in_progress` + recent
   activity). If so, leave it — don't thrash in-flight work.
2. **Stalled** → nudge: re-dispatch the ProtoMaker team for that project (via
   the Studio MCP / Workstacean bus if available), or create/raise a feature to
   resume the work.
3. **Blocked** → if the blocker is mechanical (a dependency that's actually
   done, a stale flag), note it; if it needs a human call, **escalate** with a
   crisp ask (what's blocked, why, the decision needed).
4. **Escalate** via the inbox / Activity thread for anything consequential or
   irreversible. Report the sweep back to whoever summoned you.

## Output

Lead with a one-line portfolio roll-up (`N flowing · M stalled · K blocked`),
then a one-liner per project: `✓ flowing` / `⚠ stalled — <reason>` /
`⛔ blocked — <reason> → <action taken or escalation>`. Name the project, the
signal, and the action. No filler.
