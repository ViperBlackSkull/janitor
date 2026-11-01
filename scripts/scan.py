#!/usr/bin/env python3
"""Janitor — registry-only workspace junk scanner.

Safety contract (invariants, do not weaken):
  1. A file is junk ONLY if it matches a rule in rules.json. No heuristics beyond the registry.
  2. NEVER calls rm/unlink. Everything goes to a trash store with a restore manifest.
  3. Read-only by default. --apply trashes Tier-1 matches only; Tier 2/3 are report-only forever.
  4. Hard allowlist always wins over any rule, plus an mtime guard (recently-modified files are
     never junk — something may be mid-flight in a live session).
  5. Per-project .janitor-ignore can protect MORE paths; it can never make rules more aggressive.

Stdlib only. Python 3.9+.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
RULES_PATH = SCRIPT_DIR / "rules.json"
CONFIG_PATH = Path.home() / ".config" / "janitor" / "config.json"
TRASH_ROOT = Path.home() / ".local" / "share" / "janitor-trash"

# --- hard allowlist: never reported as deletable, never trashed -----------------
ALLOW_EXTS = {".ccache", ".pfx", ".key", ".kdbx", ".pem", ".md", ".conf", ".ini",
              ".toml", ".yaml", ".yml", ".lock", ".pid", ".gnupg"}
ALLOW_NAME_SUBSTR = ("creds", "ticket", "passwd", "secret", "token", "writeup",
                     "ENGAGEMENT_STATE", "session_log", "WRITEUP")
ALLOW_EXACT = {".gitkeep", ".keep", ".gitignore", ".janitor-ignore"}
ALLOW_DIRS = {"loot", "writeup", ".git", ".claude", ".trash", ".janitor-trash",
              "node_modules", "venv", ".venv", ".tox", "dist-newstyle"}
# never descend into these (protected or huge-and-out-of-scope)
SKIP_DIRS = ALLOW_DIRS | {"__pycache__"}  # __pycache__ itself is a Tier-1 target, not a place to look

RECENT_GUARD_S = 4 * 3600      # files modified more recently than this are never junk
SWEEP_FRESH_S = 15 * 60        # sweep hook only considers files touched in this window
SCAN_DEPTH = 2

WINDOWS_RESERVED = ["nul", "con", "prn", "aux"] + \
                   [f"com{i}" for i in range(1, 10)] + [f"lpt{i}" for i in range(1, 10)]


def load_rules() -> dict:
    return json.loads(RULES_PATH.read_text())


def load_mode() -> str:
    try:
        return json.loads(CONFIG_PATH.read_text()).get("mode", "report")
    except (OSError, json.JSONDecodeError):
        return "report"


def save_mode(mode: str) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps({"mode": mode}, indent=2) + "\n")


def slugify(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "-", str(path.resolve())).strip("-")[-80:]


def load_ignore(root: Path) -> list[str]:
    """Per-project extra protections. Shrink-only by construction: patterns can only
    add paths to the allowlist, never nominate new junk."""
    f = root / ".janitor-ignore"
    if not f.is_file():
        return []
    return [ln.strip() for ln in f.read_text().splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def is_allowed(p: Path, root: Path, extra_ignore: list[str]) -> bool:
    name = p.name
    if name in ALLOW_EXACT or p.suffix.lower() in ALLOW_EXTS:
        return True
    low = name.lower()
    if any(s.lower() in low for s in ALLOW_NAME_SUBSTR):
        return True
    rel = str(p.relative_to(root))
    for pat in extra_ignore:
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(name, pat):
            return True
    return False


def sha256_of(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# --- classification -------------------------------------------------------------
def classify(root: Path, rules: dict, extra_ignore: list[str],
             min_age_s: int = RECENT_GUARD_S) -> dict[str, list[dict]]:
    """Walk root (depth-limited) and return {tier: [entries]}. Read-only."""
    findings: dict[str, list[dict]] = {"tier1": [], "tier2": [], "tier3": []}
    now = time.time()
    t1 = rules["tier1"]

    def add(tier: str, p: Path, rule: str, cause: str):
        try:
            st = p.stat()
        except OSError:
            return
        findings[tier].append({
            "path": str(p), "rel": str(p.relative_to(root)), "rule": rule,
            "cause": cause, "bytes": st.st_size, "mtime": st.st_mtime,
        })

    def walk(d: Path, depth: int):
        try:
            entries = sorted(d.iterdir())
        except OSError:
            return
        for e in entries:
            if e.is_dir(follow_symlinks=False):
                if e.name in t1["dir_names"]["names"] and now - e.stat().st_mtime >= min_age_s:
                    add("tier1", e, f"dir:{e.name}", t1["dir_names"]["cause"])
                    continue  # Tier-1 check must precede SKIP_DIRS: __pycache__ is both
                if e.name in SKIP_DIRS or e.is_symlink():
                    continue
                if depth < SCAN_DEPTH:
                    walk(e, depth + 1)
                continue
            if not e.is_file() or e.is_symlink():
                continue
            if is_allowed(e, root, extra_ignore):
                continue
            st = e.stat()
            age = now - st.st_mtime
            if age < min_age_s:
                continue  # mtime guard: mid-flight files are never junk
            base = e.name
            if base in t1["exact_names"]["names"] or base.rstrip("^") in WINDOWS_RESERVED:
                add("tier1", e, f"exact:{base}", t1["exact_names"]["cause"])
            elif e.suffix.lower() in t1["extensions"]["exts"]:
                add("tier1", e, f"ext:{e.suffix}", t1["extensions"]["cause"])
            elif st.st_size <= t1["contentless_files"]["max_bytes"] and \
                    age >= t1["contentless_files"]["older_than_hours"] * 3600:
                add("tier1", e, "contentless", t1["contentless_files"]["cause"])
            else:
                for r in rules["tier2"]:
                    if re.search(r["regex"], base):
                        add("tier2", e, r["id"], r["cause"])
                        break
                else:
                    for r in rules["tier3"]:
                        hit = (fnmatch.fnmatch(base, r["glob"]) if "glob" in r
                               else re.search(r["regex"], base))
                        if hit:
                            add("tier3", e, r["id"], r["cause"])
                            break

    walk(root, 0)

    # dedupe: dirs already reported shouldn't also appear via their children
    reported_dirs = {f["path"] for f in findings["tier1"]}
    for tier in findings:
        findings[tier] = [f for f in findings[tier]
                          if not any(str(Path(f["path"]).parent) == d for d in reported_dirs)]
    return findings


# --- trash / restore -------------------------------------------------------------
def trash(root: Path, entries: list[dict]) -> list[dict]:
    """Move entries into the trash store with a manifest. Returns manifest records."""
    ts = time.strftime("%Y%m%d-%H%M%S")
    dest = TRASH_ROOT / slugify(root) / ts
    dest.mkdir(parents=True, exist_ok=True)
    records = []
    for e in entries:
        src = Path(e["path"])
        try:
            target = dest / e["rel"].replace("/", "__")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(target))
        except OSError as ex:
            records.append({**e, "error": f"move failed: {ex}"})
            continue
        # hash only files, and never let a hash failure invalidate a successful move
        digest = "-"
        if target.is_file():
            try:
                digest = sha256_of(target)
            except OSError:
                digest = "-"
        records.append({**e, "trashed_to": str(target), "sha256": digest, "trashed_at": ts})
    (dest / "manifest.json").write_text(json.dumps(records, indent=2))
    return records


def cmd_restore(args) -> int:
    slug = slugify(Path(args.root).resolve()) if args.root else None
    base = TRASH_ROOT / slug if slug else TRASH_ROOT
    if not base.exists():
        print("Trash store is empty for this project.")
        return 0
    batches = sorted([d for d in base.iterdir() if d.is_dir() and (d / "manifest.json").exists()])
    if args.latest:
        batches = batches[-1:]
    for b in batches:
        for rec in json.loads((b / "manifest.json").read_text()):
            if args.list_only:
                status = "FAILED" if "error" in rec else rec.get("rule", "?")
                print(f"{b.name}  {rec.get('rel')}  <- {status}")
                continue
            if "error" in rec or "trashed_to" not in rec or not Path(rec["trashed_to"]).exists():
                continue
            dest = Path(rec["path"])
            if dest.exists():
                continue  # never clobber: something recreated the path since
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(rec["trashed_to"], str(dest))
            print(f"restored {rec['rel']} <- {b.name}")
        (b / "restored.at").write_text(time.strftime("%Y%m%d-%H%M%S"))
    return 0


# --- reporting -------------------------------------------------------------------
def print_report(findings: dict, root: Path, mode: str, applied: list[dict] | None = None) -> None:
    total = sum(len(v) for v in findings.values())
    if total == 0 and not applied:
        print(f"janitor: {root} is clean (mode={mode})")
        return
    labels = {"tier1": "TIER 1  auto-trashable", "tier2": "TIER 2  verify-then-trash",
              "tier3": "TIER 3  report-only"}
    for tier in ("tier1", "tier2", "tier3"):
        # collapse spray: when one directory holds >15 findings, summarize instead of listing
        by_dir: dict[str, list[dict]] = {}
        for e in findings[tier]:
            by_dir.setdefault(str(Path(e["rel"]).parent), []).append(e)
        for d, items in sorted(by_dir.items()):
            prefix = "." if d == "." else d
            if len(items) > 15:
                sample = ", ".join(Path(i["rel"]).name for i in items[:3])
                rules = ", ".join(sorted({i["rule"] for i in items}))
                print(f"[{labels[tier]}] {prefix}/ — {len(items)} files ({rules}; e.g. {sample}, …)")
            else:
                for e in items:
                    print(f"[{labels[tier]}] {e['rel']}  ({e['bytes']}B, rule={e['rule']})")
                    if os.environ.get("JANITOR_VERBOSE"):
                        print(f"           cause: {e['cause']}")
    if applied:
        ok = [r for r in applied if "error" not in r]
        failed = [r for r in applied if "error" in r]
        if len(ok) <= 15:
            for r in ok:
                print(f"[APPLIED] {r['rel']} -> trashed")
        else:
            dirs = sorted({str(Path(r['rel']).parent) for r in ok})
            print(f"[APPLIED] {len(ok)} files trashed across {', '.join(dirs)}")
        for r in failed:
            print(f"[APPLIED] {r['rel']} -> FAILED: {r['error']}")
    print(f"janitor: {total} finding(s) in {root} (mode={mode}); "
          f"tier1 auto-trashed: {len(applied) if applied else 0}")


# --- hook subcommands --------------------------------------------------------------
def hook_output(payload: dict) -> None:
    print(json.dumps(payload))


def cmd_prevent(args) -> int:
    """PreToolUse(Bash): deny Windows NUL redirects before they create junk files."""
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    cmd = (data.get("tool_input") or {}).get("command", "")
    # strip quoted spans: a 'nul' inside quotes is destined for a remote/Windows shell — not our business
    stripped = re.sub(r"'[^']*'", "", cmd)
    stripped = re.sub(r'"[^"]*"', "", stripped)
    if re.search(r'(?:[0-9]&?|&)?>{1,2}\s*(nul\^|nul|null)(?![\w.\-])', stripped):
        if load_mode() == "enforce":
            hook_output({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "janitor: this redirect targets Windows NUL, but on Linux it creates a real "
                        "junk file named 'nul'. Use /dev/null. If the command is FOR a remote Windows "
                        "host, put the redirect inside quotes so it is sent verbatim."),
                }
            })
        else:
            hook_output({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": "janitor (report mode): NUL redirect noted; would deny in enforce mode",
                }
            })
    return 0


def cmd_sweep(args) -> int:
    """PostToolUse(Bash): trash Tier-1 junk this command just created (mtime-fresh only)."""
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    cwd = data.get("cwd") or os.getcwd()
    root = Path(cwd).resolve()
    rules = load_rules()
    ignore = load_ignore(root)
    findings = classify(root, rules, ignore, min_age_s=0)
    fresh = {t: [e for e in findings[t]
                 if time.time() - e["mtime"] <= SWEEP_FRESH_S]
             for t in findings}
    count = sum(len(v) for v in fresh.values())
    if count == 0:
        return 0
    mode = load_mode()
    applied = None
    if mode == "enforce" and fresh["tier1"]:
        applied = trash(root, fresh["tier1"])
    lines = [f"janitor sweep: {count} fresh junk item(s) after that command "
             f"(mode={mode})"]
    for e in fresh["tier1"]:
        note = " -> trashed (restore: janitor restore)" if applied else ""
        lines.append(f"  tier1 {e['rel']} rule={e['rule']}{note}")
    for t in ("tier2", "tier3"):
        for e in fresh[t]:
            lines.append(f"  {t} {e['rel']} rule={e['rule']} (report-only)")
    hook_output({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": "\n".join(lines),
        }
    })
    return 0


def cmd_stop_report(args) -> int:
    """Stop hook: one-line junk census for the session project. Read-only, quiet if clean."""
    root = Path(args.root if args.root else os.getcwd()).resolve()
    findings = classify(root, load_rules(), load_ignore(root))
    n1, n2, n3 = (len(findings[t]) for t in ("tier1", "tier2", "tier3"))
    if n1 + n2 + n3:
        print(f"janitor: {n1} auto-trashable, {n2} verify-then-trash, {n3} suspect(s) in {root.name} "
              f"— run the janitor skill to clean (mode={load_mode()})")
    return 0


# --- main ----------------------------------------------------------------------------
def cmd_scan(args) -> int:
    mode = load_mode()
    results = {}
    applied_all = []
    for target in args.roots:
        root = Path(target).resolve()
        if not root.is_dir():
            print(f"janitor: skipping {target} (not a directory)", file=sys.stderr)
            continue
        findings = classify(root, load_rules(), load_ignore(root))
        applied = None
        if args.apply:
            if findings["tier1"]:
                applied = trash(root, findings["tier1"])
                applied_all += applied
            # tier2/3 are never touched by the script — skill judgment / user decision only
        results[str(root)] = findings
        if not args.json:
            print_report(findings, root, mode, applied)
    if args.json:
        print(json.dumps({"mode": mode, "results": results,
                          "applied": applied_all}, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="janitor", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("scan", help="report junk (default); --apply trashes tier 1")
    p.add_argument("roots", nargs="*", default=["."])
    p.add_argument("--apply", action="store_true",
                   help="trash Tier-1 findings (recoverable via restore)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("restore", help="restore trashed files")
    p.add_argument("root", nargs="?", help="project path (default: all)")
    p.add_argument("--latest", action="store_true", help="only the most recent batch")
    p.add_argument("--list", dest="list_only", action="store_true")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("mode", help="show or set report/enforce")
    p.add_argument("value", nargs="?", choices=["report", "enforce"])

    def cmd_mode(a) -> int:
        if a.value:
            save_mode(a.value)
            print(f"mode -> {a.value}")
        else:
            print(load_mode())
        return 0

    p.set_defaults(func=cmd_mode)

    p = sub.add_parser("prevent", help="PreToolUse hook (stdin JSON)")
    p.set_defaults(func=cmd_prevent)
    p = sub.add_parser("sweep", help="PostToolUse hook (stdin JSON)")
    p.set_defaults(func=cmd_sweep)

    p = sub.add_parser("stop-report", help="Stop hook: junk census")
    p.add_argument("root", nargs="?", default=os.getcwd())
    p.set_defaults(func=cmd_stop_report)

    args = ap.parse_args()
    if not getattr(args, "func", None):
        args = ap.parse_args(["scan"] + sys.argv[1:])
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
