"""Visible Markdown citation metadata and serialized, exact-entry increments."""

from contextlib import contextmanager
import os
from pathlib import Path
import re
import stat
import tempfile
import time


DISCOVERY_META_RE = re.compile(
    r"_\((\w+),\s*source:\s*(\w+)"
    r"(?:,\s*labels:\s*\[([^\]]*)\])?"
    r"(?:,\s*citation_score:\s*([0-9]+))?\)_"
)
PREFERENCE_META_RE = re.compile(
    r"_\(source:\s*(\w+)(?:,\s*citation_score:\s*([0-9]+))?\)_"
)


class CitationError(ValueError):
    """An entry cannot be safely identified or its score cannot be updated."""


def validate_score(value, field="citation_score"):
    if type(value) is not int or value < 0:
        raise CitationError(f"{field} must be a nonnegative integer")
    return value


def metadata_score(line):
    stripped = line.strip()
    discovery = DISCOVERY_META_RE.fullmatch(stripped)
    preference = PREFERENCE_META_RE.fullmatch(stripped)
    if discovery:
        return int(discovery.group(4) or 0)
    if preference:
        return int(preference.group(2) or 0)
    raise CitationError("Malformed metadata: use a nonnegative integer citation_score after optional labels")


def set_metadata_score(line, score):
    """Change only the score field, preserving other text and line endings."""
    validate_score(score)
    metadata_score(line)
    if "citation_score:" in line:
        return re.sub(r"(citation_score:\s*)[0-9]+", lambda match: match.group(1) + str(score), line, count=1)
    closing = line.rfind(")_")
    return line[:closing] + f", citation_score: {score}" + line[closing:]


def _symbol(value):
    if value is None:
        return None
    value = value.strip().strip("`")
    return re.sub(r"^(?:class|interface|enum|trait|struct|protocol|module) ", "", value)


def _claim(value):
    # Join visual line wrapping, but do not collapse whitespace inside literals.
    return re.sub(r"[ \t]*\r?\n[ \t]*", " ", value.strip())


def _entries(lines, kind):
    section = None
    claim = None
    fence = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if fence is not None:
            if claim is not None:
                claim.append(stripped)
            if re.fullmatch(re.escape(fence[0]) + "{" + str(len(fence)) + ",}", stripped):
                fence = None
            continue
        opening = re.match(r"`{3,}|~{3,}", stripped)
        if opening:
            fence = opening.group(0)
            if claim is not None:
                claim.append(stripped)
            continue
        if re.match(r"#{1,6}\s", stripped):
            heading = re.fullmatch(r"#{2,3}\s+(?:`(.+)`|(File-Level|Cross-References))", stripped)
            section = _symbol(heading.group(1) or heading.group(2)) if heading else None
            claim = None
            continue
        if kind == "cross" and stripped.startswith("**Discovery**:"):
            claim = [stripped.partition(":")[2].strip()]
        elif kind != "cross" and stripped.startswith("- "):
            claim = [stripped[2:]] if kind == "preference" or section not in (None, "Cross-References") else None
        elif claim is not None:
            if stripped.startswith("_("):
                try:
                    metadata_score(line)
                except CitationError as exc:
                    raise CitationError(f"Line {index + 1}: {exc}") from exc
                yield section if kind == "file" else None, _claim("\n".join(claim)), index
                claim = None
            elif stripped.startswith(("Also involves:", "Dream report:", "#")):
                claim = None
            elif stripped:
                claim.append(stripped)


def cross_metadata_line(lines):
    """Locate the single actual cross-cutting discovery's metadata, excluding examples."""
    entries = list(_entries(lines, "cross"))
    if len(entries) > 1:
        raise CitationError("Cross-cutting file contains multiple discoveries; resolve ambiguity before updating scores")
    return entries[0][2] if entries else None


def _target(path, shadow_dir):
    literal = Path(path).absolute()
    if shadow_dir is None:
        root = next((parent for parent in literal.parents if parent.name == ".shadow"), None)
        if root is None:
            raise CitationError("Provide a file under .shadow/ or an explicit --shadow-dir")
    else:
        root = Path(shadow_dir).absolute()
    resolved_root = root.resolve()
    if resolved_root != root.parent.resolve() / root.name:
        raise CitationError("The shadow root is a filesystem alias; use a real shadow directory before recording citations")
    resolved = literal.resolve()
    if not resolved.is_relative_to(resolved_root) or not resolved.is_file():
        raise CitationError("Citation target must be an existing Markdown file inside the shadow directory")
    relative = resolved.relative_to(resolved_root)
    if relative.suffix != ".md" or relative.parts[0] in ("_meta", "_dreams", "_index.md"):
        raise CitationError("Cite discovery or preference entries, not indexes or dream reports")
    kind = "preference" if relative.as_posix() == "_prefs.md" else (
        "cross" if len(relative.parts) == 2 and relative.parts[0] == "_cross" else "file"
    )
    return resolved, kind


@contextmanager
def _locked(path, timeout):
    lock = path.with_name(path.name + ".citation.lock")
    deadline = time.monotonic() + timeout
    while True:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise CitationError(
                    f"Citation writer busy: {lock}. Retry later; after an interruption, "
                    "confirm its writer stopped before removing only that lock."
                ) from None
            time.sleep(min(0.02, max(0, deadline - time.monotonic())))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(f"pid={os.getpid()}\n")
        yield
    finally:
        lock.unlink()


def record_citations(path, texts, *, symbol=None, shadow_dir=None, timeout=2.0):
    """Increment each selected claim once; no retrieval, database, or hidden IDs."""
    path, kind = _target(path, shadow_dir)
    if kind == "file" and not symbol:
        raise CitationError("Per-file citations require --symbol (use File-Level for file-wide knowledge)")
    if kind != "file" and symbol is not None:
        raise CitationError("Preferences and cross-cutting discoveries do not use --symbol")
    if not texts or any(not isinstance(text, str) or not text.strip() for text in texts):
        raise CitationError("Supply --text with the exact discovery text already consulted")
    targets = list(dict.fromkeys(_claim(text) for text in texts))
    selected_symbol = _symbol(symbol)
    with _locked(path, timeout):
        original = path.read_bytes()
        lines = original.decode("utf-8").splitlines(keepends=True)
        try:
            entries = list(_entries(lines, kind))
        except CitationError as exc:
            raise CitationError(f"{path}: {exc}") from exc
        updates = []
        for text in targets:
            matches = [
                index for entry_symbol, entry_text, index in entries
                if entry_symbol == selected_symbol and entry_text == text
            ]
            if len(matches) != 1:
                raise CitationError(
                    f"{path}: expected one matching entry for {symbol or kind} / {text!r}, "
                    f"found {len(matches)}. Re-read that entry; correct the text or resolve duplicates."
                )
            index = matches[0]
            before = metadata_score(lines[index])
            lines[index] = set_metadata_score(lines[index], before + 1)
            updates.append({"text": text, "before": before, "after": before + 1})
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".cite-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write("".join(lines).encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
            if path.read_bytes() != original:
                raise CitationError("Shadow changed during citation update; coordinate writers and retry")
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
    return updates
