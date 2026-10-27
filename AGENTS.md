# AGENTS.md

## What this is

Notebook-first EDA of the Dunnhumby "Breakfast at the Frat" dataset: a weekly
store × SKU sales/promo panel (156 weeks, 524,950 transaction rows). All analysis lives in
`notebooks/`; `src/` is effectively unused (see *Repo state*). `presentation/` is a derived
deliverable — a bilingual briefing built from the same panel by `build_deck.py` — not a
second analysis, and it must not drift from the notebooks.

## Commands

- Setup: `uv sync` (Python 3.14). Always invoke tooling as `uv run ...`, never bare
  `python`/`jupyter` — the venv is the only place deps exist.
- **Run notebooks from inside `notebooks/`**: `cd notebooks && uv run jupyter lab`.
  Every notebook hardcodes `DATA = Path("../data/dunnhumby - Breakfast at the Frat.xlsx")`,
  which resolves against the *current working directory*, not the notebook location.
  Launching Jupyter from the repo root silently breaks every data cell.
- `uv run` works from `notebooks/` too (uv walks up to find `pyproject.toml`).
- Static audit, no data needed: `cd notebooks && uv run jupyter nbconvert --to notebook
  --execute --stdout 3_claims_audit.ipynb > /dev/null` — non-zero exit on failure, leaves
  the notebook untouched.
- Rebuild the deck: `uv run python presentation/build_deck.py` from the repo root (or from
  `presentation/`; it resolves paths against the file, not the cwd). It needs the workbook,
  takes ~30 s, rewrites `presentation/index.html`, prints every number the deck quotes, and
  exits non-zero if a claim it asserts stops holding. `DECK_PREVIEW=1 uv run python
  presentation/build_deck.py` also writes PNG previews to `/tmp/opencode/deck_preview`.

## Data

- Source: <https://www.kaggle.com/datasets/frtgnn/dunnhumby-the-complete-journey>.
- `*.xlsx` is gitignored, so a fresh clone has **no data**. Single file, name contains
  spaces — must match exactly.
- Sheets: `Glossary`, `dh Store Lookup`, `dh Products Lookup`, `dh Transaction Data`.
- Parsing quirks — get these wrong and cells fail or silently misread:
  - The three data sheets have a title banner on row 1 → `header=1`. `Glossary` uses `header=3`.
  - `Glossary` also produces a junk `Unnamed: 3` column → `.drop(columns=["Unnamed: 3"])`.
  - `dh Transaction Data` takes ~30 s to parse. Read it once per session, not per cell.
- `dh Store Lookup` has 79 rows for 77 stores: `STORE_ID` 4503 and 17627 each appear twice
  with conflicting `SEG_VALUE_NAME`. Always `stores.drop_duplicates("STORE_ID")` before
  mapping labels onto or joining with transactions.
- Panel vs lookup dimensions differ — do not conflate them. Transactions cover **77 stores ×
  55 UPCs**; the lookup sheets carry 77 unique store IDs (79 rows) and 58 UPCs. The "3 of 58
  products never appear" and store-row-count items below are lookup-vs-facts mismatches, not
  data errors.
- Already-triaged data issues (do not re-derive): `PRICE`/`BASE_PRICE` missing or
  non-positive on 209 rows (0.04%); 3 of 58 products never appear in transactions;
  `PARKING_SPACE_QTY` missing on 52/79 store rows. See the findings cell at the end of
  `notebooks/0_prelim_analysis.ipynb`.
- pandas is **3.0.x**, not 2.x: string columns default to the new `str` dtype and
  copy-on-write is on. Check old idioms before reusing them.
- `presentation/build_deck.py` re-parses the workbook on every run (~30 s), unlike the
  notebooks' once-per-session load.

## Notebook conventions

- Files are numbered by analysis stage: `0_prelim_analysis`, `1_distribution_analysis`,
  then `EDA.ipynb` (the main time-series and cohort analysis), then
  `2_readme_figures.ipynb` (re-draws the five committed PNGs, `01`–`05`) and
  `3_claims_audit.ipynb` (static checks only — no data).
- `2_readme_figures.ipynb` writes to `FIG = Path("../figures")`, so it too must run from
  `notebooks/`. It prints every number its captions quote, and the README captions quote
  the same ones: run it after any analysis change, and treat a printed number that
  disagrees with the README as a stale figure, not a docs bug.
- Each notebook opens with markdown stating its purpose and the questions it answers, and
  closes with a markdown findings/takeaways cell.
- Numeric claims in prose must be printed by a code cell so they stay checkable against the
  data — that is the explicit standard stated in `1_distribution_analysis.ipynb`. An eyeballed
  reading is acceptable only when labelled as one (e.g. "raw plot approximation"); an
  unexplained statistic is not.
- Inferential claims carry their test in the same section, and the prose quotes what the
  cell prints: HAC(4) + weekly-dummy + Mann-Kendall trend tests (§2), a permutation test on
  the seasonal share (§3), Fisher-z intervals with Benjamini-Hochberg (§4), a cell-level
  cluster bootstrap (§5.2), paired within-store tests (§5.3), and two-way fixed effects with
  pairwise mechanism comparisons (§5.5). Don't add a p-value without the cell that prints
  it, and don't rank two estimates whose intervals overlap.
- **No notebook carries committed outputs.** Every code cell ships `"outputs": []` with
  `execution_count: null`, in HEAD as well as the working tree, so checking a prose claim
  means re-running the cell rather than reading the file. Re-running to verify is expected;
  committing the outputs is not. Don't strip or add outputs unasked.
- `1_distribution_analysis.ipynb` closes with `## 6. Conclusion`: one findings paragraph per
  opening question, then an `**Open threads.**` bullet list carrying forward threads raised
  earlier in the prose. Open threads must not introduce a number no cell prints.
- Several cells are the author's own prose rather than generated text. Preserve their voice and
  register when editing nearby — don't silently homogenise their wording or punctuation.
- Reuse the existing preamble and palette (seaborn `whitegrid`, explicit `plt.rcParams`,
  `pd.set_option` float format, `max_columns=50`, fixed promo/no-promo colors) instead of
  inventing new defaults.

## Presentation — generated, not hand-edited

- `presentation/index.html` is **generated and committed**: one self-contained page with
  the English deck and a Russian mirror (8 slides each), charts inlined as SVG. Edit the
  sources and rebuild; never patch the HTML by hand.
- Sources: `template.html` (English slide copy, `{{PLACEHOLDER}}` slots), `template.ru.html`
  (Russian mirror, injected as a hidden second `<main>` into the same page), and
  `build_deck.py` (all numbers, all charts, the render and the asserts).
- Every number is recomputed from the workbook in `analyse()`, following the same
  computations as `EDA.ipynb` (§2 trend, §3 seasonality, §4 repeat-year, §5.2 promotion
  economics, §5.3 baskets, §5.5 household acquisition). Changing the analysis moves the deck
  only after a rebuild, so a deck edit and a notebook edit are one change, not two.
- Charts are rendered once per language because the SVG text is baked in; chart copy lives
  in `CHART_TEXT` in `build_deck.py`.
- The build gates on prose claims as well as shape: the mechanism lifts must stay pairwise
  separated, the four category cost intervals must stay disjoint in display order, both
  languages must still carry 8 slides, and no `{{PLACEHOLDER}}` may survive. A failed assert
  means the claim no longer holds — fix the claim, don't loosen the assert.
- The deck's palette (`INK`, `MUTED`, `ACCENT`, `CLAY`…) and chart style are its own, not
  the notebooks' seaborn defaults. Reuse them when adding a chart.
- The README links to `presentation/index.html`, and `3_claims_audit.ipynb` resolves that
  link like any other; moving or renaming the file breaks the audit.
- At HEAD the committed deck is **not** byte-identical to a rebuild: `template.ru.html`
  reads "не увеличивает среднюю корзину" where `index.html` reads "не увеличивает корзину",
  a hand edit the template never received. A rebuild reverts it, so diff the file and settle
  which wording is canonical before committing a rebuild.

## Repo state — verify before relying on it

- `src/ecommerce_analysis/__init__.py` imports `ecommerce_analysis.load`, which was
  deleted in commit `4066928`. `import ecommerce_analysis` raises `ModuleNotFoundError`.
  The package is unusable — don't build on it, and don't silently "fix" it. `pyproject.toml`
  now names the project `promotion-profitability` and sets `[tool.uv] package = false` with
  a comment about the leftover `src/` directory; keep it that way, or `uv sync` tries to
  build a package that does not exist.
- A stale `src/ecommerce_analysis/__pycache__/load.cpython-314.pyc` survives from that deleted
  module (untracked, gitignored). Verified: it does **not** revive the import, which still
  raises `ModuleNotFoundError`. Don't read its presence as evidence the module exists.
- `README.md` is current: it describes the notebooks, the five committed figures, the
  bilingual deck and the measured findings, and its `§` references are checked by
  `3_claims_audit.ipynb`. Any DuckDB instructions you find (`uv run load-data`,
  `data/archive.zip`, `data/raw/`, `data/analytics.duckdb`) are historical — removed in
  `4066928`; DuckDB is not a dependency and there is no `[project.scripts]`.
- `.agents/skills/humanizer-zh/` is a vendored skill recorded in `skills-lock.json`;
  `.claude/skills/humanizer-zh` is a symlink to it. It is prose-editing tooling for the
  Russian deck copy, not part of the analysis.
- No test suite, no linter or formatter config, no CI. Verification = the static checks in
  `3_claims_audit.ipynb`, re-running the notebook and checking the printed numbers against
  the prose claims, and the deck build's asserts for the numbers the deck states as prose.
- Commits use one-line conventional-commit English (`feat: add scipy dependency`).