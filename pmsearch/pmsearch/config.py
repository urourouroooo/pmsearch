from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent


def _resolve_data_dir() -> Path:
    """
    Prefer project-local ``data/`` when you run from the repo (cwd), so ``data/runs``
    land next to ``keywords/`` / optional ``keywords.json`` — not under site-packages if installed.
    Override with env ``PMSEARCH_DATA_DIR`` (legacy: ``PUBMED_BATCH_DATA_DIR``).
    """
    env = (
        os.environ.get("PMSEARCH_DATA_DIR")
        or os.environ.get("PUBMED_BATCH_DATA_DIR")
        or ""
    ).strip()
    if env:
        return Path(env).expanduser().resolve()
    cwd_data = Path.cwd() / "data"
    if cwd_data.is_dir() and (
        (cwd_data / "keywords.json").is_file()
        or (cwd_data / "keywords").is_dir()
    ):
        return cwd_data.resolve()
    return (PROJECT_ROOT / "data").resolve()


DATA_DIR = _resolve_data_dir()
KEYWORDS_PATH = DATA_DIR / "keywords.json"
# Keyword sets: ``data/keywords/kw_N.md`` or ``kw_N_suffix.md`` (suffix = user comment)
KEYWORD_SETS_DIR = DATA_DIR / "keywords"

# Optional: ``data/runs_root.md`` — set where timestamped run folders are created (see ``parse_runs_root_md``).
RUNS_ROOT_MD_PATH = DATA_DIR / "runs_root.md"

_RUNS_MD_KV = re.compile(
    r"^(?:runs[_\s-]?root|output\s*dir|output|path)\s*[:=]\s*(.+?)\s*$",
    re.IGNORECASE,
)


def parse_runs_root_md(file_path: Path) -> Path | None:
    """
    Read ``runs_root.md`` under ``data/`` (or whatever ``DATA_DIR`` is).

    * A line like ``runs_root: ~/Desktop/pmsearch_run`` (also ``output:``, ``path:``).
    * Or a **bare path** on its own line (``~``, ``/``, ``.``, or Windows ``X:\\``); ``#`` lines are skipped.
    """
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        m = _RUNS_MD_KV.match(s)
        if m:
            raw = m.group(1).strip().strip('"').strip("'")
            if raw:
                return Path(raw).expanduser().resolve()
            continue
        if s.startswith("#"):
            continue
        if s.startswith(("~/", "~", "/", ".")):
            return Path(s).expanduser().resolve()
        if len(s) > 2 and s[1] == ":" and s[0].isalpha():
            return Path(s).expanduser().resolve()
    return None


def resolve_runs_base(cli_runs_dir: str | None = None) -> Path:
    """
    Root directory for ``<timestamp>_dayN_kw_…/`` run folders.

    Precedence: non-empty ``cli_runs_dir`` → env ``PMSEARCH_RUNS_DIR`` →
    ``parse_runs_root_md(RUNS_ROOT_MD_PATH)`` → ``DATA_DIR / "runs"``.
    """
    cli = (cli_runs_dir or "").strip()
    if cli:
        return Path(cli).expanduser().resolve()
    env = (os.environ.get("PMSEARCH_RUNS_DIR") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if RUNS_ROOT_MD_PATH.is_file():
        p = parse_runs_root_md(RUNS_ROOT_MD_PATH)
        if p is not None:
            return p
    return (DATA_DIR / "runs").resolve()


_DEFAULT_KEYWORDS_CFG: dict[str, object] = {
    "keywords": [],
    "entrez_email": "",
    "tool_name": "pmsearch_tool",
    "ncbi_api_key": "",
}


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_keywords_config() -> dict:
    """
    Load ``data/keywords.json`` if present. If missing, return defaults **in memory only**
    (no file is written) so ``run -kw N`` users are not forced to have a template JSON.
    """
    ensure_data_dir()
    if not KEYWORDS_PATH.exists():
        return dict(_DEFAULT_KEYWORDS_CFG)
    with open(KEYWORDS_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        return dict(_DEFAULT_KEYWORDS_CFG)
    merged = dict(_DEFAULT_KEYWORDS_CFG)
    merged.update(raw)
    return merged


def save_keywords_config(cfg: dict) -> None:
    ensure_data_dir()
    with open(KEYWORDS_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _first_nonempty_env(*names: str) -> str:
    for n in names:
        v = (os.environ.get(n) or "").strip()
        if v:
            return v
    return ""


def resolve_entrez_email(cfg: dict) -> str:
    """
    NCBI requires a contact email for E-utilities. Use ``entrez_email`` in
    ``keywords.json`` if set; otherwise ``ENTREZ_EMAIL`` or ``NCBI_EMAIL``.
    """
    e = (cfg.get("entrez_email") or "").strip()
    if e:
        return e
    return _first_nonempty_env("ENTREZ_EMAIL", "NCBI_EMAIL")


def resolve_ncbi_api_key(cfg: dict) -> str | None:
    """Optional API key: ``keywords.json`` ``ncbi_api_key`` or ``NCBI_API_KEY`` / ``ENTREZ_API_KEY``."""
    v = (cfg.get("ncbi_api_key") or "").strip()
    if v:
        return v
    return _first_nonempty_env("NCBI_API_KEY", "ENTREZ_API_KEY") or None


def resolve_tool_name(cfg: dict) -> str:
    """Identifies your tool to NCBI: ``tool_name`` in JSON or ``PMSEARCH_TOOL_NAME``."""
    v = (cfg.get("tool_name") or "").strip()
    if v:
        return v
    return _first_nonempty_env("PMSEARCH_TOOL_NAME") or "pmsearch_tool"


def get_keyword_list() -> list[str]:
    cfg = load_keywords_config()
    kws = cfg.get("keywords") or []
    return [str(k).strip() for k in kws if str(k).strip()]


def kw_set_md_path(n: int) -> Path:
    """Canonical ``kw_N.md`` path (for templates); actual file may be ``kw_N_*.md``."""
    return KEYWORD_SETS_DIR / f"kw_{int(n)}.md"


_KW_MD_EXACT = re.compile(r"^kw_(\d+)\.md$", re.IGNORECASE)


def _kw_md_suffix_pattern(n: int) -> re.Pattern[str]:
    """Match ``kw_N_suffix.md`` where *N* is fixed (avoids ``kw_1`` matching ``kw_10_…``)."""
    return re.compile(rf"^kw_{int(n)}_(.+)\.md$", re.IGNORECASE)


def _parse_kw_md_index(name: str) -> int | None:
    """
    Return set index *N* for ``kw_N.md`` or ``kw_N_anything.md``, else ``None``.
    """
    m = _KW_MD_EXACT.match(name)
    if m:
        return int(m.group(1))
    m = re.match(r"^kw_(\d+)_(.+)\.md$", name, re.IGNORECASE)
    if m and m.group(2).strip():
        return int(m.group(1))
    return None


def discover_kw_set_numbers() -> list[int]:
    """Sorted unique *N* for each ``kw_N.md`` or ``kw_N_*.md`` under ``KEYWORD_SETS_DIR``."""
    if not KEYWORD_SETS_DIR.is_dir():
        return []
    found: list[int] = []
    for p in KEYWORD_SETS_DIR.iterdir():
        if not p.is_file():
            continue
        idx = _parse_kw_md_index(p.name)
        if idx is not None:
            found.append(idx)
    return sorted(set(found))


def resolve_kw_set_md_path(n: int) -> Path:
    """
    Resolve the markdown file for keyword set *N*.

    * If ``kw_N.md`` exists, it wins.
    * Else exactly one ``kw_N_suffix.md`` must exist (suffix is a non-empty user comment).
    * If several ``kw_N_*.md`` exist and no ``kw_N.md``, raises ``ValueError``.
    """
    n = int(n)
    d = KEYWORD_SETS_DIR
    if not d.is_dir():
        raise FileNotFoundError(str(kw_set_md_path(n)))
    exact = (d / f"kw_{n}.md").resolve()
    if exact.is_file():
        return exact
    rx = _kw_md_suffix_pattern(n)
    matches = sorted(
        (p for p in d.iterdir() if p.is_file() and rx.match(p.name)),
        key=lambda p: p.name.lower(),
    )
    if not matches:
        raise FileNotFoundError(str(exact))
    if len(matches) > 1:
        names = ", ".join(p.name for p in matches)
        raise ValueError(
            f"Multiple keyword files for kw_{n}: {names}. "
            f"Add kw_{n}.md or remove extras so only one kw_{n}_*.md remains."
        )
    return matches[0].resolve()


@dataclass(frozen=True)
class KeywordQuerySpec:
    """How to build the string sent to PubMed ESearch ``term=``."""

    parts: list[str]
    join_between: str = "OR"  # OR | AND | NOT (between parenthesized parts; NOT is left-associative)
    freeform: bool = False  # If True, parts is a single raw query (no extra wrapping)


_JOIN_LINE = re.compile(r"^#?\s*join:\s*(OR|AND|NOT)\s*$", re.IGNORECASE)
_MODE_FREEFORM = re.compile(r"^#?\s*mode:\s*freeform\s*$", re.IGNORECASE)


def normalize_join(value: str | None) -> str:
    if not value:
        return "OR"
    u = str(value).strip().upper()
    return u if u in ("AND", "OR", "NOT") else "OR"


def parse_kw_md(text: str) -> KeywordQuerySpec:
    """
    PubMed-style queries:

    * **Lines mode (default):** each non-comment line is one clause; clauses are wrapped in
      ``(...)`` and combined with ``join: OR`` / ``AND`` / ``NOT`` (default OR). Use
      ``NOT`` for left-associative chains: ``(a) NOT (b) NOT (c)``.

    * **Freeform mode:** a line ``mode: freeform`` (``#`` optional) turns the rest of the
      file into one query: non-comment lines are joined with spaces. Passes through to
      ESearch unchanged (field tags, nested parens, etc.).

    Directives: ``join: ...``, ``mode: freeform`` — may appear at the top, ``#`` optional.
    """
    join_between = "OR"
    freeform_mode = False
    freeform_lines: list[str] = []
    out: list[str] = []

    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if _MODE_FREEFORM.match(s):
            # Only treat as freeform if no line-mode clauses collected yet (avoids
            # accidental matches from commented examples at the bottom of a file).
            if out:
                continue
            freeform_mode = True
            continue
        jm = _JOIN_LINE.match(s)
        if jm:
            join_between = jm.group(1).upper()
            continue
        if freeform_mode:
            if s.startswith("#"):
                continue
            if s.startswith("- "):
                s = s[2:].strip()
            elif s.startswith("* "):
                s = s[2:].strip()
            if s:
                freeform_lines.append(s)
            continue
        if s.startswith("#"):
            continue
        if s.startswith("- "):
            s = s[2:].strip()
        elif s.startswith("* "):
            s = s[2:].strip()
        if s and not s.startswith("#"):
            out.append(s)

    if freeform_mode:
        full = " ".join(freeform_lines).strip()
        return KeywordQuerySpec([full] if full else [], "OR", freeform=True)

    return KeywordQuerySpec(out, join_between, freeform=False)


def load_keyword_query_from_config(cfg: dict) -> KeywordQuerySpec:
    """``keywords.json``: optional ``pubmed_query`` (full string) overrides ``keywords`` list."""
    pq = (cfg.get("pubmed_query") or "").strip()
    if pq:
        return KeywordQuerySpec([pq], "OR", freeform=True)
    kws = [str(k).strip() for k in (cfg.get("keywords") or []) if str(k).strip()]
    return KeywordQuerySpec(kws, normalize_join(cfg.get("keywords_join")), freeform=False)


def load_keywords_from_kw_set(n: int) -> KeywordQuerySpec:
    path = resolve_kw_set_md_path(n)
    text = path.read_text(encoding="utf-8")
    return parse_kw_md(text)
