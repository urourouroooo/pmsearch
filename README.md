[README.md](https://github.com/user-attachments/files/26453958/README.md)
# pmsearch

Batch **PubMed** search over a **publication-date (PDAT)** window, export to **Excel**, optional **Chinese abstract translation**, and optional **keyword co-occurrence (`kwcorre`)** CSV.

---

## Features

- **ESearch + EFetch** — Pull article metadata (title, authors, affiliations, journal, abstract, MeSH / author keywords, etc.) for a query and PDAT range.
- **Excel** — Default `pubmed.xlsx` with a short run summary (PDAT, keyword source, final PubMed `term`).
- **Translation** — English abstracts → `Abstract (Chinese)` by default (`--no-translate` to skip).
- **`kwcorre`** — Optional `kwcorre.csv` (keyword counts + article indices in fetch order).
- **Keyword sources** — `data/keywords/kw_N.md`, optional `data/keywords/kw_N_comment.md`, or `data/keywords.json`.
- **Run output** — Default under `data/runs/<timestamp>_dayN_kw_…/`; override via `data/runs_root.md`, **`PMSEARCH_RUNS_DIR`**, or **`--runs-dir`**. Each run folder includes **`run_record.md`** (paths, query, PDAT).
- **CLI** — After `pip install -e .`, run **`pmsearch`** from any directory (point **`PMSEARCH_DATA_DIR`** at your `data/` if needed).

---

## Requirements

- **Python ≥ 3.9**
- **NCBI E-utilities** — Set `entrez_email` in `data/keywords.json` or **`ENTREZ_EMAIL`** / **`NCBI_EMAIL`**. Optional **`NCBI_API_KEY`** for higher rate limits.

---

## Installation

```bash
git clone  https://github.com/urourouroooo/pmsearch.git
# Highly recommend the user to move the cloned pmsearch file to another location where they can easily modify the keywords files!!!
# Personally I prefer the Desktop
```
```
cd pmsearch      # conduct these after you remove the file
pip install -e .
```

Or install dependencies then the package:

```bash
pip install -r requirements.txt
pip install -e .
```

Usage:

```bash
pmsearch --help
```

Without installing (from the repo root):

```bash
python -m pmsearch --help
```

---

## Data directory (`data/`)

The app resolves **`DATA_DIR`** (see below). Typical layout:

| Path | Purpose |
|------|---------|
| `keywords.json` | Contact email, optional API key, and/or `keywords` list or `pubmed_query` |
| `keywords/kw_1.md` | Keyword set **1** (one PubMed clause per line; `join:` / `mode: freeform` supported) |
| `keywords/kw_2.md` | Set **2**, … |
| `keywords/kw_3_notes.md` | Same index **3** as `kw_3.md`; if **`kw_3.md` exists**, it takes precedence |
| `runs_root.md` | Optional: set the parent directory for timestamped run folders (e.g. `runs_root: ~/Desktop/pmsearch_run`) |
| `runs/` | Default parent for run folders (overridable; see below) |

### How `DATA_DIR` is chosen

1. **`PMSEARCH_DATA_DIR`** (legacy: **`PUBMED_BATCH_DATA_DIR`**)
2. Else **`./data`** under the current working directory, if it looks like a project `data/` (contains `keywords.json` or `keywords/`)
3. Else **`<repo>/data`** next to the installed package (with editable install, that is your clone’s `data/`)

If you run `pmsearch` from arbitrary directories, set **`PMSEARCH_DATA_DIR`** to the folder that contains `keywords.json` and `keywords/`.

### Where run folders are created

Precedence: **`--runs-dir`** → **`PMSEARCH_RUNS_DIR`** → **`DATA_DIR/runs_root.md`** → **`DATA_DIR/runs`**

Each run creates a subdirectory such as `20260403_153045_day7_kw_3/` with `pubmed.xlsx`, `run_record.md`, and optionally `kwcorre.csv`.

---

## Commands

| Command | Description |
|---------|-------------|
| `pmsearch run` | Search, fetch, translate (optional), write Excel (+ optional kwcorre) |
| `pmsearch try` | ESearch only — print hit count |
| `pmsearch kwcorre` | Fetch metadata and write kwcorre-style CSV |
| `pmsearch keywords` | Manage the simple `keywords` list in `keywords.json` (separate from `kw_*.md`) |

### `pmsearch run` (common options)

| Option | Description |
|--------|-------------|
| `-kw N` | Use `data/keywords/kw_N.md` or the **only** `kw_N_*.md` for that `N` |
| `-kw` | No number: run every keyword set index found under `keywords/` |
| `--use-keywords-json` | Load query from `keywords.json` (do not combine with `-kw`) |
| `--days N` | Sliding window: **N** calendar days ending **today** (default **15**). Ignored if `--range` is set. |
| `--range START:END` | Fixed PDAT interval: `START:END` or `START..END`; dates `YYYY-MM-DD`, `YYYY/MM/DD`, or `YYYY.MM.DD`. Overrides `--days`. |
| `--no-translate` | Skip abstract translation |
| `--max-articles M` | Cap fetched articles (first *M* PMIDs in search order) |
| `--kwcorre` | Also write `kwcorre.csv` in the run folder |
| `--runs-dir DIR` | Parent directory for this run’s timestamp folder |
| `-o name.xlsx` | Excel basename inside the run folder (default `pubmed.xlsx`) |
| `-q` / `-v` | Quiet / verbose |

The same **`--days`** / **`--range`** behavior applies to **`try`** and **`kwcorre`**.

### Examples

```bash
pmsearch run -kw 3 --days 7 --kwcorre
pmsearch run -kw 1 --range 2025-01-01:2025-03-31
pmsearch run -kw --days 15 --runs-dir ~/Desktop/pmsearch_run
pmsearch try -kw 1 --days 30
pmsearch try -kw 1 --range 2025-01-01:2025-01-31
pmsearch kwcorre -kw 2 --days 7
```

### `kw_N.md` syntax (short)

- **Default:** one non-comment line = one clause; lines wrapped and joined with `join: OR` / `AND` / `NOT` (default OR). Lines starting with `#` are comments.
- **`mode: freeform`** (optional `#`): following non-comment lines are joined into **one** raw PubMed query string, passed through to ESearch.

### Keyword filenames

- **`kw_N.md`** — canonical name for set **N**.
- **`kw_N_anything.md`** — optional descriptive suffix; still set **N**. If **`kw_N.md` exists**, it wins. If only multiple `kw_N_*.md` files exist (no `kw_N.md`), the run fails until you add **`kw_N.md`** or keep a single `kw_N_*.md`.

---

## Development

- Package: **`pmsearch`**
- Entry point: **`pmsearch.__main__:main`** (`python -m pmsearch`)
- Dependencies: **`pyproject.toml`** / **`requirements.txt`**

---

## License

Add a **`LICENSE`** file in the repository root. This README does not specify legal terms.
