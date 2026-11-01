# janitor

An AI janitor **agent** for Claude Code, shipped as a plugin. The agent (a
forked-context skill) investigates a folder with real judgment — file
contents, project state files, git history, mtimes — and trashes what it can
defend. The deterministic script is demoted to the agent's hands: it is the
only mutation path, it enforces the hard allowlist inside the verb, and
nothing is ever `rm`'d. An always-on agent hook (`type: "agent"`, experimental)
reviews the workspace at turn end in enforce mode.

**Architecture**: agent brain (judgment, evidence, dispositions) → safe verbs
(`scan`, `trash-paths`, `restore`) → trash store with manifests. Determinism
survives only where safety demands it: trash-not-delete, hard allowlist,
restore.

## Why

Agent sessions litter project folders in predictable ways: Windows `> nul`
redirect habits create real files named `nul` on Linux, `__pycache__` sprouts
everywhere, some tools flatten absolute output paths into underscored
filenames (`_home_viper_...txt`), and failed attempts get cloned as `foo2.py`
instead of edited. The registry (`scripts/rules.json`) still exists — but as
*evidence with documented causes* feeding the agent, not as its ceiling.

## Safety contract

1. **Agent judgment, verb-enforced safety.** The agent may nominate anything;
   the `trash-paths` verb refuses hard-allowlisted and protected paths no
   matter what the agent decides. Registry Tier 2/3 are guidance now, not gates.
2. **Trash, never delete.** Everything lands in
   `~/.local/share/janitor-trash/<project>/<timestamp>/` with a manifest;
   `scan.py restore` reverses any batch.
3. **Report-only by default.** `--apply` trashes Tier 1 (provable accidents,
   regenerable caches) only. Tier 2 needs duplicate verification, Tier 3 needs
   a human — both are forever report-only in the script.
4. **Hard allowlist + mtime guard.** Credentials, notes, config files,
   `loot/`, `writeup/`, state files, and anything modified in the last 4 hours
   are untouchable.
5. **Projects can only shrink exposure.** A `.janitor-ignore` file
   (gitignore-style patterns) protects extra paths; no mechanism makes the
   janitor more aggressive per-project.

## Modes

Hooks read `~/.config/janitor/config.json` (`{"mode": "report"}` by default):

| mode | PreToolUse (Bash) | PostToolUse (Bash) | Stop |
| --- | --- | --- | --- |
| `report` | observes NUL redirects, allows | annotates fresh junk | one-line census if junk exists |
| `enforce` | denies NUL redirects with a fix hint | trashes fresh Tier-1 junk | census |

```
python3 scripts/scan.py mode enforce
```

## Install

This repo is its own local marketplace (`.claude-plugin/marketplace.json`).

```
claude plugin marketplace add ~/Code/janitor
claude plugin install janitor@viper-local
```

Once enabled, hooks + skill are active in every project on the machine.
To share: push the repo, then others install with:

```
/plugin install janitor --marketplace <owner>/<repo>
```

## CLI

```
python3 scripts/scan.py scan [roots…] [--apply] [--json]   # report (default) / trash tier 1
python3 scripts/scan.py restore [root] [--latest] [--list]
python3 scripts/scan.py mode [report|enforce]
```

Stdlib-only Python 3. No dependencies.
