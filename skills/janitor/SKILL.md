---
name: janitor
description: Safe workspace cleanup — report and trash known agent-workflow junk (stray nul/null files, __pycache__, path-flattened tool dumps, iteration clones) with a restore manifest. Use when tidying a project folder, when an agent session left clutter, when a folder "is a mess" or needs a sweep, before archiving an engagement, or to restore something the janitor trashed. Never deletes — everything is recoverable.
allowed-tools: Bash(python3 *scan.py *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/scan.py *)
---

# Janitor — safe, registry-only workspace cleanup

The scanner is `${CLAUDE_PLUGIN_ROOT}/scripts/scan.py`. It is **read-only by default**
and **never deletes** — `--apply` moves Tier-1 junk to a trash store with a restore
manifest at `~/.local/share/janitor-trash/<project>/<timestamp>/`.

## Safety contract (never weaken these)

1. A file is junk **only** if it matches a rule in `scripts/rules.json`. Every rule
   names its documented cause. No vibes-based matching.
2. Trash, never `rm`. Restores always work.
3. `--apply` touches **Tier 1 only**. Tier 2/3 are report-only — your judgment plus
   the user's, never the script's.
4. Hard allowlist + 4-hour mtime guard: creds (`*.ccache *.pfx *.key *.kdbx`), notes
   (`*.md`), config files, `loot/`, `writeup/`, anything containing `creds`/`writeup`/
   `ENGAGEMENT_STATE`/`session_log`, and anything a live session touched recently.
5. A project `.janitor-ignore` (gitignore-style, one pattern per line) can protect
   **more** paths. It can never nominate new junk.

## Procedure

1. **Always scan first, report-only:**
   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/scan.py scan <project-root>
   ```
2. **Tier 1** (auto-trashable: provable accidents like `nul`/`nul^`, regenerable
   caches, day-old ≤3-byte files) — safe to apply:
   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/scan.py scan <root> --apply
   ```
3. **Tier 2** (tool-spray signatures like `_home_*.txt` path-flattened dumps) —
   verify a real copy exists elsewhere first (`diff`, `ls` the expected location),
   then trash it by moving it to the trash store the same way, and say so in your
   summary. If no duplicate exists, leave it and report.
4. **Tier 3** (iteration clones `foo2.py`, `*.old.*`, `res-*` scraps, numbered logs) —
   never delete on your own. Present the list, recommend keep/drop per file using
   project state (engagement state files, git history, the newest variant wins),
   and let the user confirm. When the project has a state file with a dead-ends
   section, cross-reference it: a lane marked dead means its scratch is disposable.
5. **Report what happened**, including restore instructions:
   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/scan.py restore --list
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/scan.py restore --latest
   ```

## Modes

Hooks (NUL-redirect blocking, post-command sweep, stop-report census) read
`~/.config/janitor/config.json`:

- `report` (default) — hooks only observe and annotate; nothing is moved or blocked.
- `enforce` — the PreToolUse hook denies Windows-NUL redirects (reason included),
  the sweep hook trashes fresh Tier-1 junk automatically.

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/scan.py mode enforce
```

Flip to enforce only after the user has watched report mode for a while.

## Growing the registry

When you discover a new recurring junk pattern, determine its cause first (search
the web / read the tool's source), then add a rule to `scripts/rules.json` with the
cause documented. Tier assignment: provable accidents and regenerable caches are
Tier 1; anything requiring verification is Tier 2; anything requiring judgment is
Tier 3. When unsure, Tier 3.
