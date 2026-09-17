# Pediatric Camp Analytics

An automated ETL + analytics + dashboard project for medical screening data
collected during Pediatric Camps. Drop new CSV exports into `csv/`, run the
pipeline, and get a cleaned master dataset, an Excel workbook, and a live
web dashboard — all keyed on each child's **UHID**.

---

## 1. Project Purpose

Pediatric Camps generate multiple CSV exports over time (general
health/BMI forms, dental forms, vitals reports, mental-health
questionnaires, etc.), often from different export tools, with slightly
different column names, casing, and formatting. This project:

- Combines every CSV in `csv/` into one clean, de-duplicated dataset keyed
  on `UHID`.
- Filters to **Pediatric Camp** records only (case/whitespace insensitive).
- Recalculates BMI and BMI status using a **pediatric** age/sex-specific
  reference instead of adult BMI thresholds.
- Produces an Excel workbook, JSON files, and a responsive web dashboard
  with KPI cards, charts, filters, and an image carousel.
- Automatically re-runs whenever CSVs change (locally via `--watch`, or in
  CI via GitHub Actions).

---

## 2. Folder Structure

```
pediatric-camp-analytics/
├── csv/                     # Drop raw CSV exports here
├── excel/                   # Generated Excel report (auto-overwritten)
├── output/                  # Generated CSV/JSON used by the website
│   ├── cleaned_data.csv
│   ├── pediatric_camp_data.json
│   ├── summary.json
│   ├── data_quality_report.csv
│   └── duplicate_merge_log.csv
├── images/                  # Carousel photos (camp-1.jpg ... camp-5.jpg)
├── scripts/
│   ├── etl.py                 # Main entry point / orchestrator
│   ├── column_normalizer.py   # Reusable column-name standardization
│   ├── data_cleaning.py       # CSV discovery, loading, filtering
│   ├── dedup_merge.py         # UHID duplicate detection & merging
│   ├── pediatric_bmi.py       # Pediatric BMI calculation & classification
│   ├── validation.py          # Medical value range validation
│   ├── data_analysis.py       # KPIs & chart-data aggregation
│   └── generate_reports.py    # Excel workbook generation
├── website/
│   ├── index.html
│   ├── styles.css
│   └── script.js
├── config/
│   └── config.json
├── requirements.txt
├── README.md
└── .github/workflows/data_pipeline.yml
```

---

## 3. How to Add CSV Files

Just copy any number of new CSV exports into the `csv/` folder — filenames
are never hard-coded; every `*.csv` file present is discovered and
processed automatically, and files can have different (but overlapping)
column sets. Then re-run the pipeline (see §8) or push to GitHub (see §11).

---

## 4. How the ETL Pipeline Works

`scripts/etl.py` runs these stages in order:

1. **Discover & load** every CSV in `csv/`. Each file is auto-classified as
   either a *wide-form template export* (one row per visit, many form
   fields as columns) or a *long-form vitals export* (one row per
   UHID + vital parameter, which gets pivoted into a wide table).
2. **Normalize columns** — headers like `"UHID "`, `"uhid"`, `" Uhid"` are
   mapped to a single canonical `UHID` column (see §5).
3. **Normalize values** — whitespace collapsed, missing tokens (`NA`,
   `N/A`, `-`, `null`, `None`, empty string, ...) standardized, categorical
   values case-normalized (`male`/`MALE`/`Male` → `Male`).
4. **Filter to Pediatric Camp records** (see §7).
5. **De-duplicate & merge by UHID** (see §5-6 below / §6 of the original
   spec).
6. **Recalculate pediatric BMI** and BMI status from Height + Weight + Age
   + Gender (see §"Pediatric BMI Recalculation").
7. **Validate** medical fields against plausible ranges, flagging (never
   deleting) questionable values.
8. **Compute analytics** — KPIs and chart-ready aggregates, adapted to
   whichever columns are actually present in the data.
9. **Write outputs** — `output/*.csv`, `output/*.json`, and
   `excel/Pediatric_Camp_Cleaned_Data.xlsx`.

Console output looks like:

```
[INFO] Found 5 CSV files
[INFO] Loaded 1,110 raw records across 5 file(s)
[INFO] Pediatric Camp records: 595
[INFO] Unique UHIDs: 159
[INFO] Duplicate UHIDs detected: 159
[INFO] Duplicate records merged into 159 master records
[INFO] Pediatric BMI and BMI Status recalculated from Height/Weight/Age/Gender
[INFO] Missing values normalized and validation flags applied
[INFO] CSV/JSON outputs written to output/
[INFO] Excel report generated: excel/Pediatric_Camp_Cleaned_Data.xlsx
[INFO] Dashboard JSON generated
[INFO] ETL completed successfully
```

---

## 5. UHID Duplicate Handling

`UHID` is the primary identifier throughout the pipeline. After all CSVs
are combined:

- UHIDs are trimmed and upper-cased so formatting differences never create
  false "different patients."
- Records sharing a UHID are **merged, not deleted**: for every column, the
  first non-null/non-empty value found across the duplicate records is
  kept (`prefer_non_null` strategy, configurable via
  `config.json → conflict_resolution_strategy`).
- Every merge is logged to `output/duplicate_merge_log.csv` with the
  UHID, how many duplicate records were involved, which columns were
  merged, which columns had **conflicting** non-null values across
  records, and the resolution strategy used — so merges are fully
  auditable.

A supplementary vitals export (long-format, one row per vital parameter)
has no camp field of its own; its rows are joined onto the confirmed
Pediatric Camp roster **by UHID** rather than being dropped or
mis-classified.

---

## 6. Null / Missing Value Handling

- Missing tokens (`""`, `NA`, `N/A`, `-`, `null`, `None`, etc., configurable
  in `config.json`) are normalized to a single missing representation
  (`NaN`), never silently deleted from the row.
- No row is ever dropped just because it contains nulls.
- No individual medical measurement (BMI, height, weight, vitals, ...) is
  ever fabricated or replaced with an average. If height or weight is
  missing, the recalculated BMI is left blank and the record is flagged in
  the validation report instead.
- `output/data_quality_report.csv` documents, per column: total records,
  missing count, missing %, unique values, and data type.

---

## 7. Pediatric Camp Filtering

The camp/program column is auto-detected from `config.json →
camp_column_candidates` (e.g. `Camp`, `Camp Name`, `Camp Type`, `Program`,
`Event`, `Camp Category`, `Created By`, `Modification by`). In this
dataset's exports, the camp is recorded in the **`Created By`** field.

Matching is case-insensitive and whitespace-normalized, so `Pediatric
Camp`, `PEDIATRIC CAMP`, `pediatric  camp`, ` Pediatric Camp ` etc. are all
recognized as the same value (configured via `config.json →
camp_filter`).

If no camp-like column exists in a given file (as with the vitals export),
the pipeline does **not** silently produce an empty result for that file —
it logs a clear `[WARNING]`, and includes rows from that file only for
UHIDs already confirmed as Pediatric Camp patients from another file. If
**no** file yields any confirmed Pediatric Camp records at all, the
pipeline stops with a clear `[ERROR]` rather than producing an empty
dataset silently.

---

## 8. How to Run Locally

```bash
# from the project root
pip install -r requirements.txt
python scripts/etl.py
```

This regenerates everything in `output/` and `excel/`. Re-run any time you
add or change files in `csv/`.

---

## 9. How to Start Watch Mode

```bash
python scripts/etl.py --watch
```

This runs the pipeline once immediately, then monitors `csv/` and
automatically re-runs the full pipeline whenever a CSV file is added or
modified. Requires the `watchdog` package (already in
`requirements.txt`). Stop with `Ctrl+C`.

---

## 10. How to Open the Dashboard

The website reads `output/pediatric_camp_data.json` and
`output/summary.json` via `fetch()`, so it needs to be served over HTTP
(opening `index.html` directly with `file://` will be blocked by most
browsers' CORS rules for `fetch`). From the project root:

```bash
python -m http.server 8000
```

Then open `http://localhost:8000/website/` in a browser.

For permanent hosting, deploy via **GitHub Pages** (Settings → Pages →
serve from the repository root or `/website` — see §12).

---

## 11. How GitHub Actions Works

`.github/workflows/data_pipeline.yml` triggers on any push that changes
files under `csv/**` (or manually via the "Run workflow" button). It:

1. Checks out the repo.
2. Installs Python dependencies.
3. Runs `python scripts/etl.py`.
4. Commits the regenerated `output/` and `excel/` files back to the repo
   (only if they actually changed) and pushes.

**No infinite loop:** commits made by the workflow use the message suffix
`[skip ci]`, and pushes authenticated with the default `GITHUB_TOKEN` do
not themselves trigger new workflow runs — both together prevent the
workflow from re-triggering itself.

---

## 12. How to Configure the GitHub Repository

```bash
git init                       # already done for you in this project
git remote add origin <your-repo-url>
git push -u origin main
```

For GitHub Pages: **Settings → Pages → Build and deployment → Deploy from
a branch → `main` / `/ (root)`** (or point it at `/website` if you prefer
`https://<user>.github.io/<repo>/` to load the dashboard directly — you'll
need to adjust the relative `../output/...` and `../images/...` paths in
`website/script.js` and `index.html` if you do).

No tokens, passwords, or credentials are stored in this repository. The
workflow uses only the automatically-provided `GITHUB_TOKEN`.

---

## 13. How to Add Carousel Images

Place JPG/PNG files named `camp-1.jpg` through `camp-5.jpg` in `images/`.
The carousel loads exactly these five filenames and **gracefully skips**
any that don't exist — so it works fine with anywhere from 1 to 5 images.
Landscape photos (roughly 21:8) look best on desktop; the layout switches
to a 4:3 crop on mobile.

Currently included: `camp-1.jpg` (the camp group photograph supplied with
this project). Add `camp-2.jpg` … `camp-5.jpg` for the full rotating
carousel.

---

## 14. How to Modify Dashboard KPIs

KPIs are computed in two places that should be kept in sync:

- **Server-side** (used for `output/summary.json` and the Excel "Summary"
  sheet): `scripts/data_analysis.py → build_kpis()`.
- **Client-side** (used for live filtering in the browser):
  `website/script.js → computeKpis()`.

Both already **adapt automatically** — a KPI is only shown if its
underlying column exists in the data (e.g. `Referral_Needed_Count` won't
appear unless a `Referral_Needed` column is present). Add a new KPI by
adding a block to both functions.

---

## 15. How to Modify Charts

Same pattern as KPIs:

- Server-side: `scripts/data_analysis.py → build_chart_data()`.
- Client-side: `website/script.js → renderCharts()`, which calls the
  generic `renderChart(canvasId, cardCssId, chartType, {labels, values})`
  helper (backed by Chart.js). Add a new `<div class="chart-card">` block
  in `index.html`, then a corresponding `renderChart(...)` call.

Charts automatically hide themselves (`.chart-card.empty`) with a small
explanatory note if the current filter selection leaves no data for that
chart.

---

## 16. Data Privacy Considerations

Because this is patient/medical data:

- `config.json → privacy_mode: true` (default) strips `Patient_Name`,
  `Mobile_No`, and `UHID` itself from `output/pediatric_camp_data.json` —
  the only file the public dashboard reads. The dashboard therefore never
  displays or transmits an individual identifier.
- The dashboard only ever shows **aggregated** statistics and **de-identified**
  per-record fields (age, gender, BMI, screening flags, etc. — never name
  or contact info) used purely to power client-side filtering.
- Full patient-level detail (including name/UHID/mobile) is retained only
  in `output/cleaned_data.csv` and the Excel workbook — these are
  generated artifacts meant for the camp organizers, not for public
  publishing. Restrict access to `output/cleaned_data.csv`,
  `excel/*.xlsx`, and `csv/*` (e.g. via `.gitignore`/private repo/access
  controls) if this project is deployed somewhere the CSVs or full
  cleaned data shouldn't be public.
- Set `privacy_mode: false` only in a deliberately access-controlled
  deployment.

---

## 17. Pediatric BMI Recalculation — Method & Limitations

**BMI and BMI Status are always recalculated from Height + Weight + Age +
Gender** in `scripts/pediatric_bmi.py` — the BMI/BMI Status values present
in the raw CSV exports are never trusted or copied through.

```
BMI = Weight (kg) / (Height (m))²         # Height (cm) is converted to metres first
```

If height or weight is missing or implausible, BMI is left blank (never
fabricated) and the record is flagged for review in the validation
report — it is **not** silently dropped.

**Classification** uses an **age- and sex-specific pediatric BMI-for-age
reference** (ages 2–19) instead of fixed adult thresholds:

| BMI relative to age/sex band | Status        |
|---|---|
| < 5th percentile             | Underweight   |
| 5th – 85th percentile        | Normal        |
| 85th – 95th percentile       | Overweight    |
| ≥ 95th percentile            | Obese         |

The table in `scripts/pediatric_bmi.py` (`BOYS_BMI_BANDS` /
`GIRLS_BMI_BANDS`) provides approximate, integer-age, sex-specific 5th /
85th / 95th percentile BMI cutoffs, constructed in the style of
CDC (2000) / WHO growth-chart references. **This is a simplified
percentile-band lookup, not a full LMS (Lambda-Mu-Sigma) calculation** —
the method real clinical growth-chart software uses to compute an exact
percentile for a fractional age. It is intended to give a clear, auditable
pediatric classification suitable for camp/dashboard reporting.

**For clinical decisions, use the official WHO Child Growth Standards /
WHO Growth Reference 5–19 years, or CDC growth charts, via an LMS-based
calculator.**

Records for children outside the supported age range (2–19 years), or
missing age/gender, are flagged **"Needs Review"** rather than guessed,
while the calculated BMI value (if computable) is still retained.

Output columns: `BMI`, `BMI_Status`, `BMI_Percentile_Band` (e.g.
`"85th-95th"`), `BMI_Reference` (the method statement above, embedded in
every record for auditability).

---

## 18. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `[ERROR] No CSV files found` | Nothing in `csv/`. Add at least one `.csv` file. |
| `[ERROR] No UHID column found; file skipped` | That file has no column recognized as `UHID`. Check its header row, or add a variant to `CANONICAL_COLUMN_MAP` in `scripts/column_normalizer.py`. |
| `[ERROR] No records matched the Pediatric Camp filter` | Check `config.json → camp_filter` and `camp_column_candidates` against the actual column/values in your CSVs. |
| Dashboard shows "Could not load output/pediatric_camp_data.json" | Run `python scripts/etl.py` first, and make sure you're serving the site over HTTP (`python -m http.server`), not opening `index.html` directly via `file://`. |
| A KPI/chart is missing | That's expected if the underlying column isn't present in your data — KPIs/charts only render when supported by the data (see §14/§15). |
| GitHub Action doesn't push | Confirm the workflow has `permissions: contents: write` (already set) and that the repository allows Actions to push (Settings → Actions → General → Workflow permissions → "Read and write permissions"). |
| Watch mode errors with `ImportError: watchdog` | `pip install watchdog` (included in `requirements.txt`). |

---

## Configuration Reference (`config/config.json`)

| Key | Purpose |
|---|---|
| `camp_filter` | Target camp/program value to keep (case/whitespace-insensitive). |
| `camp_column_candidates` | Ordered list of column names to search for the camp/program field. |
| `unique_id_column` | Primary identifier column (`UHID`). |
| `csv_directory` / `output_directory` / `excel_directory` / `images_directory` | Folder locations. |
| `dashboard_title` / `dashboard_subtitle` | Website header text. |
| `privacy_mode` | Strip direct identifiers from the public dashboard JSON. |
| `missing_value_tokens` | Strings treated as "missing" during cleaning. |
| `conflict_resolution_strategy` | How duplicate-UHID conflicts are resolved. |
| `pediatric_bmi_reference` | Human-readable description of the BMI reference method (see §17). |
| `pediatric_age_min` / `pediatric_age_max` | Supported age range for BMI-for-age classification. |
| `vital_reference_ranges` | Plausible-value ranges used for the `*_Quality_Flag` validation columns. |
