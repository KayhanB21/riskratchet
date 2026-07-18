"""EXPERIMENTAL: map Istanbul/nyc or LCOV coverage onto discovered TypeScript function spans
(P20, slice 3, since 0.2.13; LCOV since 0.2.16).

Discovery (`typescript.py`) tells us *where* each TypeScript function is; this module
answers *how well tested* it is, by reading an Istanbul `coverage-final.json` (the dominant
TS/JS coverage artifact, produced by `nyc`/`c8`/Jest's `--coverage`) or an LCOV `lcov.info`
(the other common artifact — `c8 --reporter=lcov`, Karma, many Jest reporters, and CI
uploaders emit it) and intersecting its per-line / per-branch data with a function's line
span. The result is a `CoverageStats` — the same shape the Python backend produces in
`coverage.py`.

**The shape is shared; the *semantics* are not identical, and equal percentages do not
mean equal measurement.** TS `line_coverage` here is *statement-start-derived* (a line is
"measured" iff an Istanbul statement starts on it), whereas the Python backend's is
line-level (coverage.py's executable-line set). A TS function at 80% and a Python function
at 80% are not the same denominator. So this is *not* "consumable unchanged" by a future
cross-language scoring pipeline — it must be recalibrated first (see
`docs/language-backend-contract.md §2`). TS branch arcs also have no `(src_line, dst_line)`
analog, so they go in `CoverageStats.missing_branch_arms` as `(line, arm_index)`, never in
the Python `missing_branches`.

This is informational only — like discovery, it never feeds scoring, baseline, or gating in
0.2.x. It is reached solely through the `scan --experimental-typescript --ts-coverage` path
and imports no tree-sitter (it is pure JSON), so a Python-only install is unaffected.

When coverage was collected on *compiled JS* (c8/V8, or nyc instrumenting built output
without `babel-plugin-istanbul`) and not source-map-remapped, the report's line numbers
refer to JS, not the `.ts` we parse — the two silently misalign. `spans_cover_any_statement`
gives callers a cheap detector so they can warn and suppress coverage rather than show
confidently-wrong numbers.

Istanbul format (mirrors what `nyc` writes): a top-level object keyed by absolute file path,
each value carrying `statementMap`/`s` (per-statement ranges + hit counts), `branchMap`/`b`
(per-branch arm ranges + per-arm hit counts), and `fnMap`/`f`. All map keys and `s`/`b`/`f`
keys are JSON strings; lines are 1-based, columns 0-based. Unknown keys (`_coverageSchema`,
`hash`, `inputSourceMap`) are tolerated.

LCOV (`lcov.info`) is supported since 0.2.16. Rather than teach the mapping functions a second
shape, an LCOV report is parsed into the *same* synthetic Istanbul-shaped per-file dict — each
`DA:<line>,<hits>` becomes a one-line "statement" (`statementMap`/`s`); each `BRDA:` group,
keyed by `(line, block)`, becomes a synthetic branch (`branchMap`/`b`). So `coverage_for_ts_span`,
`spans_cover_any_statement`, and the merge/lookup machinery are reused unchanged. LCOV is
*line*-oriented (a line is "measured" iff it has a `DA` record), a third measurement basis
distinct from Istanbul's statement-start lines and coverage.py's executable-line set — so, as in
`docs/language-backend-contract.md §2`, equal percentages across backends do not mean equal
measurement. LCOV `FN`/`FNDA` (function hit counts) and the `LF`/`LH`/`BRF`/`BRH` file totals have
no home in `CoverageStats` and are parsed-and-ignored; `missing_branches` stays empty (LCOV, like
Istanbul, has no `(src_line, dst_line)` arc).

Format is auto-detected per file (extension `.info`/`.lcov` or a leading `TN:`/`SF:` line → LCOV;
a leading `{` → Istanbul JSON), so a single `--ts-coverage` list may mix both and merges them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from riskratchet.coverage import MissingCoveragePolicy
from riskratchet.models import CoverageStats, FunctionSpan

# Branch `type`s Istanbul reports. All are counted toward the branch denominator (faithful to
# raw nyc totals) — including `default-arg`, which the Python backend has no analog for.
# Documented in docs/language-backend-contract.md §2.


@dataclass(frozen=True)
class IstanbulCoverageData:
    """Indexed view of an Istanbul `coverage-final.json`.

    `_files` is keyed by the original (usually absolute) path strings; `_by_suffix`
    accelerates lookups by relative path or basename, since the report's paths are absolute
    and machine-specific while discovery works in repo-relative posix paths. Mirrors
    `coverage.CoverageData`.
    """

    _files: dict[str, dict[str, Any]]
    _by_suffix: dict[str, list[str]]

    @property
    def file_paths(self) -> tuple[str, ...]:
        return tuple(self._files.keys())

    def lookup(self, relative_posix_path: str) -> dict[str, Any] | None:
        if relative_posix_path in self._files:
            return self._files[relative_posix_path]
        candidates = self._by_suffix.get(_basename(relative_posix_path))
        if not candidates:
            return None
        # Longest-suffix wins: prefer the candidate that matches the most trailing path.
        best: str | None = None
        for candidate in candidates:
            matches = candidate == relative_posix_path or candidate.endswith("/" + relative_posix_path)
            if matches and (best is None or len(candidate) > len(best)):
                best = candidate
        return self._files[best] if best is not None else None


def load_istanbul_coverage(path: Any) -> IstanbulCoverageData:
    """Load an Istanbul `coverage-final.json` from disk. Raises FileNotFoundError if missing,
    ValueError on unreadable/non-object content (mirrors `coverage.load_coverage`)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read Istanbul coverage file {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"Istanbul coverage file {path} is not a JSON object of file entries")

    files: dict[str, dict[str, Any]] = {}
    by_suffix: dict[str, list[str]] = {}
    for original_path, payload in raw.items():
        if not isinstance(payload, dict):
            continue
        normalized = original_path.replace("\\", "/")
        files[normalized] = payload
        by_suffix.setdefault(_basename(normalized), []).append(normalized)
    return IstanbulCoverageData(_files=files, _by_suffix=by_suffix)


def load_istanbul_coverage_files(
    paths: list[Any],
    *,
    on_error: Any = None,
) -> IstanbulCoverageData:
    """Load and merge several Istanbul reports into one view (monorepos commonly emit one
    `coverage-final.json` per package). Istanbul keys are absolute paths, so a prefix map
    is unnecessary — distinct packages contribute disjoint keys, and a later report wins on
    the rare duplicate key. Each path that is missing or unreadable is reported via
    `on_error(path, message)` (when given) and skipped, rather than failing the whole listing.
    """
    files: dict[str, dict[str, Any]] = {}
    by_suffix: dict[str, list[str]] = {}
    for path in paths:
        try:
            shard = load_istanbul_coverage(path)
        except FileNotFoundError:
            if on_error is not None:
                on_error(path, "file not found")
            continue
        except ValueError as exc:
            if on_error is not None:
                on_error(path, str(exc))
            continue
        for key, payload in shard._files.items():
            if key not in files:
                by_suffix.setdefault(_basename(key), []).append(key)
            files[key] = payload
    return IstanbulCoverageData(_files=files, _by_suffix=by_suffix)


def empty_istanbul_coverage() -> IstanbulCoverageData:
    return IstanbulCoverageData(_files={}, _by_suffix={})


def load_lcov_coverage(path: Any) -> IstanbulCoverageData:
    """Load an LCOV `lcov.info` from disk, normalized into the same Istanbul-shaped view.

    Each `SF:` record is turned into a synthetic per-file dict (`statementMap`/`s` from `DA:`,
    `branchMap`/`b` from `BRDA:`) so the existing mapping functions consume it unchanged. Raises
    FileNotFoundError if missing, ValueError on unreadable content or a report with no `SF:`
    records (mirrors `load_istanbul_coverage`)."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise ValueError(f"could not read LCOV coverage file {path}: {exc}") from exc
    return _lcov_from_text(text, path)


def _lcov_from_text(text: str, path: Any) -> IstanbulCoverageData:
    """Parse already-read LCOV text into a view (shared by the loader and the sniffing dispatcher,
    so a content-sniffed file is not read twice)."""
    files, by_suffix, saw_sf = _parse_lcov(text)
    if not files:
        if saw_sf:
            # There was an SF: record but nothing survived — e.g. every DA: line was corrupt. A
            # falsely-empty entry would read as "100% covered"; refuse it instead of lying.
            raise ValueError(f"LCOV coverage file {path} has SF: records but no readable coverage data")
        raise ValueError(f"LCOV coverage file {path} has no SF: records")
    return IstanbulCoverageData(_files=files, _by_suffix=by_suffix)


def load_ts_coverage_files(
    paths: list[Any],
    *,
    on_error: Any = None,
) -> IstanbulCoverageData:
    """Load and merge several TS coverage reports, auto-detecting Istanbul JSON vs LCOV per file.

    A `--ts-coverage` list may freely mix `coverage-final.json` and `lcov.info` shards (common in
    a polyglot monorepo). Detection is by extension (`.info`/`.lcov` → LCOV, `.json` → Istanbul)
    and, for anything else, by content (a leading `TN:`/`SF:` line → LCOV, otherwise Istanbul
    JSON). Keys are absolute source paths, so shards contribute disjoint entries and a later report
    wins on the rare duplicate. Each path that is missing or unreadable is reported via
    `on_error(path, message)` (when given) and skipped, rather than failing the whole listing."""
    files: dict[str, dict[str, Any]] = {}
    by_suffix: dict[str, list[str]] = {}
    for path in paths:
        try:
            shard = _load_one_ts_coverage(path)
        except FileNotFoundError:
            if on_error is not None:
                on_error(path, "file not found")
            continue
        except ValueError as exc:
            if on_error is not None:
                on_error(path, str(exc))
            continue
        for key, payload in shard._files.items():
            if key not in files:
                by_suffix.setdefault(_basename(key), []).append(key)
            files[key] = payload
    return IstanbulCoverageData(_files=files, _by_suffix=by_suffix)


def _load_one_ts_coverage(path: Any) -> IstanbulCoverageData:
    """Dispatch one report to the Istanbul or LCOV loader based on extension, then content."""
    by_suffix = _is_lcov_by_suffix(path)
    if by_suffix is True:
        return load_lcov_coverage(path)
    if by_suffix is False:
        # Extension says JSON. If the Istanbul loader rejects it and the bytes look like LCOV, the
        # user almost certainly saved an LCOV report under a `.json` name — say so, don't just
        # surface the opaque "not a JSON object" error.
        try:
            return load_istanbul_coverage(path)
        except ValueError as exc:
            if _path_looks_like_lcov_content(path):
                raise ValueError(
                    f"{path} looks like an LCOV report but has a .json extension; "
                    "rename it to .info/.lcov (or pass it as-is under a neutral extension)"
                ) from exc
            raise
    # Unknown extension: sniff the content, then reuse the text so the file is read only once.
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise ValueError(f"could not read coverage file {path}: {exc}") from exc
    if _text_is_lcov(text):
        return _lcov_from_text(text, path)
    return load_istanbul_coverage(path)


def _path_looks_like_lcov_content(path: Any) -> bool:
    """Best-effort re-read + sniff, only used to sharpen an error message; never raises."""
    try:
        return _text_is_lcov(path.read_text(encoding="utf-8"))
    except OSError:
        return False


_LCOV_SUFFIXES = (".info", ".lcov")
_LCOV_LINE_PREFIXES = ("SF:", "TN:", "VER:")


def _is_lcov_by_suffix(path: Any) -> bool | None:
    """True if the path is clearly LCOV by extension, False if clearly JSON, None if unknown."""
    name = (getattr(path, "name", None) or str(path)).lower()
    if name.endswith(_LCOV_SUFFIXES):
        return True
    if name.endswith(".json"):
        return False
    return None


def _text_is_lcov(text: str) -> bool:
    """True if the first non-blank line looks like an LCOV record (vs a leading `{` for JSON)."""
    for line in text.lstrip("\ufeff").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        return stripped.startswith(_LCOV_LINE_PREFIXES)
    return False


def _parse_lcov(text: str) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]], bool]:
    """Parse LCOV text into `(files, by_suffix, saw_sf)` of synthetic Istanbul-shaped per-file dicts.

    Records run `SF:<path>` … `end_of_record`. `DA:<line>,<hits>` become one-line statements;
    `BRDA:<line>,<block>,<branch>,<taken>` are grouped by `(line, block)` into synthetic branches
    (a `taken` of `-` or `0` is an uncovered arm). Malformed individual data lines are skipped
    (graceful degradation, as with malformed Istanbul substructures); a record with no trailing
    `end_of_record` is still flushed at EOF.

    A record whose `DA:` lines were *all* unparseable is dropped entirely (`da_seen > 0` but nothing
    parsed): a falsely-empty entry would be read downstream as "100% covered", so a corrupt record
    must not masquerade as a clean one. A record with genuinely no `DA:` lines is a valid
    nothing-to-measure entry and is kept. `saw_sf` reports whether any `SF:` was present at all, so
    the caller can tell "not LCOV / empty" apart from "LCOV but corrupt". A leading UTF-8 BOM is
    stripped so a BOM-prefixed report still parses."""
    files: dict[str, dict[str, Any]] = {}
    by_suffix: dict[str, list[str]] = {}
    saw_sf = False

    current_path: str | None = None
    da: list[tuple[int, int]] = []
    brda: list[tuple[int, str, int]] = []
    da_seen = 0  # `DA:` lines encountered (parsed or not), to detect an all-corrupt record

    def _flush() -> None:
        nonlocal current_path, da, brda, da_seen
        # Drop a record whose DA lines were all corrupt; keep a genuinely DA-less (empty) record.
        if current_path is not None and not (da_seen > 0 and not da):
            normalized = current_path.replace("\\", "/")
            if normalized not in files:
                by_suffix.setdefault(_basename(normalized), []).append(normalized)
            files[normalized] = _lcov_record_to_istanbul(da, brda)
        current_path, da, brda, da_seen = None, [], [], 0

    for raw_line in text.lstrip("\ufeff").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("SF:"):
            _flush()
            current_path = line[3:].strip()
            saw_sf = True
        elif line == "end_of_record":
            _flush()
        elif line.startswith("DA:") and current_path is not None:
            da_seen += 1
            da_record = _parse_lcov_da(line[3:])
            if da_record is not None:
                da.append(da_record)
        elif line.startswith("BRDA:") and current_path is not None:
            brda_record = _parse_lcov_brda(line[5:])
            if brda_record is not None:
                brda.append(brda_record)
    _flush()
    return files, by_suffix, saw_sf


def _lcov_record_to_istanbul(
    da: list[tuple[int, int]],
    brda: list[tuple[int, str, int]],
) -> dict[str, Any]:
    """Build one synthetic Istanbul-shaped file dict from parsed `DA`/`BRDA` records."""
    statement_map: dict[str, Any] = {}
    hits: dict[str, Any] = {}
    for index, (line, count) in enumerate(da):
        sid = str(index)
        statement_map[sid] = {"start": {"line": line}}
        hits[sid] = count

    # Group branch arms by (line, block), preserving first-seen order, into positional arm lists.
    # Arm order follows encounter order (well-formed LCOV emits branches in order); the positional
    # index becomes the `arm_index` in `missing_branch_arms`, exactly as Istanbul's `b[id]` array
    # is positional. Two distinct blocks on one line stay separate branch points (correct
    # denominator) but can yield the same `(line, arm_index)` pair — an informational-only overlap
    # that matches Istanbul's own per-branch indexing, not a coverage-count error.
    groups: dict[tuple[int, str], list[int]] = {}
    order: list[tuple[int, str]] = []
    for line, block, taken in brda:
        key = (line, block)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(taken)

    branch_map: dict[str, Any] = {}
    b: dict[str, Any] = {}
    for index, key in enumerate(order):
        bid = str(index)
        branch_map[bid] = {"loc": {"start": {"line": key[0]}}}
        b[bid] = list(groups[key])

    return {"statementMap": statement_map, "s": hits, "branchMap": branch_map, "b": b}


def _parse_lcov_da(fields: str) -> tuple[int, int] | None:
    """`DA:<line>,<hits>[,<checksum>]` → `(line, hits)`; None if the numbers are malformed."""
    parts = fields.split(",")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def _parse_lcov_brda(fields: str) -> tuple[int, str, int] | None:
    """`BRDA:<line>,<block>,<branch>,<taken>` → `(line, block, taken)`; `taken` of `-` → 0.
    None if the line/taken numbers are malformed. `branch` is dropped (arm order carries it)."""
    parts = fields.split(",")
    if len(parts) < 4:
        return None
    try:
        line = int(parts[0])
    except ValueError:
        return None
    block = parts[1]
    taken_raw = parts[3]
    if taken_raw == "-":
        taken = 0
    else:
        try:
            taken = int(taken_raw)
        except ValueError:
            return None
    return line, block, taken


def spans_cover_any_statement(
    file_coverage: dict[str, Any],
    spans: list[FunctionSpan],
) -> bool:
    """True if at least one Istanbul statement starts inside one of `spans`.

    A file whose statements land in *no* discovered span — yet has statements and discovered
    functions — is a strong misalignment signal: the coverage line numbers almost certainly
    describe compiled JS rather than the `.ts` source. Callers use this to warn and omit
    coverage for the file instead of emitting wrong numbers.
    """
    starts = _statement_start_lines(file_coverage)
    if not starts:
        return True  # nothing to align; not a misalignment, just an empty/no-statement file
    return any(span.start_line <= line <= span.end_line for line in starts for span in spans)


def _statement_start_lines(file_coverage: dict[str, Any]) -> list[int]:
    statement_map = file_coverage.get("statementMap")
    if not isinstance(statement_map, dict):
        return []
    lines = [_start_line(rng) for rng in statement_map.values()]
    return [line for line in lines if line is not None]


def coverage_for_ts_span(
    file_coverage: dict[str, Any] | None,
    span: FunctionSpan,
    *,
    missing_policy: MissingCoveragePolicy = MissingCoveragePolicy.PESSIMISTIC,
) -> CoverageStats:
    """Compute line/branch coverage for the lines inside `span` from one file's Istanbul data.

    Line coverage keys on each statement's `start.line` only (not its end line), collapsing
    statements that share a line with `max` hit count — exactly what
    `istanbul-lib-coverage.getLineCoverage` does. A file missing from the report follows
    `missing_policy` (PESSIMISTIC → 0%; OPTIMISTIC / SKIP → not penalized). A file present
    but with no measurable statements in the span is treated as fully covered: there is
    nothing for tests to exercise.

    `missing_policy` mirrors the Python `coverage.coverage_for_span` (a scoring pipeline needs
    absent-file = 0%). The informational CLI listing does not rely on it — it decides whether
    to show a coverage tag from the presence of the file entry directly, so an unmeasured file
    is reported explicitly rather than as a fabricated 0% or 100%.
    """
    if file_coverage is None:
        if missing_policy is MissingCoveragePolicy.PESSIMISTIC:
            return CoverageStats.uncovered()
        return CoverageStats(line_coverage=1.0, branch_coverage=None)

    line_coverage, missing_lines = _line_stats(file_coverage, span)
    if line_coverage is None:
        return CoverageStats(line_coverage=1.0, branch_coverage=None)

    branch_coverage, missing_branch_arms = _branch_stats(file_coverage, span)
    return CoverageStats(
        line_coverage=line_coverage,
        branch_coverage=branch_coverage,
        missing_lines=missing_lines,
        # Istanbul has no src→dst arc; arms go in the TS-specific field, never `missing_branches`.
        missing_branch_arms=missing_branch_arms,
    )


def _line_stats(
    file_coverage: dict[str, Any],
    span: FunctionSpan,
) -> tuple[float | None, tuple[int, ...]]:
    """Per-line executed/missing from `statementMap` + `s`. Returns (None, ()) when no
    statement starts inside the span (nothing measurable)."""
    statement_map = file_coverage.get("statementMap")
    hits = file_coverage.get("s")
    if not isinstance(statement_map, dict) or not isinstance(hits, dict):
        return None, ()

    line_hits: dict[int, int] = {}
    for sid, rng in statement_map.items():
        line = _start_line(rng)
        if line is None or not (span.start_line <= line <= span.end_line):
            continue
        count = _as_int(hits.get(sid))
        line_hits[line] = max(line_hits.get(line, 0), count)

    if not line_hits:
        return None, ()

    executed = {line for line, count in line_hits.items() if count > 0}
    missing = tuple(sorted(line for line in line_hits if line not in executed))
    return len(executed) / len(line_hits), missing


def _branch_stats(
    file_coverage: dict[str, Any],
    span: FunctionSpan,
) -> tuple[float | None, tuple[tuple[int, int], ...]]:
    """Branch coverage for branches whose `loc.start.line` falls in the span. `b[id]` is an
    array of arm hit counts positionally aligned to `branchMap[id].locations`; an arm with
    count > 0 is executed. All branch `type`s (`if`/`switch`/`binary-expr`/`cond-expr`/
    `default-arg`) count toward the denominator, faithful to raw nyc totals. Returns
    `(coverage, missing_arms)` where each missing arm is `(branch_loc_start_line, arm_index)`,
    or `(None, ())` when no branch is measured in the span."""
    branch_map = file_coverage.get("branchMap")
    hits = file_coverage.get("b")
    if not isinstance(branch_map, dict) or not isinstance(hits, dict):
        return None, ()

    total = 0
    covered = 0
    missing: list[tuple[int, int]] = []
    for bid, branch in branch_map.items():
        if not isinstance(branch, dict):
            continue
        line = _start_line(branch.get("loc"))
        if line is None or not (span.start_line <= line <= span.end_line):
            continue
        arm_counts = hits.get(bid)
        if not isinstance(arm_counts, list):
            continue
        for index, raw in enumerate(arm_counts):
            total += 1
            if _as_int(raw) > 0:
                covered += 1
            else:
                missing.append((line, index))

    if total == 0:
        return None, ()
    return covered / total, tuple(missing)


def _start_line(rng: Any) -> int | None:
    if not isinstance(rng, dict):
        return None
    start = rng.get("start")
    if not isinstance(start, dict):
        return None
    line = start.get("line")
    return int(line) if isinstance(line, int) else None


def _as_int(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


def _basename(posix_path: str) -> str:
    return posix_path.rsplit("/", 1)[-1]
