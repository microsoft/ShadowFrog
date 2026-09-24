#!/usr/bin/env python3
"""Shared Markdown parsing, bounded retrieval, and human-facing shadow views.

The core shadow-read.py helper and user-facing shadow-viewer.py use this
implementation without changing source-to-shadow paths or discovery syntax.
Parsing and structural inspection alone never increment citation scores.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
import re
import sys
import sqlite3
import subprocess
import time
import traceback
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
_bytecode = sys.dont_write_bytecode
sys.dont_write_bytecode = True
try:
    from _citations import (
        CitationStore, RECEIPT_TTL, canonical_path, discovery_id, is_busy_error,
        validate_event_id,
    )
except ImportError as exc:
    raise SystemExit("[shadow error] Missing citation helper/dependency; reinstall the complete core skill") from exc
finally:
    sys.path.pop(0)
    sys.dont_write_bytecode = _bytecode


_DISCOVERY_META_RE = re.compile(
    r"_\((\w+),\s*source:\s*(\w+)"
    r"(?:,\s*labels:\s*\[([^\]]*)\])?"
    r"\)_"
)


def warn(msg):
    """Print a warning to stderr. Agents read these to adjust strategy."""
    print(f"[shadow warning] {msg}", file=sys.stderr)


def error(msg):
    """Print an error to stderr."""
    print(f"[shadow error] {msg}", file=sys.stderr)


def find_shadow_dir(start="."):
    """Walk up from start to find .shadow/ directory."""
    try:
        p = Path(start).resolve()
        while p != p.parent:
            candidate = p / ".shadow"
            if candidate.is_dir():
                return candidate
            p = p.parent
    except (OSError, PermissionError) as e:
        error(f"Failed to search for .shadow/ directory from '{start}': {e}")
    return None


def parse_discovery(line, continuation_lines=None):
    """Parse a discovery bullet and its metadata line(s)."""
    try:
        text = line[2:].strip() if line.startswith("- ") else line.strip()
    except (TypeError, AttributeError) as e:
        warn(f"parse_discovery: bad line input ({type(line).__name__}): {e}")
        return {"text": str(line) if line else ""}

    meta = {}
    full_text = text

    if continuation_lines:
        for cl in continuation_lines:
            try:
                stripped = cl.strip()
                # _(status, source: type, labels: [l1, l2])_ or _(status, source: type)_
                m = _DISCOVERY_META_RE.match(stripped)
                if m:
                    meta["status"] = m.group(1)
                    meta["source"] = m.group(2)
                    if m.group(3):
                        meta["labels"] = [
                            l.strip()
                            for l in m.group(3).split(",")
                            if l.strip()
                        ]
                else:
                    # _(source: type)_ (preferences format)
                    m2 = re.match(r"_\(source:\s*(\w+)\)_", stripped)
                    if m2:
                        meta["source"] = m2.group(1)
                    elif stripped.startswith("Also involves:"):
                        refs = re.findall(r"`([^`]+)`", stripped)
                        meta["also_involves"] = refs
                    elif stripped.startswith("Dream report:"):
                        m_dr = re.search(r"`([^`]+)`", stripped)
                        if m_dr:
                            meta["dream_report"] = m_dr.group(1)
                    else:
                        full_text += " " + stripped
            except Exception as e:
                warn(f"parse_discovery: failed parsing continuation line "
                     f"'{cl[:80]}': {e}")

    return {"text": full_text, **meta}


def parse_shadow_file(filepath):
    """Parse a per-file shadow into structured data.

    Returns a result dict even on partial failure — whatever was parsed
    before the error is preserved. Warnings go to stderr.
    """
    result = {
        "path": str(filepath),
        "source_file": None,
        "language": None,
        "lines": None,
        "last_modified": None,
        "symbols": [],
        "discoveries": [],
        "cross_references": [],
        "parse_errors": [],
    }

    try:
        content = filepath.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        msg = (f"Cannot read {filepath}: encoding error at byte "
               f"{e.start}: {e.reason}. File may not be UTF-8.")
        warn(msg)
        result["parse_errors"].append(msg)
        return result
    except OSError as e:
        msg = f"Cannot read {filepath}: {e}"
        warn(msg)
        result["parse_errors"].append(msg)
        return result

    lines = content.split("\n")
    current_symbol = None
    i = 0

    while i < len(lines):
        line = lines[i]

        try:
            # Header: # Shadow: src/auth.py
            if line.startswith("# Shadow: "):
                result["source_file"] = line[len("# Shadow: "):].strip()

            # Metadata: **Language**: Python | **Lines**: 142 | ...
            elif line.startswith("**Language**"):
                parts = line.split("|")
                for part in parts:
                    part = part.strip()
                    if part.startswith("**Language**"):
                        m = re.search(r"\*\*:\s*(.+)", part)
                        if m:
                            result["language"] = m.group(1).strip()
                    elif "Lines" in part:
                        m = re.search(r"(\d+)", part)
                        if m:
                            try:
                                result["lines"] = int(m.group(1))
                            except ValueError:
                                pass
                    elif "Last modified" in part:
                        m = re.search(r"\*\*:\s*(.+)", part)
                        if m:
                            result["last_modified"] = m.group(1).strip()

            # Symbol heading: ## `symbol_name` or ### `Class.method`
            elif re.match(r"^#{2,3}\s", line):
                sym_match = re.match(r"^(#{2,3})\s+`(.+?)`", line)
                if sym_match:
                    name = sym_match.group(2)
                    current_symbol = name
                    result["symbols"].append(name)
                elif "Cross-References" in line:
                    current_symbol = "__cross_refs__"
                elif "File-Level" in line:
                    current_symbol = "__file_level__"
                else:
                    current_symbol = None

            # Discovery bullet (skip cross-reference links)
            elif (
                line.strip().startswith("- ")
                and current_symbol
                and current_symbol != "__cross_refs__"
            ):
                # Collect continuation lines
                continuation = []
                j = i + 1
                while j < len(lines):
                    next_line = lines[j]
                    if (
                        next_line.strip() == ""
                        or next_line.strip().startswith("- ")
                        or re.match(r"^#{1,3}\s", next_line)
                    ):
                        break
                    continuation.append(next_line)
                    j += 1

                disc = parse_discovery(line.strip(), continuation)
                disc["symbol"] = (
                    "file-level" if current_symbol == "__file_level__"
                    else current_symbol
                )
                disc["file"] = result["source_file"]
                result["discoveries"].append(disc)
                i = j
                continue

            # Cross-reference link
            elif (
                current_symbol == "__cross_refs__"
                and line.strip().startswith("- ")
            ):
                link_match = re.search(r"\[(.+?)\]", line)
                if link_match:
                    result["cross_references"].append(link_match.group(1))

        except Exception as e:
            msg = (f"Error parsing {filepath} at line {i + 1}: "
                   f"{type(e).__name__}: {e}")
            warn(msg)
            result["parse_errors"].append(msg)

        i += 1

    return result


def parse_prefs(shadow_dir):
    """Parse _prefs.md into a list of preferences."""
    prefs_path = shadow_dir / "_prefs.md"
    if not prefs_path.exists():
        return []

    try:
        content = prefs_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        warn(f"Cannot read preferences file {prefs_path}: {e}")
        return []

    prefs = []
    lines = content.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        try:
            if line.strip().startswith("- ") and not line.strip().startswith(
                "- ["
            ):
                continuation = []
                j = i + 1
                while j < len(lines):
                    next_line = lines[j]
                    if next_line.strip() == "" or next_line.strip().startswith(
                        "- "
                    ):
                        break
                    continuation.append(next_line)
                    j += 1

                pref = parse_discovery(line.strip(), continuation)
                pref["type"] = "preference"
                prefs.append(pref)
                i = j
                continue
        except Exception as e:
            warn(f"Error parsing preference at line {i + 1} in "
                 f"{prefs_path}: {e}")
        i += 1

    return prefs


def parse_cross_cutting(shadow_dir):
    """Parse all _cross/*.md files."""
    cross_dir = shadow_dir / "_cross"
    if not cross_dir.exists():
        return []

    entries = []
    try:
        md_files = sorted(cross_dir.glob("*.md"))
    except OSError as e:
        warn(f"Cannot list cross-cutting directory {cross_dir}: {e}")
        return []

    for f in md_files:
        try:
            content = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            warn(f"Cannot read cross-cutting file {f}: {e}")
            continue

        entry = {"slug": f.stem, "file": str(f.name)}

        try:
            # Title
            m = re.search(r"^# (.+)", content, re.MULTILINE)
            if m:
                entry["title"] = m.group(1).strip()

            # Category
            m = re.search(r"\*\*Category\*\*:\s*(.+)", content)
            if m:
                entry["category"] = m.group(1).strip()

            # Refs — only within the **Refs**: section, not backticked
            # bullets elsewhere in the file (e.g., inside the Discovery body).
            refs = []
            refs_block = re.search(
                r"\*\*Refs\*\*:\s*\n(.*?)(?=\n[ \t]*\n|\n\*\*|\Z)",
                content,
                re.DOTALL,
            )
            if refs_block:
                refs = re.findall(r"-\s*`([^`]+)`", refs_block.group(1))
            entry["refs"] = refs

            # Discovery text
            m = re.search(
                r"\*\*Discovery\*\*:\s*(.+?)(?=\n\n|\n_\(|\Z)",
                content,
                re.DOTALL,
            )
            if m:
                entry["discovery"] = m.group(1).strip()

            # Status/source (with optional labels)
            m = _DISCOVERY_META_RE.search(content)
            if m:
                entry["status"] = m.group(1)
                entry["source"] = m.group(2)
                if m.group(3):
                    entry["labels"] = [
                        l.strip()
                        for l in m.group(3).split(",")
                        if l.strip()
                    ]
            else:
                # Fallback to simpler pattern
                m2 = re.search(
                    r"_\((\w+),\s*source:\s*(\w+)\)_", content
                )
                if m2:
                    entry["status"] = m2.group(1)
                    entry["source"] = m2.group(2)
        except Exception as e:
            warn(f"Error parsing cross-cutting file {f.name}: "
                 f"{type(e).__name__}: {e}")
            entry.setdefault("title", f.stem)

        entries.append(entry)

    return entries


def load_state(shadow_dir):
    """Load _meta/state.json."""
    state_path = shadow_dir / "_meta" / "state.json"
    if not state_path.exists():
        return {}
    try:
        content = state_path.read_text(encoding="utf-8")
        state = json.loads(content)
        if not isinstance(state, dict):
            warn(f"state.json is not a JSON object (got {type(state).__name__})")
            return {}
        return state
    except json.JSONDecodeError as e:
        warn(f"Invalid JSON in {state_path}: {e}")
        return {}
    except OSError as e:
        warn(f"Cannot read {state_path}: {e}")
        return {}


def get_all_shadow_files(shadow_dir):
    """Get all per-file shadow .md files (excluding special files)."""
    special = {"_index.md", "_prefs.md"}
    special_dirs = {"_cross", "_meta", "_dreams"}

    results = []
    try:
        for f in shadow_dir.rglob("*.md"):
            try:
                rel = f.relative_to(shadow_dir)
                parts = rel.parts
                if parts[0] in special_dirs:
                    continue
                if str(rel) in special:
                    continue
                results.append(f)
            except (ValueError, IndexError) as e:
                warn(f"Skipping file {f}: {e}")
    except OSError as e:
        warn(f"Error walking shadow directory {shadow_dir}: {e}")

    return sorted(results)


def collect_all_discoveries(shadow_dir):
    """Parse all shadow files and collect every discovery.

    Continues past individual file failures — reports errors and moves on.
    """
    all_disc = []
    failed_files = []
    for sf in get_all_shadow_files(shadow_dir):
        try:
            parsed = parse_shadow_file(sf)
            if parsed.get("parse_errors"):
                failed_files.append(
                    (str(sf), parsed["parse_errors"])
                )
            modified = _mtime(sf)
            for d in parsed["discoveries"]:
                d.setdefault("file", parsed["source_file"])
                d["shadow_path"] = str(sf.relative_to(shadow_dir))
                d["shadow_mtime"] = modified
                all_disc.append(d)
        except Exception as e:
            msg = f"Failed to parse {sf}: {type(e).__name__}: {e}"
            warn(msg)
            failed_files.append((str(sf), [msg]))

    if failed_files:
        warn(f"{len(failed_files)} file(s) had parse errors "
             f"(discoveries from other files still collected)")

    return all_disc


# --- View Functions ---

@dataclass(frozen=True)
class RetrievalOptions:
    limit: int = 10
    max_chars: int = 4000
    cursor: str | None = None
    event_id: str | None = None
    record: bool = True
    text_cursor: str | None = None

    def __post_init__(self):
        if type(self.limit) is not int or self.limit < 1:
            raise ValueError("Result limit must be positive")
        if type(self.max_chars) is not int or self.max_chars < 0 or (self.max_chars and self.max_chars < 256):
            raise ValueError("Output budget must be at least 256 characters, or 0 for no cap")
        for name in ("cursor", "text_cursor"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{name} must be a nonempty returned cursor")
        if self.event_id is not None:
            validate_event_id(self.event_id)


def _knowledge_entry(kind, file, symbol, text, data, refs=(), mtime=0):
    symbol = _canonical_symbol(symbol)
    anchor = f"{file}::{symbol}" if symbol else file
    return {
        "id": discovery_id(kind, anchor, text, refs),
        "kind": kind, "file": file, "symbol": symbol, "anchor": anchor,
        "text": text, "refs": list(refs), "mtime": mtime,
        "status": data.get("status", "verified" if kind == "preference" else "?"),
        "source": data.get("source", "?"), "labels": sorted(set(data.get("labels", []))),
        "title": data.get("title", ""), "category": data.get("category", "?"),
        "dream_report": data.get("dream_report", ""),
    }


def _canonical_symbol(symbol):
    # Container headings use the same prefixes as shadow-init.py::Symbol.heading_text.
    if symbol in ("File-Level", "file-level"):
        return "file-level"
    return re.sub(r"^(?:class|interface|enum|trait|struct|protocol|module) ", "", symbol, count=1)


def _canonical_source_file(shadow_dir, source_file):
    if (
        not source_file or any(char in source_file for char in (":", "\\", "\0", "\n", "\r"))
        or any(part in ("", ".", "..") for part in source_file.split("/"))
    ):
        raise ValueError("Use a repository-relative source path with forward slashes")
    root = canonical_path(shadow_dir)
    target = canonical_path(root / (source_file + ".md"))
    if not target.is_relative_to(root):
        raise ValueError("Requested shadow resolves outside --shadow-dir")
    return target.relative_to(root).as_posix()[:-3]


def _mtime(path):
    try:
        return path.stat().st_mtime
    except OSError as exc:
        warn(f"Modification time unavailable for {path}: {exc}; treating it as undated.")
        return 0


def _preference_entries(shadow_dir):
    prefs = parse_prefs(shadow_dir)
    modified = _mtime(shadow_dir / "_prefs.md") if prefs else 0
    return _unique_entries([
        _knowledge_entry(
            "preference", "_prefs.md", "", pref.get("text", ""), pref,
            mtime=modified,
        )
        for pref in prefs
    ])


def _unique_entries(entries):
    # Duplicate claims at the same location have one identity and one score.
    unique = {}
    for entry in entries:
        if not entry["text"].strip():
            warn(f"Empty discovery at {entry['anchor']}; repair its text before retrieval.")
            continue
        prior = unique.get(entry["id"])
        if prior is None:
            unique[entry["id"]] = entry
        else:
            strongest = entry if _trust(entry) < _trust(prior) else prior
            unique[entry["id"]] = {
                **strongest, "labels": sorted(set(entry["labels"]) | set(prior["labels"])),
            }
    return list(unique.values())


def _knowledge_entries(shadow_dir, source_file=None):
    """Collect identities without recording a citation for parsing or matching."""
    entries = []
    files = {}

    def canonical_file(file):
        if file not in files:
            files[file] = _canonical_source_file(shadow_dir, file)
        return files[file]

    def canonical_refs(refs):
        normalized = []
        for ref in refs:
            file, separator, symbol = ref.partition("::")
            if separator:
                try:
                    ref = f"{canonical_file(file)}::{_canonical_symbol(symbol)}"
                except (OSError, ValueError, RuntimeError) as exc:
                    warn(f"Cannot resolve reference {ref!r}: {exc}; inspect and repair that reference.")
            normalized.append(ref)
        return sorted(set(normalized))

    if source_file is None:
        discoveries = collect_all_discoveries(shadow_dir)
    else:
        source_file = canonical_file(source_file)
        path = shadow_dir / (source_file + ".md")
        parsed = parse_shadow_file(path) if path.is_file() else {"discoveries": []}
        modified = _mtime(path) if parsed["discoveries"] else 0
        discoveries = [
            {**disc, "shadow_path": source_file + ".md", "shadow_mtime": modified}
            for disc in parsed["discoveries"]
        ]
    for disc in discoveries:
        file = canonical_file(Path(disc["shadow_path"]).as_posix()[:-3])
        entries.append(_knowledge_entry(
            "discovery", file, disc.get("symbol", "file-level"), disc.get("text", ""),
            disc, canonical_refs(disc.get("also_involves", [])), disc.get("shadow_mtime", 0),
        ))
    for cross in parse_cross_cutting(shadow_dir):
        refs = canonical_refs(cross.get("refs", []))
        if source_file is not None and not any(ref.split("::", 1)[0] == source_file for ref in refs):
            continue
        relative = "_cross/" + cross["file"]
        entries.append(_knowledge_entry(
            "cross-cutting", relative, "", cross.get("discovery", cross.get("title", "")),
            cross, refs, _mtime(shadow_dir / relative),
        ))
    if source_file is None:
        entries.extend(_preference_entries(shadow_dir))
    return _unique_entries(entries)


def _trust(entry):
    if entry["status"] == "refuted":
        return 5
    if entry["source"] == "user":
        return 0
    if entry["source"] == "interaction":
        return 1
    return {"verified": 2, "uncertain": 3}.get(entry["status"], 4)


def _rank_entries(entries, scores, limit, recent=False):
    groups = defaultdict(list)
    for entry in entries:
        priority = ((-entry["mtime"],) if recent else ()) + (
            entry.get("relevance", 0), _trust(entry),
        )
        groups[priority].append(entry)
    result = []
    for priority in sorted(groups):
        group = groups[priority]
        seen = sorted(
            (entry for entry in group if scores.get(entry["id"], 0)),
            key=lambda entry: -scores[entry["id"]],
        )
        unseen = [entry for entry in group if not scores.get(entry["id"], 0)]
        width = max(1, limit)
        while seen and unseen:
            remaining = width - len(result) % width
            take = min(len(seen), remaining - 1)
            result.extend(seen[:take])
            del seen[:take]
            result.append(unseen.pop(0))
        result.extend(seen)
        result.extend(unseen)
    return result


def _citation_store(shadow_dir):
    try:
        return CitationStore.for_shadow(shadow_dir)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        warn(f"Citation tracking unavailable: {exc}. This visit will not be recorded; check local Git/state access.")
        return None


def _clip(text, limit):
    if len(text) <= limit:
        return text
    return text[:limit] if limit < 3 else text[:limit - 3] + "..."


def _preview_parts(entry, score, style, group_count):
    text = entry["text"].replace("\n", " ")
    anchor = _clip(entry["anchor"], 160)
    identity = f"id={entry['id']} citation_score={score}"
    metadata = f"({entry['status']}, source: {entry['source']})"
    labels = ",".join(entry["labels"]) or "-"
    if style == "top":
        anchor = entry["symbol"] if entry["kind"] == "discovery" else entry["file"]
        prefix = f"- [{_clip(labels, 40)}] `{_clip(anchor, 48)}` ({entry['status']}) id={entry['id']}: "
        return prefix, text
    if style == "recent":
        stamp = datetime.fromtimestamp(entry["mtime"]).strftime("%Y-%m-%d %H:%M")
        heading = f"  [{stamp}] ({entry['kind']})"
    elif entry["kind"] == "cross-cutting":
        heading = f"Cross-cutting: {_clip(entry['title'], 120)}\n  Category: {entry['category']}"
    elif entry["kind"] == "preference":
        heading = f"Preferences [{entry['source']}]"
    else:
        heading = f"{_clip(entry['file'], 160)} ({group_count} matches)"
    prefix = f"{heading}\n  {anchor}\n  {metadata} [{labels}]\n  {identity}\n"
    if entry["refs"]:
        label = "Refs" if entry["kind"] == "cross-cutting" else "Also involves"
        prefix += f"  {label}: {_clip(', '.join(entry['refs']), 160)}\n"
    if style == "labels" and len(entry["labels"]) > 1:
        prefix += f"  Also labeled: {labels}\n"
    return prefix + "  ", text


def _read_scores(store, identities):
    if store is not None:
        try:
            return store.scores(identities), True
        except (OSError, ValueError, sqlite3.Error) as exc:
            if is_busy_error(exc):
                warn("Citation ledger busy; scores are unknown. Counting will still be attempted after output if enabled.")
            else:
                warn(f"Cannot read citation scores: {exc}. Scores are unknown; check the local cache.")
    return {}, False


def _record_visible(store, identities, options, event=None):
    if store is not None and identities and options.record:
        try:
            store.record(identities, options.event_id if event is None else event)
        except (OSError, ValueError, sqlite3.Error) as exc:
            if is_busy_error(exc):
                warn("Citation ledger busy; this visit was not recorded (scores were not updated). Retry later with the same --event-id if supplied.")
            else:
                warn(f"Citation scores were not updated: {exc}. This visit was not recorded; repair local state and retry.")


def _catalog_digest(entries, recent):
    values = [
        {key: value for key, value in entry.items() if recent or key != "mtime"}
        for entry in sorted(entries, key=lambda entry: entry["id"])
    ]
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _emit_knowledge(shadow_dir, entries, header, options, *, style="search", request=""):
    store = _citation_store(shadow_dir)
    scores, scores_known = _read_scores(store, [entry["id"] for entry in entries])
    ranked = _rank_entries(entries, scores, options.limit, recent=style == "recent")
    catalog = "" if style == "top" else _catalog_digest(entries, recent=style == "recent")
    token = None
    offset = 0
    if options.cursor:
        match = re.fullmatch(r"([0-9a-f]{32}):(\d+)", options.cursor)
        if not match:
            raise ValueError("Invalid cursor; copy the --cursor value from the previous response")
        if store is None:
            raise ValueError("Cannot resume cursor without the local ledger; repair it or restart the query")
        token, offset = match.group(1), int(match.group(2))
        ids = store.load_page(token, request, catalog)
        by_id = {entry["id"]: entry for entry in entries}
        try:
            ranked = [by_id[identity] for identity in ids]
        except KeyError as exc:
            raise ValueError("Invalid local pagination snapshot; restart the query") from exc
        if offset >= len(ranked):
            raise ValueError("Cursor is past the available results; restart the query")

    counts = defaultdict(int)
    for entry in entries:
        counts[entry["file"]] += 1
    # Include the terminal newline and continuation instructions in the budget.
    cap = options.max_chars
    prefix = _clip(header, min(300, cap // 5)) if cap else header
    reserve = 90 if style != "top" else 6
    width = min(options.limit, len(ranked) - offset)
    while width:
        selected = ranked[offset:offset + width]
        parts = [
            _preview_parts(entry, scores.get(entry["id"], 0) if scores_known else "?", style, counts[entry["file"]])
            for entry in selected
        ]
        if cap:
            used = len(prefix) + reserve + 3
            fits = 0
            for entry_prefix, text in parts:
                used += len(entry_prefix) + min(12, len(text)) + 1
                if used > cap:
                    break
                fits += 1
            if fits < width:
                if width > 1:
                    width = max(1, fits)
                    if not options.cursor:
                        ranked = _rank_entries(entries, scores, width, recent=style == "recent")
                    continue
                entry = selected[0]
                score = scores.get(entry["id"], 0) if scores_known else "?"
                minimal = f"({entry['status']}, source: {entry['source']}) id={entry['id']} citation_score={score}: "
                parts = [(minimal, entry["text"])]
        break
    body_budget = cap - len(prefix) - reserve - 1 - width - sum(len(part[0]) for part in parts) if cap else 180 * width
    if width and body_budget < width:
        raise ValueError("Output budget cannot fit discovery content; increase the character budget")
    snippets = [0] * width
    # Distribute spare room across entries rather than dropping a whole warning.
    pending = list(range(width))
    while pending and body_budget:
        share = max(1, body_budget // len(pending))
        next_pending = []
        for index in pending:
            remaining = min(180, len(parts[index][1])) - snippets[index]
            take = min(remaining, share, body_budget)
            snippets[index] += take
            body_budget -= take
            if snippets[index] < min(180, len(parts[index][1])):
                next_pending.append(index)
        pending = next_pending
    output = prefix + "".join(
        "\n" + entry_prefix + _clip(text, snippets[index])
        for index, (entry_prefix, text) in enumerate(parts)
    )
    shown = [entry["id"] for entry in selected]
    next_offset = offset + len(shown)
    if next_offset < len(ranked):
        if style == "top":
            output += "\n(...)"
        else:
            if token is None and store is not None:
                try:
                    token = store.save_page(request, catalog, [entry["id"] for entry in ranked])
                except (OSError, ValueError, sqlite3.Error) as exc:
                    warn(f"Cannot save pagination: {exc}. Repair the local ledger or narrow the query.")
            if token:
                output += f"\nMore: --cursor {token}:{next_offset} (same view)"
            else:
                output += "\nMore omitted: narrow the query (local ledger unavailable)."
    if style != "top":
        output += "\nExpand a claim: --get ID"
    output = prefix.replace("{shown}", str(len(shown))) + output[len(prefix):]
    if cap and len(output) + 1 > cap:
        raise ValueError("Output budget cannot fit retrieval metadata; increase --max-chars")
    print(output, flush=True)
    _record_visible(store, shown, options)


def view_get(shadow_dir, identity, *, options=None):
    """Expand a current discovery, chunking long text without changing its ID."""
    options = options or RetrievalOptions()
    if not re.fullmatch(r"d_[0-9a-f]{32}", identity):
        raise ValueError("--get requires the complete id=d_... value from a retrieval result")
    entry = next((entry for entry in _knowledge_entries(shadow_dir) if entry["id"] == identity), None)
    if entry is None:
        raise ValueError("Discovery ID is absent or its claim changed; search again for its current ID")
    store = _citation_store(shadow_dir)
    scores, scores_known = _read_scores(store, [identity])
    score = scores.get(identity, 0) if scores_known else "?"
    body = entry["anchor"] + "\n\n" + entry["text"]
    if entry["labels"]:
        body += "\nLabels: " + ", ".join(entry["labels"])
    if entry["kind"] == "cross-cutting":
        body += "\nCategory: " + entry["category"] + "\nTitle: " + entry["title"]
    if entry["refs"]:
        body += "\nRefs: " + ", ".join(entry["refs"])
    if entry["dream_report"]:
        body += "\nDream report: " + entry["dream_report"]
    revision = hashlib.sha256(json.dumps(
        [entry["status"], entry["source"], body], ensure_ascii=False,
    ).encode("utf-8")).hexdigest()[:32]
    start = 0
    event = options.event_id
    expires = int(time.time()) + RECEIPT_TTL
    record = options.record
    if options.text_cursor:
        match = re.fullmatch(
            r"([0-9a-f]{32})~(\d{1,12})~([01])~([A-Za-z0-9._:-]{1,128})~(\d+)",
            options.text_cursor,
        )
        if not match:
            raise ValueError("Invalid text cursor; copy the --text-cursor value from the previous expansion")
        prior_revision, expiry, recording, prior_event, offset = match.groups()
        if prior_revision != revision:
            raise ValueError("Discovery body or metadata changed; restart --get without --text-cursor")
        if int(expiry) <= time.time():
            raise ValueError("Text cursor expired; restart --get without --text-cursor")
        if event is not None and event != prior_event:
            raise ValueError("Text cursor has a different --event-id; reuse its original event")
        start, expires, event = int(offset), int(expiry), prior_event
        record = record and recording == "1"
        if start >= len(body):
            raise ValueError("Text cursor is past the end of this discovery; restart --get")
    header = (
        f"id={identity} citation_score={score}\n"
        f"({entry['status']}, source: {entry['source']})\n"
    )
    tail = ""
    available = len(body) - start
    if options.max_chars and len(header) + available + 1 > options.max_chars:
        event = event or uuid.uuid4().hex

        def continuation(offset):
            token = f"{revision}~{expires}~{int(record)}~{event}~{offset}"
            value = f"\nContinue: --get {identity} --text-cursor {token}"
            if options.max_chars != 4000:
                value += f" --max-chars {options.max_chars}"
            return value

        available = options.max_chars - len(header) - len(continuation(len(body))) - 1
        if available < 1:
            raise ValueError("Output budget cannot fit continuation metadata; increase --max-chars")
        tail = continuation(start + available)
    output = header + body[start:start + available] + tail
    print(output, flush=True)
    if record:
        _record_visible(store, [identity], options, event)


def view_summary(shadow_dir):
    """Overview + detailed statistics.

    Each section is independently wrapped — if label stats fail, you
    still get counts and the per-file table.
    """
    # Load data (each can fail independently)
    state = {}
    shadow_files = []
    prefs = []
    cross = []

    try:
        state = load_state(shadow_dir)
    except Exception as e:
        warn(f"Failed to load state.json: {e}")

    try:
        shadow_files = get_all_shadow_files(shadow_dir)
    except Exception as e:
        warn(f"Failed to list shadow files: {e}")

    try:
        prefs = parse_prefs(shadow_dir)
    except Exception as e:
        warn(f"Failed to parse preferences: {e}")

    try:
        cross = parse_cross_cutting(shadow_dir)
    except Exception as e:
        warn(f"Failed to parse cross-cutting discoveries: {e}")

    # Parse each file once for both stats and discoveries
    file_stats = []
    all_disc = []
    total_symbols = 0
    for sf in shadow_files:
        try:
            parsed = parse_shadow_file(sf)
            src = parsed["source_file"] or str(sf.relative_to(shadow_dir))
            n_sym = len(parsed["symbols"])
            n_disc = len(parsed["discoveries"])
            total_symbols += n_sym
            file_stats.append((src, n_sym, n_disc))
            for d in parsed["discoveries"]:
                d.setdefault("file", parsed["source_file"])
                d["shadow_path"] = str(sf.relative_to(shadow_dir))
                all_disc.append(d)
        except Exception as e:
            warn(f"Failed to process {sf}: {e}")
    file_stats.sort(key=lambda x: x[2], reverse=True)

    # Header counts (always shown)
    print("Shadow Knowledge Base Summary")
    print("=" * 50)
    print(f"  Files shadowed:    {len(shadow_files)}")
    print(f"  Symbols tracked:   {total_symbols}")
    print(f"  Discoveries:       {len(all_disc)}")
    print(f"  Preferences:       {len(prefs)}")
    print(f"  Cross-cutting:     {len(cross)}")

    # Source breakdown
    try:
        source_counts = defaultdict(int)
        status_counts = defaultdict(int)
        for d in all_disc:
            source_counts[d.get("source", "unknown")] += 1
            status_counts[d.get("status", "unknown")] += 1

        if source_counts:
            print("\nBy source:")
            for src, cnt in sorted(source_counts.items(), key=lambda x: -x[1]):
                pct = cnt / len(all_disc) * 100 if all_disc else 0
                bar = "#" * int(pct / 2)
                print(f"  {src:15s} {cnt:4d} ({pct:5.1f}%) {bar}")

        if status_counts:
            print("\nBy status:")
            for st, cnt in sorted(status_counts.items(), key=lambda x: -x[1]):
                pct = cnt / len(all_disc) * 100 if all_disc else 0
                bar = "#" * int(pct / 2)
                print(f"  {st:15s} {cnt:4d} ({pct:5.1f}%) {bar}")
    except Exception as e:
        warn(f"Failed to compute source/status breakdown: {e}")

    # Label breakdown
    try:
        label_counts = defaultdict(int)
        for d in all_disc:
            for lbl in d.get("labels", []):
                label_counts[lbl] += 1
        if label_counts:
            print("\nBy label:")
            for lbl, cnt in sorted(label_counts.items(), key=lambda x: -x[1]):
                print(f"  {lbl:15s} {cnt:4d}")
    except Exception as e:
        warn(f"Failed to compute label breakdown: {e}")

    # Per-file table
    try:
        if file_stats:
            print(f"\n{'File':<40s} {'Symbols':>8s} {'Disc.':>6s}")
            print(f"{'-'*40} {'-'*8} {'-'*6}")
            for src, n_sym, n_disc in file_stats[:20]:
                print(f"{src:<40s} {n_sym:>8d} {n_disc:>6d}")
            if len(file_stats) > 20:
                print(f"... and {len(file_stats) - 20} more files")
    except Exception as e:
        warn(f"Failed to render per-file table: {e}")

    # Cross-cutting titles
    try:
        if cross:
            print(f"\nCross-cutting discoveries:")
            for e in cross:
                title = e.get("title", e.get("slug", "?"))
                cat = e.get("category", "?")
                print(f"  [{cat}] {title}")
    except Exception as e:
        warn(f"Failed to render cross-cutting list: {e}")

    # State info
    try:
        if state:
            print(f"\nLast update: {state.get('last_update_at', '?')} "
                  f"({state.get('last_update_type', '?')})")
            print(f"Last commit: {state.get('last_commit', '?')}")
    except Exception as e:
        warn(f"Failed to render state info: {e}")


def view_search(shadow_dir, query, *, options=None):
    """Bounded search with stable continuation over matching knowledge identities."""
    options = options or RetrievalOptions()
    query_lower = query.lower()
    if not query.strip():
        raise ValueError("Search query must be nonempty")
    matches = []
    for entry in _knowledge_entries(shadow_dir):
        fields = [entry["anchor"], entry["file"], entry["symbol"], entry["text"],
                  entry["title"], *entry["refs"]]
        if any(query_lower in field.lower() for field in fields):
            entry["relevance"] = 0 if query_lower in [field.lower() for field in fields[:3]] else 1
            matches.append(entry)
    if not matches and not options.cursor:
        print(_clip(f"No results for '{query}'.", options.max_chars - 1) if options.max_chars
              else f"No results for '{query}'.")
        return
    _emit_knowledge(
        shadow_dir, matches, f"Search: '{query}' ({len(matches)} results)", options,
        request=json.dumps(["search", query_lower]),
    )


def view_symbol(shadow_dir, anchor, *, options=None):
    options = options or RetrievalOptions()
    file, separator, symbol = anchor.partition("::")
    if not separator or not symbol:
        raise ValueError("--symbol requires file::symbol (use File-Level for a file-level section)")
    file = _canonical_source_file(shadow_dir, file)
    symbol = _canonical_symbol(symbol)
    canonical = f"{file}::{symbol}"
    matches = [
        entry for entry in _knowledge_entries(shadow_dir, file)
        if entry["anchor"] == canonical or canonical in entry["refs"]
    ]
    if not matches and not options.cursor:
        print(_clip(f"No knowledge for '{anchor}'.", options.max_chars - 1)
              if options.max_chars else f"No knowledge for '{anchor}'.")
        return
    _emit_knowledge(
        shadow_dir, matches, f"Knowledge for {anchor} ({len(matches)} results)", options,
        request=json.dumps(["symbol", canonical]),
    )


def view_file(shadow_dir, source_file, *, options=None):
    """Read one known source file's shadow, including file-level and cross refs."""
    options = options or RetrievalOptions()
    source_file = _canonical_source_file(shadow_dir, source_file)
    entries = _knowledge_entries(shadow_dir, source_file)
    if not entries and not options.cursor:
        message = f"No knowledge for '{source_file}'."
        print(_clip(message, options.max_chars - 1) if options.max_chars else message)
        return
    _emit_knowledge(
        shadow_dir, entries, f"Knowledge for {source_file} ({len(entries)} results)",
        options, request=json.dumps(["file", source_file]),
    )


def view_prefs(shadow_dir, *, options=None):
    """Page preferences without letting popular code discoveries hide directives."""
    options = options or RetrievalOptions()
    prefs = _preference_entries(shadow_dir)
    if not prefs and not options.cursor:
        print("No preferences recorded yet.")
        return
    _emit_knowledge(shadow_dir, prefs, f"Project Preferences ({len(prefs)} total)", options, request="prefs")


def view_labels(shadow_dir, label_filter, *, options=None):
    """Show discoveries filtered by label(s).

    label_filter can be a single label or comma-separated list.
    """
    options = options or RetrievalOptions()
    filters = [label.strip().lower() for label in label_filter.split(",") if label.strip()]
    if not filters:
        raise ValueError("Supply at least one label with --labels")
    matching = [
        entry for entry in _knowledge_entries(shadow_dir)
        if set(filters) & {label.lower() for label in entry["labels"]}
    ]

    if not matching and not options.cursor:
        message = f"No discoveries with label(s): {', '.join(filters)}"
        print(_clip(message, options.max_chars - 1) if options.max_chars else message)
        return

    _emit_knowledge(
        shadow_dir, matching,
        f"Discoveries with label(s): {', '.join(filters)} ({len(matching)} results)",
        options, style="labels", request=json.dumps(["labels", sorted(set(filters))]),
    )


def view_recent(shadow_dir, count=10, *, options=None):
    """Show the N most recent discoveries (by shadow file mtime).

    Collects all discoveries across all shadow files, cross-cutting entries,
    and preferences, sorts by the source file's modification time (most recent
    first), and shows the actual discovery content.
    Each data source is independent — if cross-cutting fails, per-file
    discoveries still appear.
    """
    options = options or RetrievalOptions(limit=count)
    all_items = _knowledge_entries(shadow_dir)
    if not all_items and not options.cursor:
        print("No discoveries found.")
        return

    _emit_knowledge(
        shadow_dir, all_items, f"Most Recent Discoveries (top {count})",
        options, style="recent", request="recent",
    )


def view_top(shadow_dir, file_path, labels_filter, limit, max_chars, *, options=None):
    """Show the top N actionable discoveries for a single source file.

    Designed for the preToolUse hook: concise output suitable for
    inlining into additionalContext when the agent is about to mutate a
    file. Pulls from both the per-file shadow and any _cross/ entries
    whose refs touch this file.

    Trust/status precedes citation score. Output, including the final newline,
    is hard-capped; only entries actually emitted are counted.
    """
    norm = file_path.strip()
    if norm.startswith("./"):
        norm = norm[2:]
    label_set = {l.strip().lower() for l in labels_filter.split(",") if l.strip()}
    options = options or RetrievalOptions(limit=limit, max_chars=max_chars)
    candidates = [
        entry for entry in _knowledge_entries(shadow_dir, norm)
        if not label_set or label_set & {label.lower() for label in entry["labels"]}
    ]

    if not candidates:
        labels_disp = ",".join(sorted(label_set)) if label_set else "any"
        message = f"No actionable discoveries ({labels_disp}) for {norm}."
        print(_clip(message, max_chars - 1) if max_chars else message)
        return
    _emit_knowledge(
        shadow_dir, candidates,
        f"Top {{shown}} of {len(candidates)} actionable discoveries for {norm}:",
        options, style="top", request=json.dumps(["top", norm, sorted(label_set)]),
    )


def view_check_invariants(shadow_dir):
    """Walk the shadow knowledge base and report invariant violations.

    Statically-checkable invariants from shadow-frog/SKILL.md:
      #3 (partial) Per-file 'Also involves:' uses file::symbol notation
      #4 Cross-ref back-pointers match: _cross/<slug>.md refs <->
         per-file ## Cross-References
      #5 Every ## Cross-References entry has a matching _cross/*.md

    Plus syntactic guards that catch the most common drift:
      - Symbol headings use the required backtick form
      - Discovery metadata uses valid status enum
      - Discovery metadata uses valid source enum
      - Discovery labels are from the allowed set
      - _cross/ Category field uses a known value

    Invariants #1, #2, #7 are NOT checked (would require source parsing
    and semantic match); #6 is filesystem-enforced.

    Exit 0 = clean, 1 = at least one violation. Violations print one per
    line in `path:line: kind: message` form so grep/editors can navigate.
    """
    VALID_STATUS = {"verified", "uncertain", "refuted"}
    VALID_SOURCE = {"exploration", "user", "interaction"}
    VALID_LABELS = {"bug", "performance", "security",
                    "feature-gap", "tech-debt"}
    VALID_CATEGORIES = {
        "pattern", "behavior", "edge-case", "contract",
        "performance", "intent", "warning", "history", "convention",
    }

    violations = []
    def v(path, line, kind, msg):
        violations.append(f"{path}:{line}: {kind}: {msg}")

    # Pass 1: walk per-file shadows -> collect cross-reference entries
    # they declare and validate their internal format.
    per_file_xref_targets = {}  # rel_shadow_path -> set(slug declared)
    cross_dir = shadow_dir / "_cross"
    cross_slugs_on_disk = set()
    if cross_dir.is_dir():
        try:
            cross_slugs_on_disk = {f.stem for f in cross_dir.glob("*.md")}
        except OSError as e:
            warn(f"Cannot list {cross_dir}: {e}")

    md_heading_re = re.compile(r"^(#{2,3})\s+(.*)$")
    backtick_heading_re = re.compile(r"^(#{2,3})\s+`[^`]+`\s*$")
    also_involves_re = re.compile(r"^\s*Also involves:\s*(.+)$", re.I)
    file_sym_re = re.compile(r"`([^`]+::[^`]+)`")

    for shadow_path in get_all_shadow_files(shadow_dir):
        try:
            rel = shadow_path.relative_to(shadow_dir)
        except ValueError:
            continue
        try:
            text = shadow_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            v(rel, 0, "unreadable", str(e))
            continue

        in_cross_refs = False
        declared = set()
        for ln, raw in enumerate(text.split("\n"), 1):
            line = raw.rstrip()

            heading = md_heading_re.match(line)
            if heading:
                title = heading.group(2).strip()
                if title.lower().startswith("cross-references"):
                    in_cross_refs = True
                    continue
                in_cross_refs = False
                # Skip special headings ("File-Level Notes", "Notes", etc.)
                if (
                    title.lower().startswith("file-level")
                    or title.lower() in {"notes", "metadata"}
                ):
                    continue
                # Symbol heading must use backtick form
                if not backtick_heading_re.match(line):
                    v(rel, ln, "heading",
                      f"symbol heading must be `## `name`` or "
                      f"`### `Class.name``; got: {line[:80]}")
                continue

            if in_cross_refs and line.strip().startswith("- "):
                # Format: - [slug](.shadow/_cross/slug.md) — title
                slug_match = re.search(
                    r"_cross/([^)\s]+?)\.md", line
                )
                if slug_match:
                    declared.add(slug_match.group(1))
                else:
                    # Looser fallback: bare slug in brackets
                    alt = re.search(r"\[([^\]]+)\]", line)
                    if alt:
                        declared.add(alt.group(1).strip())

            # Discovery metadata line
            md = _DISCOVERY_META_RE.search(line)
            if md:
                status, source = md.group(1), md.group(2)
                labels_raw = md.group(3) or ""
                if status not in VALID_STATUS:
                    v(rel, ln, "enum",
                      f"status '{status}' not in {sorted(VALID_STATUS)}")
                if source not in VALID_SOURCE:
                    v(rel, ln, "enum",
                      f"source '{source}' not in {sorted(VALID_SOURCE)}")
                for lbl in (l.strip() for l in labels_raw.split(",") if l.strip()):
                    if lbl not in VALID_LABELS:
                        v(rel, ln, "enum",
                          f"label '{lbl}' not in {sorted(VALID_LABELS)}")

            # `Also involves:` must list file::symbol anchors in backticks
            ai = also_involves_re.match(line)
            if ai:
                rest = ai.group(1)
                anchors = file_sym_re.findall(rest)
                if not anchors:
                    v(rel, ln, "anchor",
                      "Also involves: needs `file::symbol` "
                      "backtick anchors")
                # Light sanity: every anchor has both file and symbol
                for a in anchors:
                    if "::" not in a or not a.split("::", 1)[1].strip():
                        v(rel, ln, "anchor",
                          f"anchor '{a}' missing symbol after ::")

        per_file_xref_targets[str(rel)] = declared

        # Invariant #5: every declared cross slug must exist on disk
        for slug in declared:
            if slug not in cross_slugs_on_disk:
                v(rel, 0, "cross-ref",
                  f"references _cross/{slug}.md but file does not exist")

    # Pass 2: walk _cross/*.md -> validate refs format + back-pointer.
    # Build the reverse map: cross_slug -> set(file::symbol it points at).
    cross_back = {}  # slug -> set(file paths it should be linked from)
    if cross_dir.is_dir():
        for cf in sorted(cross_dir.glob("*.md")):
            slug = cf.stem
            try:
                text = cf.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                v(cf.relative_to(shadow_dir), 0, "unreadable", str(e))
                continue

            rel_cf = cf.relative_to(shadow_dir)

            # Category enum check
            cat_m = re.search(r"\*\*Category\*\*:\s*(.+)", text)
            if cat_m:
                cat = cat_m.group(1).strip().lower()
                if cat not in VALID_CATEGORIES:
                    v(rel_cf, 0, "enum",
                      f"Category '{cat}' not in {sorted(VALID_CATEGORIES)}")
            else:
                v(rel_cf, 0, "schema",
                  "missing **Category**: field")

            # Discovery metadata
            md = _DISCOVERY_META_RE.search(text)
            if md:
                status, source = md.group(1), md.group(2)
                if status not in VALID_STATUS:
                    v(rel_cf, 0, "enum",
                      f"status '{status}' not in {sorted(VALID_STATUS)}")
                if source not in VALID_SOURCE:
                    v(rel_cf, 0, "enum",
                      f"source '{source}' not in {sorted(VALID_SOURCE)}")
            else:
                v(rel_cf, 0, "schema",
                  "missing trailing _(status, source: ...)_ metadata")

            # Refs must be `file::symbol` anchors
            refs_block = re.search(
                r"\*\*Refs\*\*:\s*\n((?:\s*-\s+`[^`]+`\s*\n?)+)",
                text,
            )
            if not refs_block:
                v(rel_cf, 0, "schema",
                  "missing **Refs**: block (one per line, "
                  "`- `file::symbol``)")
            else:
                anchors = file_sym_re.findall(refs_block.group(1))
                if not anchors:
                    v(rel_cf, 0, "anchor",
                      "Refs block has no `file::symbol` entries")
                for a in anchors:
                    if "::" not in a or not a.split("::", 1)[1].strip():
                        v(rel_cf, 0, "anchor",
                          f"ref '{a}' missing symbol after ::")
                    else:
                        # Convert file part to shadow path:
                        # src/foo.py -> src/foo.py.md (relative to shadow_dir)
                        file_part = a.split("::", 1)[0].strip()
                        shadow_rel = f"{file_part}.md"
                        cross_back.setdefault(slug, set()).add(shadow_rel)

    # Invariant #4 back-pointer: every file referenced by a cross slug
    # must declare that slug in its ## Cross-References.
    for slug, expected_files in cross_back.items():
        for shadow_rel in expected_files:
            declared = per_file_xref_targets.get(shadow_rel)
            if declared is None:
                v(f"_cross/{slug}.md", 0, "cross-ref",
                  f"refs {shadow_rel} but no such shadow file exists")
            elif slug not in declared:
                v(f"_cross/{slug}.md", 0, "cross-ref",
                  f"refs {shadow_rel} but that shadow's ## "
                  f"Cross-References does not link back to "
                  f"_cross/{slug}.md")

    # Output
    if not violations:
        print(f"✓ Invariants OK ({len(per_file_xref_targets)} per-file "
              f"shadows, {len(cross_slugs_on_disk)} cross-cutting "
              f"discoveries)")
        return 0

    for line in violations:
        print(line)
    print(f"\n{len(violations)} invariant violation(s) found.",
          file=sys.stderr)
    return 1


def main(*, agent=False):
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8")
    try:
        parser = argparse.ArgumentParser(
            description=(
                "Optional bounded agent retrieval. Navigate directly to shadow files first; "
                "use this helper for large sections or targeted search."
                if agent else
                "Browse and visualize a .shadow/ knowledge base for users."
            ),
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        if agent:
            parser.add_argument(
                "target", nargs="?", metavar="FILE[::SYMBOL]",
                help="Known source file or file::symbol; its shadow path is determined directly",
            )

        # Views (mutually exclusive)
        views = parser.add_mutually_exclusive_group()
        views.add_argument(
            "--summary", action="store_true",
            help="Overview + detailed statistics (default)",
        )
        views.add_argument(
            "--search", metavar="QUERY",
            help="Universal search: files, symbols, and discovery text",
        )
        views.add_argument("--file", metavar="FILE", help="Read a known file's shadow without a repository-wide search")
        views.add_argument("--symbol", metavar="FILE::SYMBOL", help="Knowledge for an exact symbol and its cross-cutting refs")
        views.add_argument("--get", metavar="ID", help="Expand a discovery returned by a bounded read")
        views.add_argument(
            "--prefs", action="store_true",
            help="Show project-wide preferences",
        )
        views.add_argument(
            "--recent", nargs="?", const=10, type=int, metavar="N",
            help="N most recent discoveries with content (default: 10)",
        )
        views.add_argument(
            "--labels", metavar="LABEL",
            help=(
                "Show discoveries by label "
                "(e.g., bug, security, bug,performance)"
            ),
        )
        views.add_argument(
            "--top", metavar="FILE",
            help=(
                "Top actionable discoveries for FILE (a source path "
                "like src/auth.py). Concise output for the preToolUse "
                "hook: filters to actionable labels (default: "
                "bug,security), includes both per-file and _cross/ "
                "entries that reference FILE, ranks verified first."
            ),
        )
        views.add_argument(
            "--check-invariants", action="store_true",
            help=(
                "Walk the shadow and report structural violations: "
                "missing back-pointers, dangling _cross/ refs, invalid "
                "enums, bad heading format. Exit 1 if any are found."
            ),
        )

        # Options
        parser.add_argument(
            "--shadow-dir", default=None,
            help="Path to .shadow/ directory (default: auto-detect)",
        )
        parser.add_argument(
            "--top-labels", default="bug,security", metavar="LABELS",
            help=(
                "Comma-separated labels to include in --top "
                "(default: bug,security). Pass empty string to include "
                "all labeled discoveries."
            ),
        )
        parser.add_argument(
            "--top-limit", type=int, default=3, metavar="N",
            help="Max discoveries to show in --top (default: 3)",
        )
        parser.add_argument(
            "--top-max-chars", type=int, default=600, metavar="N",
            help=(
                "Hard cap on --top total output length "
                "(default: 600). Use 0 for no cap."
            ),
        )
        parser.add_argument("--limit", type=int, help="Results per page for file/search/symbol/labels/prefs (default: 10)")
        parser.add_argument("--max-chars", type=int, help="Retrieval output budget (default: 4000; 0 disables cap)")
        parser.add_argument("--cursor", help="Continue the same view/filters with a frozen result ordering")
        parser.add_argument("--event-id", help="Retry token; an entry counts once per token (default: fresh event)")
        parser.add_argument("--no-record", action="store_true", help="Do not increment citation scores")
        parser.add_argument("--text-cursor", help="Continue the same logical read of an unchanged --get body")

        args = parser.parse_args()
        selected_view = (
            args.summary or args.check_invariants or args.top is not None
            or args.search is not None or args.file is not None or args.symbol is not None
            or args.get is not None or args.prefs or args.labels is not None or args.recent is not None
        )
        if agent and args.target is not None:
            if selected_view:
                parser.error("Use a positional target or an explicit view, not both")
            if "::" in args.target:
                args.symbol = args.target
            else:
                args.file = args.target
        elif agent and not selected_view:
            parser.error("Provide a known FILE[::SYMBOL] or an explicit retrieval operation such as --search")
        retrieval = (
            args.top is not None or args.search is not None or args.file is not None or args.symbol is not None
            or args.get is not None or args.prefs or args.labels is not None or args.recent is not None
        )
        if not retrieval and (
            args.limit is not None or args.max_chars is not None or args.cursor
            or args.event_id is not None or args.no_record or args.text_cursor is not None
        ):
            parser.error("Retrieval options require --search, --file, --symbol, --get, --top, --prefs, --labels, or --recent")
        if args.text_cursor is not None and args.get is None:
            parser.error("--text-cursor requires --get")
        if args.cursor and (args.get is not None or args.top is not None):
            parser.error("--cursor is for paged file/search/symbol/labels/prefs/recent; use --text-cursor with --get")
        if args.limit is not None and (args.top is not None or args.recent is not None or args.get is not None):
            parser.error("Use --top-limit or --recent N instead of --limit; --get returns one discovery")
        if args.max_chars is not None and args.top is not None:
            parser.error("Use --top-max-chars with --top")
        try:
            options = RetrievalOptions(
                limit=args.top_limit if args.top is not None else (
                    args.recent if args.recent is not None else (args.limit if args.limit is not None else 10)
                ),
                max_chars=args.top_max_chars if args.top is not None else (
                    args.max_chars if args.max_chars is not None else 4000
                ),
                cursor=args.cursor, event_id=args.event_id, record=not args.no_record,
                text_cursor=args.text_cursor,
            )
        except ValueError as exc:
            parser.error(str(exc))

        # Find shadow dir
        if args.shadow_dir:
            shadow_dir = Path(args.shadow_dir)
        else:
            shadow_dir = find_shadow_dir()

        if not shadow_dir or not shadow_dir.is_dir():
            cwd = os.getcwd()
            error(
                f"No .shadow/ directory found. "
                f"Searched from: {cwd}\n"
                f"[shadow error] "
                f"Run /shadow-frog-init first to create the shadow, "
                f"or pass --shadow-dir /path/to/.shadow/ explicitly."
            )
            if args.shadow_dir:
                error(
                    f"Provided --shadow-dir '{args.shadow_dir}' does not "
                    f"exist or is not a directory."
                )
            sys.exit(1)

        # Dispatch
        if args.check_invariants:
            sys.exit(view_check_invariants(shadow_dir))
        if args.top is not None:
            view_top(
                shadow_dir,
                args.top,
                args.top_labels,
                args.top_limit,
                args.top_max_chars,
                options=options,
            )
        elif args.search is not None:
            view_search(shadow_dir, args.search, options=options)
        elif args.file is not None:
            view_file(shadow_dir, args.file, options=options)
        elif args.symbol is not None:
            view_symbol(shadow_dir, args.symbol, options=options)
        elif args.get is not None:
            view_get(shadow_dir, args.get, options=options)
        elif args.prefs:
            view_prefs(shadow_dir, options=options)
        elif args.labels is not None:
            view_labels(shadow_dir, args.labels, options=options)
        elif args.recent is not None:
            view_recent(shadow_dir, args.recent, options=options)
        else:
            view_summary(shadow_dir)

    except SystemExit:
        raise
    except KeyboardInterrupt:
        error("Interrupted by user.")
        sys.exit(130)
    except (ValueError, sqlite3.Error) as exc:
        if is_busy_error(exc):
            error("Local citation ledger is busy; retry the same command later. Do not reset a busy database.")
        else:
            error(f"{exc}. Correct the request or repair the local citation ledger, then retry.")
        sys.exit(1)
    except Exception as e:
        error(
            f"Unexpected error: {type(e).__name__}: {e}\n"
            f"[shadow error] Full traceback:\n"
            f"{traceback.format_exc()}"
            f"This is likely a bug in the shadow knowledge helper. "
            f"The shadow data may be in an unexpected format. "
            f"Try running with --shadow-dir to confirm the path, "
            f"or inspect the .shadow/ files manually."
        )
        sys.exit(1)
