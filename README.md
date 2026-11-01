# janitor

Registry-only workspace junk scanner for Claude Code, shipped as a plugin:
hooks handle the always-on part, a skill handles verified deep cleans.

## Why

Agent sessions litter project folders in predictable ways: Windows `> nul`
redirect habits create real files named `nul` on Linux, `__pycache__` sprouts
everywhere, some tools flatten absolute output paths into underscored
filenames (`_home_viper_...txt`), and failed attempts get cloned as `foo2.py`
instead of edited. All of it is *predictable* — so cleanup can be driven by an
explicit registry of known junk instead of heuristics that might eat something
useful.

## Safety contract

1. **Registry-only.** A file is junk iff it matches a rule in
   `scripts/rules.json`, each with a documented cause.
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

From a checkout:

```
claude plugin add /path/to/janitor --plugin-dir   # or add via /plugin → browse
```

Once enabled, hooks + skill are active in every project on the machine. To
share: push this repo; others install it the same way (or via a marketplace).

## CLI

```
python3 scripts/scan.py scan [roots…] [--apply] [--json]   # report (default) / trash tier 1
python3 scripts/scan.py restore [root] [--latest] [--list]
python3 scripts/scan.py mode [report|enforce]
```

Stdlib-only Python 3. No dependencies.
