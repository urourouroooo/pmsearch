from __future__ import annotations

import argparse
import copy
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

try:
    from tqdm import tqdm as _tqdm_base

    class _TranslateTqdm(_tqdm_base):
        """Progress bar without tqdm's default ``, `` before postfix (``| █ …|`` not ``|, █ …|``)."""

        def format_meter(self, n, total, elapsed, **kwargs):  # type: ignore[override]
            s = super().format_meter(n, total, elapsed, **kwargs)
            return s.replace("|, ", "| ")

except ImportError:  # pragma: no cover
    class _TqdmStub:
        """Minimal stand-in when tqdm is not installed (manual ``total`` + ``update``)."""

        def __init__(self, iterable=None, **_kwargs):
            self._iterable = iterable

        def __iter__(self):
            if self._iterable is None:
                raise TypeError("tqdm stub requires iterable")
            return iter(self._iterable)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def update(self, n=1):
            pass

        def set_postfix_str(self, s: str = "", *, refresh: bool = True) -> None:
            pass

        def set_description_str(self, s: str = "", *, refresh: bool = True) -> None:
            pass

        def close(self) -> None:
            pass

    _TranslateTqdm = _TqdmStub  # type: ignore[misc, assignment]

from .config import (
    DATA_DIR,
    KEYWORD_SETS_DIR,
    RUNS_ROOT_MD_PATH,
    discover_kw_set_numbers,
    get_keyword_list,
    load_keyword_query_from_config,
    load_keywords_config,
    load_keywords_from_kw_set,
    resolve_entrez_email,
    resolve_kw_set_md_path,
    resolve_ncbi_api_key,
    resolve_runs_base,
    resolve_tool_name,
    save_keywords_config,
)
from .excel_export import build_run_summary_lines, export_rows
from .pubmed_client import (
    build_search_term,
    date_window_today,
    fetch_all_keyword_lists,
    fetch_all_pubmed_records,
    parse_pdat_range,
    search_pmids,
)
from .translate_zh import translate_to_chinese

# argparse ``-kw`` with no value: run every ``data/keywords/kw_*.md``
KW_ALL_SETS = "all"


def _kw_section_label(n: int) -> str:
    """Banner label: resolved ``kw_N*.md`` filename, or ``kw_N`` if not yet resolvable."""
    try:
        return resolve_kw_set_md_path(int(n)).name
    except (FileNotFoundError, ValueError, OSError):
        return f"kw_{int(n)}"

RUN_BANNER_START = "================ START RETRIEVING ================"
RUN_BANNER_END = "================  WORK COMPLETE  ================"

# Retrieve status line: [RETRIEVING] (yellow) → [COMPLETE] (rose-red, hue nudged toward red).
_T_RESET = "\033[0m"
_T_BOLD = "\033[1m"
_T_RETRIEVE_YELLOW = "\033[38;2;255;210;70m"
_T_RETRIEVE_COMPLETE_ROSE = "\033[38;2;255;72;105m"


def _retrieve_tag_retrieving() -> str:
    return f"{_T_BOLD}{_T_RETRIEVE_YELLOW}[RETRIEVING]{_T_RESET}"


def _retrieve_tag_complete() -> str:
    return f"{_T_BOLD}{_T_RETRIEVE_COMPLETE_ROSE}[COMPLETE]{_T_RESET}"


def _run_out(msg: str, *, flush: bool = False) -> None:
    """Print one logical line, then a blank line (readable run output when not ``--quiet``)."""
    print(msg, flush=flush)
    print(flush=flush)


def _segmented_bar_str(n_done: int, total: int, nseg: int = 10) -> str:
    """
    Discrete bar: ``nseg`` blocks with gaps; roughly one block per 100/nseg %% completed.
    Uses full/light block glyphs (not a single growing solid bar).
    """
    if total <= 0:
        return ""
    n_done = max(0, min(int(n_done), int(total)))
    filled = (n_done * nseg) // total
    if n_done >= total:
        filled = nseg
    parts = ["█" if i < filled else "░" for i in range(nseg)]
    return " ".join(parts)


class _SegmentedTranslateTqdm(_TranslateTqdm):
    """Same as ``_TranslateTqdm`` but keeps segmented ``postfix`` in sync on each ``update``."""

    def __enter__(self):
        super().__enter__()
        self.set_postfix_str(_segmented_bar_str(0, self.total))
        return self

    def update(self, n=1):  # type: ignore[override]
        rv = super().update(n)
        self.set_postfix_str(_segmented_bar_str(self.n, self.total))
        return rv


def _normalize_parsed_kw(args: argparse.Namespace) -> int:
    """
    Coerce ``-kw 5`` string to int. ``-kw`` alone stays ``KW_ALL_SETS``; absent stays None.
    Returns 0 or exit code 2 on invalid N.
    """
    if not hasattr(args, "kw"):
        return 0
    k = getattr(args, "kw", None)
    if k is None or k == KW_ALL_SETS:
        return 0
    if isinstance(k, int):
        if k < 1:
            print("pmsearch: -kw N requires N >= 1", file=sys.stderr)
            return 2
        return 0
    if isinstance(k, str):
        s = k.strip()
        if not s.isdigit():
            print(
                "pmsearch: -kw must be a positive integer, or use -kw alone for all kw_N*.md",
                file=sys.stderr,
            )
            return 2
        n = int(s)
        if n < 1:
            print("pmsearch: -kw N requires N >= 1", file=sys.stderr)
            return 2
        args.kw = n
        return 0
    print("pmsearch: invalid -kw value", file=sys.stderr)
    return 2


def _prepare_pubmed_search(
    args: argparse.Namespace,
    cfg: dict,
) -> dict[str, Any] | None:
    """Build query + dates for ESearch. Returns None if configuration is invalid."""
    vb = getattr(args, "verbose", False) and not getattr(args, "quiet", False)
    email = resolve_entrez_email(cfg)
    if not email:
        print(
            "NCBI requires a contact email: set entrez_email in data/keywords.json, "
            "or set environment variable ENTREZ_EMAIL (see NCBI E-utilities guidelines).",
            file=sys.stderr,
        )
        return None
    kw_arg = getattr(args, "kw", None)
    use_json = getattr(args, "use_keywords_json", False)
    if kw_arg is not None and use_json:
        print("Do not use -kw together with --use-keywords-json.", file=sys.stderr)
        return None
    if kw_arg is None and not use_json:
        print(
            "Required: -kw N, -kw alone (all kw_N.md / kw_N_*.md), or --use-keywords-json.",
            file=sys.stderr,
        )
        return None
    if kw_arg is not None:
        if not isinstance(kw_arg, int):
            print(
                "-kw/--kw expects a positive integer when a number is given "
                "(e.g. 1 for data/keywords/kw_1.md or kw_1_….md).",
                file=sys.stderr,
            )
            return None
        if kw_arg < 1:
            print(
                "-kw/--kw expects a positive integer (e.g. 1 for kw_1.md or kw_1_….md).",
                file=sys.stderr,
            )
            return None
        try:
            qspec = load_keywords_from_kw_set(kw_arg)
        except FileNotFoundError:
            print(
                f"Keyword set not found for kw_{kw_arg}: need kw_{kw_arg}.md or exactly one "
                f"kw_{kw_arg}_*.md under {KEYWORD_SETS_DIR}",
                file=sys.stderr,
            )
            return None
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return None
        if vb:
            print(f"Keyword set file: {resolve_kw_set_md_path(kw_arg)}")
    else:
        qspec = load_keyword_query_from_config(cfg)

    def _spec_empty() -> bool:
        if not qspec.parts:
            return True
        if qspec.freeform:
            return not (qspec.parts[0] or "").strip()
        return not any((p or "").strip() for p in qspec.parts)

    if _spec_empty():
        print(
            "No query configured. Check data/keywords/kw_N.md (or kw_N_*.md) or keywords.json "
            "(with --use-keywords-json), or run: keywords add ...",
            file=sys.stderr,
        )
        return None
    try:
        term = build_search_term(
            qspec.parts,
            join_between=qspec.join_between,
            freeform=qspec.freeform,
        )
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return None
    pr = (getattr(args, "pdat_range", None) or "").strip()
    if pr:
        try:
            mindate, maxdate, days = parse_pdat_range(pr)
        except ValueError as e:
            print(f"pmsearch: invalid --range: {e}", file=sys.stderr)
            return None
    else:
        days = args.days
        mindate, maxdate = date_window_today(days=days)
    api_key = resolve_ncbi_api_key(cfg)
    tool = resolve_tool_name(cfg)

    if vb:
        if qspec.freeform:
            print("Query mode: freeform (raw PubMed query string)")
        else:
            print(f"Between-line join: {qspec.join_between}")
        print(f"Query: {term}")
        if pr:
            print(
                f"Publication date (PDAT): {mindate} — {maxdate} "
                f"(fixed range, {days} calendar day(s) inclusive)"
            )
        else:
            print(
                f"Publication date (PDAT): {mindate} — {maxdate} "
                f"({days} day(s) ending today, inclusive)"
            )

    return {
        "qspec": qspec,
        "term": term,
        "mindate": mindate,
        "maxdate": maxdate,
        "days": days,
        "pdat_fixed_range": bool(pr),
        "email": email,
        "api_key": api_key,
        "tool": tool,
    }


def _new_run_dir(prep: dict[str, Any], args: argparse.Namespace) -> Path:
    """Create ``<runs_base>/<timestamp>_dayN_kw_…/`` (see ``resolve_runs_base``)."""
    run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    use_json = getattr(args, "use_keywords_json", False)
    kw_arg = getattr(args, "kw", None)
    tag = "keywords_json" if use_json else f"kw_{kw_arg}"
    base = resolve_runs_base(getattr(args, "runs_dir", None))
    run_dir = base / f"{run_ts}_day{prep['days']}_{tag}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _write_run_record_md(
    run_dir: Path,
    *,
    kind: str,
    prep: dict[str, Any],
    term: str,
    export_paths: list[tuple[str, str]],
) -> None:
    """Write ``run_record.md`` in the run folder (paths + query + PDAT)."""
    mindate = prep["mindate"]
    maxdate = prep["maxdate"]
    days = prep["days"]
    q_safe = term.strip() or "(empty)"
    if "```" in q_safe:
        q_safe = q_safe.replace("```", "``\\`")
    lines = [
        "# pmsearch run record",
        "",
        f"- **Kind**: `{kind}`",
        f"- **Run folder**: `{run_dir.resolve()}`",
        f"- **Runs root (parent)**: `{run_dir.resolve().parent}` "
        f"(override: `--runs-dir`, env `PMSEARCH_RUNS_DIR`, or `{RUNS_ROOT_MD_PATH}`)",
        f"- **Local time**: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`",
        f"- **PDAT window**: `{mindate}` — `{maxdate}` ({days} calendar day(s) inclusive; "
        f"{'fixed `--range`' if prep.get('pdat_fixed_range') else 'sliding `--days` ending today'})",
        "",
        "## PubMed query",
        "",
        "```",
        q_safe,
        "```",
        "",
        "## Output files",
        "",
    ]
    for label, p in export_paths:
        lines.append(f"- **{label}**: `{p}`")
    lines.append("")
    (run_dir / "run_record.md").write_text("\n".join(lines), encoding="utf-8")


def _aggregate_kwcorre_from_triples(
    triples: list[tuple[str, list[str], list[str]]],
    sources: str,
) -> tuple[pd.DataFrame, list[dict[str, Any]], Counter[str], Counter[str]]:
    """
    Keyword occurrence counts plus comma-separated article indices (1,2,3,…) in fetch order.

    ``triples``: one entry per article in retrieval order; article ``1`` is the first PMID
    in the list passed to ``fetch_all_keyword_lists``.
    """
    author_c: Counter[str] = Counter()
    mesh_c: Counter[str] = Counter()
    author_article_nums: dict[str, set[int]] = {}
    mesh_article_nums: dict[str, set[int]] = {}

    for idx, (_pmid, ak, mk) in enumerate(triples):
        article_num = idx + 1
        for k in ak:
            k2 = k.strip()
            if not k2:
                continue
            author_c[k2] += 1
            author_article_nums.setdefault(k2, set()).add(article_num)
        for k in mk:
            k2 = k.strip()
            if not k2:
                continue
            mesh_c[k2] += 1
            mesh_article_nums.setdefault(k2, set()).add(article_num)

    rows: list[dict[str, Any]] = []
    if sources in ("all", "author"):
        for k, c in author_c.most_common():
            nums = author_article_nums.get(k, set())
            article_numbers = ",".join(str(x) for x in sorted(nums))
            rows.append(
                {
                    "keyword": k,
                    "article_count": c,
                    "source": "author_keyword",
                    "article_numbers": article_numbers,
                }
            )
    if sources in ("all", "mesh"):
        for k, c in mesh_c.most_common():
            nums = mesh_article_nums.get(k, set())
            article_numbers = ",".join(str(x) for x in sorted(nums))
            rows.append(
                {
                    "keyword": k,
                    "article_count": c,
                    "source": "mesh",
                    "article_numbers": article_numbers,
                }
            )

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(
            ["article_count", "source", "keyword"],
            ascending=[False, True, True],
        )

    return df, rows, author_c, mesh_c


def _cmd_try_once(args: argparse.Namespace, cfg: dict) -> int:
    """ESearch only: show how many articles match (no efetch, no Excel)."""
    prep = _prepare_pubmed_search(args, cfg)
    if prep is None:
        return 2
    pmids = search_pmids(
        prep["term"],
        prep["mindate"],
        prep["maxdate"],
        email=prep["email"],
        tool=prep["tool"],
        api_key=prep["api_key"],
    )
    n = len(pmids)
    if getattr(args, "quiet", False):
        print(n)
        return 0
    kw_n = getattr(args, "kw", None)
    label = f" (kw_{kw_n})" if isinstance(kw_n, int) else ""
    if prep.get("pdat_fixed_range"):
        span = (
            f"PDAT {prep['mindate']}—{prep['maxdate']} "
            f"({prep['days']} calendar day(s) inclusive)"
        )
    else:
        span = f"the last {prep['days']} day(s) ending today (PDAT)"
    print(f"Try: {n} PubMed hit(s) in {span}. No download.{label}")
    return 0


def cmd_try(args: argparse.Namespace) -> int:
    cfg = load_keywords_config()
    if getattr(args, "kw", None) == KW_ALL_SETS:
        if args.use_keywords_json:
            print("Do not use -kw together with --use-keywords-json.", file=sys.stderr)
            return 2
        nums = discover_kw_set_numbers()
        if not nums:
            print(
                "No data/keywords/kw_N.md or kw_N_*.md files found.",
                file=sys.stderr,
            )
            return 2
        if not args.quiet:
            print(
                f"Try: {len(nums)} keyword set(s): {', '.join(f'kw_{n}' for n in nums)}",
                flush=True,
            )
        rc = 0
        for n in nums:
            sub = copy.copy(args)
            sub.kw = n
            if not args.quiet:
                print(f"\n--- {_kw_section_label(n)} ---", flush=True)
            r = _cmd_try_once(sub, cfg)
            if r != 0:
                rc = r
        return rc
    return _cmd_try_once(args, cfg)


def _cmd_kwcorre_once(args: argparse.Namespace, cfg: dict) -> int:
    """
    Aggregate author keywords and MeSH terms from the retrieved article set,
    same query and date window as ``run``.
    """
    prep = _prepare_pubmed_search(args, cfg)
    if prep is None:
        return 2
    pmids = search_pmids(
        prep["term"],
        prep["mindate"],
        prep["maxdate"],
        email=prep["email"],
        tool=prep["tool"],
        api_key=prep["api_key"],
    )
    if not pmids:
        print("No results.")
        return 0

    n_hits = len(pmids)
    max_cap = getattr(args, "max_articles", None) or 0
    if max_cap > 0:
        pmids = pmids[:max_cap]

    if not args.quiet:
        print(
            f"kwcorre: {n_hits} PubMed hit(s); fetching metadata for {len(pmids)} article(s) "
            "to collect keywords…",
            flush=True,
        )

    triples = fetch_all_keyword_lists(
        pmids,
        email=prep["email"],
        tool=prep["tool"],
        api_key=prep["api_key"],
    )

    src = getattr(args, "sources", "all")
    df, rows, author_c, mesh_c = _aggregate_kwcorre_from_triples(triples, src)

    run_dir = _new_run_dir(prep, args)
    out_name = "kwcorre.csv"
    if getattr(args, "output", None) and str(args.output).strip():
        on = Path(args.output).name
        if on.lower().endswith(".csv"):
            out_name = on
    out_path = (run_dir / out_name).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, encoding="utf-8-sig")

    _write_run_record_md(
        run_dir,
        kind="kwcorre",
        prep=prep,
        term=prep["term"],
        export_paths=[("kwcorre.csv", str(out_path.resolve()))],
    )

    if not args.quiet:
        print(
            f"Saved: {out_path} "
            f"({len(rows)} keyword rows from {len(triples)} articles; "
            f"author_kw unique: {len(author_c)}, mesh unique: {len(mesh_c)})"
        )
        print(f"Run folder: {run_dir.resolve()}")
    return 0


def cmd_kwcorre(args: argparse.Namespace) -> int:
    cfg = load_keywords_config()
    if getattr(args, "kw", None) == KW_ALL_SETS:
        if args.use_keywords_json:
            print("Do not use -kw together with --use-keywords-json.", file=sys.stderr)
            return 2
        nums = discover_kw_set_numbers()
        if not nums:
            print(
                "No data/keywords/kw_N.md or kw_N_*.md files found.",
                file=sys.stderr,
            )
            return 2
        if not args.quiet:
            print(
                f"kwcorre: {len(nums)} keyword set(s): {', '.join(f'kw_{n}' for n in nums)}",
                flush=True,
            )
        rc = 0
        for n in nums:
            sub = copy.copy(args)
            sub.kw = n
            if not args.quiet:
                print(f"\n--- {_kw_section_label(n)} ---", flush=True)
            r = _cmd_kwcorre_once(sub, cfg)
            if r != 0:
                rc = r
        return rc
    return _cmd_kwcorre_once(args, cfg)


def _cmd_run_once(
    args: argparse.Namespace,
    cfg: dict,
    *,
    emit_run_banners: bool = True,
) -> int:
    prep = _prepare_pubmed_search(args, cfg)
    if prep is None:
        return 2
    term = prep["term"]
    mindate = prep["mindate"]
    maxdate = prep["maxdate"]
    qspec = prep["qspec"]
    email = prep["email"]
    tool = prep["tool"]
    api_key = prep["api_key"]

    pmids = search_pmids(
        term,
        mindate,
        maxdate,
        email=email,
        tool=tool,
        api_key=api_key,
    )
    search_hit_count = len(pmids)
    if not pmids:
        print("No results.")
        return 0

    max_cap = getattr(args, "max_articles", None) or 0
    if max_cap > 0:
        pmids_to_fetch = pmids[:max_cap]
    else:
        pmids_to_fetch = pmids

    fetch_id_count = len(pmids_to_fetch)

    retrieve_prefix = ""
    if not args.quiet:
        if emit_run_banners:
            _run_out(RUN_BANNER_START, flush=True)
        if max_cap == 0:
            retrieve_prefix = (
                f"PubMed search: {search_hit_count} hit(s). Retrieving {fetch_id_count}. "
            )
        elif search_hit_count <= max_cap:
            retrieve_prefix = (
                f"PubMed search: {search_hit_count} hit(s). Retrieving {fetch_id_count}. "
            )
        else:
            retrieve_prefix = (
                f"PubMed search: {search_hit_count} hit(s). Retrieving {max_cap} "
                "(--max-articles; first N in PubMed search order). "
            )
        print(
            f"{retrieve_prefix}{_retrieve_tag_retrieving()}",
            end="",
            flush=True,
        )

    rows = fetch_all_pubmed_records(
        pmids_to_fetch,
        email=email,
        tool=tool,
        api_key=api_key,
    )

    if not args.quiet:
        print(f"\r\033[K{retrieve_prefix}{_retrieve_tag_complete()}", flush=True)
        print(flush=True)
    by_pmid: dict[str, dict] = {}
    for r in rows:
        pid = str(r.get("PMID", ""))
        if not pid or pid in by_pmid:
            continue
        by_pmid[pid] = r

    unique_ordered: list[dict] = []
    for i, pmid in enumerate(pmids_to_fetch):
        if pmid not in by_pmid:
            continue
        rec = by_pmid[pmid]
        rec["Article No."] = i + 1
        unique_ordered.append(rec)

    if not args.quiet:
        n_raw = len(rows)
        n_unique = len(unique_ordered)
        if n_raw != fetch_id_count or n_unique != n_raw:
            _run_out(
                f"Metadata: parsed {n_raw} XML row(s) for {fetch_id_count} requested PMID(s); "
                f"{n_unique} unique PMID(s) after deduplication."
            )

    if args.no_translate:
        for r in unique_ordered:
            r["Abstract (Chinese)"] = ""
    else:
        n_art = len(unique_ordered)
        if n_art > 0:
            # No default ``{bar}``: show 10 discrete blocks with gaps via ``postfix``.
            bar_fmt = (
                "{desc}: {percentage:3.0f}% |{postfix}| {n_fmt}/{total_fmt} "
                "[{elapsed}<{remaining}]"
            )
            with _SegmentedTranslateTqdm(
                total=n_art,
                desc="Translating abstracts",
                unit="article",
                disable=args.quiet,
                bar_format=bar_fmt,
                miniters=1,
            ) as pbar:
                for r in unique_ordered:
                    ab = r.get("Abstract") or ""
                    r["Abstract (Chinese)"] = translate_to_chinese(ab)
                    pbar.update(1)
            if not args.quiet:
                print(flush=True)

    run_dir = _new_run_dir(prep, args)

    out_name = "pubmed.xlsx"
    if getattr(args, "output", None) and str(args.output).strip():
        on = Path(args.output).name
        if on.lower().endswith(".xlsx"):
            out_name = on

    out_path = run_dir / out_name

    use_json = getattr(args, "use_keywords_json", False)
    kw_arg = getattr(args, "kw", None)
    kw_md_basename: str | None = None
    if not use_json and isinstance(kw_arg, int):
        kw_md_basename = resolve_kw_set_md_path(kw_arg).name
    summary = build_run_summary_lines(
        mindate=mindate,
        maxdate=maxdate,
        days=prep["days"],
        term=term,
        qspec=qspec,
        kw_num=kw_arg,
        use_keywords_json=use_json,
        kw_md_basename=kw_md_basename,
    )
    out = export_rows(unique_ordered, out_path, run_summary=summary)
    if not args.quiet:
        _run_out(f"Saved: {out}")

    kwcorre_saved: str | None = None
    if getattr(args, "kwcorre", False):
        if not args.quiet:
            _run_out(
                f"kwcorre: fetching keyword lists for {len(pmids_to_fetch)} article(s)…",
                flush=True,
            )
        kw_triples = fetch_all_keyword_lists(
            pmids_to_fetch,
            email=email,
            tool=tool,
            api_key=api_key,
        )
        kw_src = getattr(args, "kwcorre_sources", "all")
        df_kw, rows_kw, author_c, mesh_c = _aggregate_kwcorre_from_triples(
            kw_triples, kw_src
        )
        kwcorre_path = (run_dir / "kwcorre.csv").resolve()
        kwcorre_path.parent.mkdir(parents=True, exist_ok=True)
        df_kw.to_csv(kwcorre_path, index=False, encoding="utf-8-sig")
        kwcorre_saved = str(kwcorre_path.resolve())
        if not args.quiet:
            _run_out(
                f"Saved kwcorre: {kwcorre_path} "
                f"({len(rows_kw)} keyword rows from {len(kw_triples)} articles; "
                f"author_kw unique: {len(author_c)}, mesh unique: {len(mesh_c)})"
            )

    export_paths: list[tuple[str, str]] = [("Excel", str(out.resolve()))]
    if kwcorre_saved:
        export_paths.append(("kwcorre.csv", kwcorre_saved))
    _write_run_record_md(
        run_dir,
        kind="run",
        prep=prep,
        term=term,
        export_paths=export_paths,
    )

    if not args.quiet:
        _run_out(f"Run folder: {run_dir.resolve()}")
        if emit_run_banners:
            print(RUN_BANNER_END, flush=True)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_keywords_config()
    if getattr(args, "kw", None) == KW_ALL_SETS:
        if args.use_keywords_json:
            print("Do not use -kw together with --use-keywords-json.", file=sys.stderr)
            return 2
        nums = discover_kw_set_numbers()
        if not nums:
            print(
                "No data/keywords/kw_N.md or kw_N_*.md files found.",
                file=sys.stderr,
            )
            return 2
        if not args.quiet:
            print(
                f"Run: {len(nums)} keyword set(s) → separate folders: "
                f"{', '.join(f'kw_{n}' for n in nums)}",
                flush=True,
            )
            _run_out(RUN_BANNER_START, flush=True)
        rc = 0
        for n in nums:
            sub = copy.copy(args)
            sub.kw = n
            if not args.quiet:
                print(f"\n--- {_kw_section_label(n)} ---", flush=True)
            r = _cmd_run_once(sub, cfg, emit_run_banners=False)
            if r != 0:
                rc = r
        if not args.quiet:
            print(RUN_BANNER_END, flush=True)
        return rc
    return _cmd_run_once(args, cfg)


def cmd_keywords(args: argparse.Namespace) -> int:
    cfg = load_keywords_config()
    kws = list(cfg.get("keywords") or [])
    if args.action == "list":
        for k in kws:
            print(k)
        return 0
    if args.action == "add":
        for a in args.words:
            s = str(a).strip()
            if s and s not in kws:
                kws.append(s)
        cfg["keywords"] = kws
        save_keywords_config(cfg)
        print("Keywords updated.")
        return 0
    if args.action == "remove":
        for a in args.words:
            s = str(a).strip()
            if s in kws:
                kws.remove(s)
        cfg["keywords"] = kws
        save_keywords_config(cfg)
        print("Keywords updated.")
        return 0
    if args.action == "clear":
        cfg["keywords"] = []
        save_keywords_config(cfg)
        print("Keywords cleared.")
        return 0
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Batch PubMed search (last N days) and export to Excel.",
        epilog="NCBI requires a contact email: set ENTREZ_EMAIL, or entrez_email in data/keywords.json.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser(
        "run",
        help="Search using keywords.json or data/keywords/kw_N.md / kw_N_*.md and export",
    )
    p_run.add_argument(
        "-kw",
        "--kw",
        nargs="?",
        const=KW_ALL_SETS,
        default=None,
        metavar="N",
        help="kw_N (file kw_N.md or kw_N_*.md): give N, or -kw alone for every set (separate folder each)",
    )
    p_run.add_argument(
        "--use-keywords-json",
        action="store_true",
        help="Load query from data/keywords.json instead of -kw",
    )
    p_run.add_argument(
        "--days",
        type=int,
        default=15,
        help="Number of calendar days ending today (default: 15, inclusive); ignored if --range is set",
    )
    p_run.add_argument(
        "--range",
        dest="pdat_range",
        default="",
        metavar="START:END",
        help=(
            "Fixed PDAT window: START:END or START..END (YYYY-MM-DD or YYYY/MM/DD). "
            "Overrides --days."
        ),
    )
    p_run.add_argument(
        "--no-translate",
        action="store_true",
        help="Skip abstract translation (English only)",
    )
    p_run.add_argument(
        "--runs-dir",
        default="",
        metavar="DIR",
        help=(
            "Parent folder for timestamped run dirs (overrides PMSEARCH_RUNS_DIR and "
            f"data/runs_root.md; default: {DATA_DIR / 'runs'})"
        ),
    )
    p_run.add_argument(
        "-o",
        "--output",
        default="",
        help=(
            "Output .xlsx basename under the run folder "
            "(see --runs-dir; default: pubmed.xlsx)"
        ),
    )
    p_run.add_argument(
        "--max-articles",
        type=int,
        default=0,
        help="Max articles to fetch (0 = no limit; uses first N PMIDs from search order)",
    )
    p_run.add_argument(
        "--kwcorre",
        action="store_true",
        help="Also write kwcorre.csv (counts + article_numbers 1,2,3,… in fetch order) in the run folder",
    )
    p_run.add_argument(
        "--kwcorre-sources",
        choices=("all", "author", "mesh"),
        default="all",
        help="With --kwcorre: author keywords, MeSH, or both (default: all)",
    )
    p_run.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print query, date range, hit counts, and cap info",
    )
    p_run.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress summary line and saved path; tqdm still shows unless output is not a TTY",
    )
    p_run.set_defaults(func=cmd_run)

    p_try = sub.add_parser(
        "try",
        help="Preview hit count for kw_N.md / kw_N_*.md (PubMed ESearch only; no download)",
    )
    p_try.add_argument(
        "-kw",
        "--kw",
        nargs="?",
        const=KW_ALL_SETS,
        default=None,
        metavar="N",
        help="kw_N: give N, or -kw alone for every kw_N.md / kw_N_*.md (hit count per set)",
    )
    p_try.add_argument(
        "--use-keywords-json",
        action="store_true",
        help="Load query from data/keywords.json instead of -kw",
    )
    p_try.add_argument(
        "--days",
        type=int,
        default=15,
        help="Same as run: calendar days ending today (default: 15, inclusive); ignored if --range is set",
    )
    p_try.add_argument(
        "--range",
        dest="pdat_range",
        default="",
        metavar="START:END",
        help="Same as run: fixed PDAT window (overrides --days)",
    )
    p_try.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print query string and PDAT range",
    )
    p_try.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Print only the hit count (number)",
    )
    p_try.set_defaults(func=cmd_try)

    p_kwc = sub.add_parser(
        "kwcorre",
        help="Author keywords + MeSH → kwcorre.csv (article_count + article_numbers 1,2,3,…)",
    )
    p_kwc.add_argument(
        "-kw",
        "--kw",
        nargs="?",
        const=KW_ALL_SETS,
        default=None,
        metavar="N",
        help="kw_N: give N, or -kw alone for every kw_N.md / kw_N_*.md (separate run folder each)",
    )
    p_kwc.add_argument(
        "--use-keywords-json",
        action="store_true",
        help="Load query from data/keywords.json instead of -kw",
    )
    p_kwc.add_argument(
        "--days",
        type=int,
        default=15,
        help="Same as run: calendar days ending today (default: 15, inclusive); ignored if --range is set",
    )
    p_kwc.add_argument(
        "--range",
        dest="pdat_range",
        default="",
        metavar="START:END",
        help="Same as run: fixed PDAT window (overrides --days)",
    )
    p_kwc.add_argument(
        "--max-articles",
        type=int,
        default=0,
        help="Max articles to analyze (0 = all search hits; large sets are slower)",
    )
    p_kwc.add_argument(
        "--sources",
        choices=("all", "author", "mesh"),
        default="all",
        help="Include author keywords, MeSH, or both (default: all)",
    )
    p_kwc.add_argument(
        "--runs-dir",
        default="",
        metavar="DIR",
        help=(
            "Parent folder for timestamped run dirs (same as ``run``; overrides env / runs_root.md)"
        ),
    )
    p_kwc.add_argument(
        "-o",
        "--output",
        default="",
        help="Summary CSV basename under the run folder (default: kwcorre.csv)",
    )
    p_kwc.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print query string and PDAT range",
    )
    p_kwc.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress progress line and summary",
    )
    p_kwc.set_defaults(func=cmd_kwcorre)

    p_kw = sub.add_parser("keywords", help="Manage saved search keywords")
    kw_sub = p_kw.add_subparsers(dest="action", required=True)
    kw_sub.add_parser("list", help="List keywords")
    p_add = kw_sub.add_parser("add", help="Add keywords")
    p_add.add_argument("words", nargs="+", help="Keyword strings")
    p_rem = kw_sub.add_parser("remove", help="Remove keywords")
    p_rem.add_argument("words", nargs="+", help="Keywords to remove")
    kw_sub.add_parser("clear", help="Clear all keywords")

    args = parser.parse_args()
    if args.command == "keywords":
        return cmd_keywords(args)
    err = _normalize_parsed_kw(args)
    if err:
        return err
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
