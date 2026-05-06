# pmsearch 0.1.0

Batch **PubMed** search over a **publication-date (PDAT)** window, export to **Excel** easy to read, optional  abstract translation**, and optional **keyword co-occurrence (`kwcorre`)** analysis export to CSV.

---

## Features

- **ESearch + EFetch** — Pull article metadata (title, authors, affiliations, journal, abstract, MeSH / author keywords, etc.) for a query and PDAT range.
- **Excel** — Default `pubmed.xlsx` with a short run summary (PDAT, keyword source, final PubMed `term`).
- **Translation** — English abstracts → Chinese by default, Japanse or no translation by option.
- **`kwcorre`** — Optional `kwcorre.csv` (keyword counts + article indices in fetch order).
- **Keyword sources** — manage your own keyword file in the form of: `data/keywords/kw_N.md`, optional `data/keywords/kw_N_comment.md` (N is number)
- **Run output** — Default under `data/runs/<timestamp>_dayN_kw_…/`; override via `data/runs_root.md`, **`PMSEARCH_RUNS_DIR`**, or **`--runs-dir`**. Each run folder includes **`run_record.md`** (paths, query, PDAT).
- **CLI** — After `pip install -e .`, run **`pmsearch`** from any directory (point **`PMSEARCH_DATA_DIR`** at your `data/` if needed).

---

## Requirements

- **Python ≥ 3.9**
- **NCBI E-utilities** — User needs to set `entrez_email` in `data/keywords.json` or **`ENTREZ_EMAIL`** / **`NCBI_EMAIL`**. Optional **`NCBI_API_KEY`** for higher rate limits.

---

## Installation

```bash
# Clone the repository to a location where you can easily modify the keyword files!
# cd ~/Desktop
git clone https://github.com/urourouroooo/pmsearch.git
cd pmsearch
```

Create a new environment (optional), then install:

```bash
conda create -n pmsearch python=3.10
conda activate pmsearch

pip install --upgrade pip
pip install -e .
```

> **Tip:** `pip install --upgrade pip` is required if your pip version is older than 21.3. Skipping it may cause an editable-install error.

Test run:

```bash
pmsearch --help
```

Without installing (from the repo root):

```bash
python -m pmsearch --help
```

## Usage

### STEP 1: Set your contact email

**[👉🏻 Required]** NCBI policy requires a valid contact email for E-utilities access.

Copy the example config and fill in your email:

```bash
cp data/keywords.json.example data/keywords.json
```

Then open `data/keywords.json` and replace the placeholder:

```json
{
  "keywords": [],
  "entrez_email": "your_email@example.com",
  "ncbi_api_key": ""
}
```

Without this step, pmsearch will exit with an error asking for an email.

### STEP2: Set searching keywords

 `pmsearch` recognize md files within data/keywords. 
The Markdown file name must follow the format `kw_N.md` or `kw_N_xxxx.md`, where `N` is a number used to select keywords at runtime, and `xxxx` is optional and serves as a user-defined label for easier identification.

The keyword usage in `kw_N.md` should follow the **standard keyword conventions used in PubMed.** See `kw_N.md syntax (short) `section below.

This repository includes a demo keyword file: `data/keywords/kw_1_demo.md`. ] Here, by using the term `join:AND`, the first line and second line will be joined by `AND`. 

```
join: AND

"T follicular helper cells"[MeSH Terms] OR Tfh[Title/Abstract] OR "follicular helper cells"[Title/Abstract]

humans[MeSH Terms] OR mice[MeSH Terms]
```

### STEP3 : Set output directory & translation language

By default, pmsearch outputs Excel and CSV files to `data/runs`. However, users can customize the output directory by modifying the `data/keywords/runs_root.md` file.  For example, if you want to set the output directory to a folder called pmsearch_run on desktop, you can simply write:

```
runs_root: ~/Desktop/pmsearch_run
```

`pmsearch` can translate the abstract via Google API. Currently, Chinses and Japanese are supported, while the default setting is Chinese.
Users can customize the default language settings into Japanese, for example, by modifying data/translate_lang.md by simply write:

```
jap
```

More language translation option will be available in future updates.

### STEP4: RUN

Use the demo keyword file `data/keywords/kw_1_demo.md` search related article within recent 15 days (default) and do keywords co-occurrence analysis. Translate the abstract into Chinese (default).

```bash
pmsearch run -kw 1 --kwcorre
```

Or, to test the keyword set and see how many hits without downloading:

```bash
pmsearch try -kw 1
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
pmsearch try -kw 1 --range 2025/01/01..2025/01/31
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
