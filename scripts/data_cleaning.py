"""
data_cleaning.py
-----------------
Core ETL: discover CSVs, load them (handling both "wide form" template
exports and "long form" vitals exports), normalize columns/values, filter
to Pediatric Camp records, and merge duplicate UHIDs into a single master
dataset.

No filenames are hard-coded: every *.csv file in the configured csv/
directory is discovered and processed automatically.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from column_normalizer import normalize_columns, coalesce_duplicate_columns, slugify_header

logger = logging.getLogger("pediatric_etl")

CATEGORICAL_CASE_MAP = {
    "male": "Male", "female": "Female", "other": "Other", "transgender": "Transgender",
    "yes": "Yes", "no": "No",
    "normal": "Normal", "underweight": "Underweight", "overweight": "Overweight", "obese": "Obese",
    "good": "Good", "poor": "Poor", "fair": "Fair",
    "final": "Final", "pending": "Pending", "draft": "Draft",
}


@dataclass
class LoadIssue:
    file: str
    level: str  # INFO / WARNING / ERROR
    message: str


@dataclass
class LoadResult:
    frames: List[pd.DataFrame] = field(default_factory=list)
    issues: List[LoadIssue] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_csv_files(csv_dir: str) -> List[str]:
    files = sorted(glob.glob(os.path.join(csv_dir, "*.csv")))
    return files


# ---------------------------------------------------------------------------
# Value normalization helpers
# ---------------------------------------------------------------------------

def normalize_missing_tokens(value, missing_tokens: List[str]):
    if pd.isna(value):
        return np.nan
    text = str(value).strip()
    if text == "" or text.lower() in {t.lower() for t in missing_tokens}:
        return np.nan
    return text


def normalize_whitespace(value):
    if pd.isna(value):
        return value
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_categorical_case(value):
    if pd.isna(value):
        return value
    text = str(value).strip()
    key = text.lower()
    return CATEGORICAL_CASE_MAP.get(key, text)


def normalize_camp_text(value) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value).strip()).lower()


AGE_GENDER_RE = re.compile(
    r"(?:(?P<years>\d+)\s*Y)?(?:,?\s*(?P<months>\d+)\s*M)?(?:,?\s*(?P<days>\d+)\s*D)?\s*/\s*(?P<gender>\w+)",
    re.IGNORECASE,
)


def parse_age_gender(raw: Optional[str]) -> Tuple[Optional[float], Optional[str]]:
    """Parse strings like '14 Y,8 D / Female' or '26 Y,15 D / Male' into
    (age_in_years_float, gender)."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None, None
    match = AGE_GENDER_RE.search(str(raw))
    if not match:
        return None, None
    years = int(match.group("years")) if match.group("years") else 0
    months = int(match.group("months")) if match.group("months") else 0
    days = int(match.group("days")) if match.group("days") else 0
    age_years = round(years + months / 12.0 + days / 365.0, 2)
    gender = match.group("gender")
    gender = normalize_categorical_case(gender) if gender else None
    return (age_years if (years or months or days) else None), gender


def parse_plain_age(raw: Optional[str]) -> Optional[float]:
    """Parse a standalone Age field such as '46 Y' or '29 Y,1 M,7 D'."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    text = str(raw)
    y = re.search(r"(\d+)\s*Y", text, re.IGNORECASE)
    m = re.search(r"(\d+)\s*M", text, re.IGNORECASE)
    d = re.search(r"(\d+)\s*D", text, re.IGNORECASE)
    if not (y or m or d):
        # maybe it's just a bare number
        try:
            return float(text.strip())
        except ValueError:
            return None
    years = int(y.group(1)) if y else 0
    months = int(m.group(1)) if m else 0
    days = int(d.group(1)) if d else 0
    return round(years + months / 12.0 + days / 365.0, 2)


def to_numeric(value):
    if pd.isna(value):
        return np.nan
    text = str(value).strip()
    text = re.sub(r"[^\d\.\-]", "", text)
    if text in ("", "-", "."):
        return np.nan
    try:
        return float(text)
    except ValueError:
        return np.nan


# ---------------------------------------------------------------------------
# Camp column detection
# ---------------------------------------------------------------------------

def detect_camp_column(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    """Return the first candidate column (matched case-insensitively /
    whitespace-insensitively) that is actually present in df, provided it
    contains at least one value that looks like the target camp text."""
    df_cols_by_slug = {slugify_header(c): c for c in df.columns}
    for cand in candidates:
        slug = slugify_header(cand)
        # exact canonical match (post column_normalizer) or slug match
        if cand in df.columns:
            return cand
        if slug in df_cols_by_slug:
            return df_cols_by_slug[slug]
    return None


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

WIDE_ID_COLS = {"UHID"}


def load_wide_form_csv(path: str, config: dict, issues: List[LoadIssue]) -> Optional[pd.DataFrame]:
    fname = os.path.basename(path)
    try:
        raw = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[])
    except Exception as exc:  # noqa: BLE001
        issues.append(LoadIssue(fname, "ERROR", f"Could not read file: {exc}"))
        return None

    if raw.empty:
        issues.append(LoadIssue(fname, "WARNING", "File is empty; skipped."))
        return None

    raw.columns = normalize_columns(raw.columns)
    raw = coalesce_duplicate_columns(raw)

    if "UHID" not in raw.columns:
        issues.append(LoadIssue(fname, "ERROR", "No UHID column found; file skipped (UHID is required)."))
        return None

    # Normalize missing tokens + whitespace across all text columns
    missing_tokens = config.get("missing_value_tokens", ["", "NA", "N/A", "-", "null", "None"])
    for col in raw.columns:
        raw[col] = raw[col].apply(lambda v: normalize_missing_tokens(v, missing_tokens))
        raw[col] = raw[col].apply(normalize_whitespace)

    # Drop fully-empty helper columns like "Unnamed: 39"
    raw = raw.loc[:, ~raw.columns.str.contains(r"^Unnamed", case=False, regex=True)]

    # UHID normalization
    raw = raw[raw["UHID"].notna()].copy()
    raw["UHID"] = raw["UHID"].str.strip().str.upper()

    # Age/Gender extraction
    if "Age_Gender_Raw" in raw.columns:
        parsed = raw["Age_Gender_Raw"].apply(parse_age_gender)
        raw["Age_Years"] = parsed.apply(lambda t: t[0])
        raw["Gender"] = parsed.apply(lambda t: t[1])
    elif "Age" in raw.columns:
        raw["Age_Years"] = raw["Age"].apply(parse_plain_age)

    if "Gender" in raw.columns:
        raw["Gender"] = raw["Gender"].apply(normalize_categorical_case)

    # Height / Weight numeric
    if "Height_cm" in raw.columns:
        raw["Height_cm"] = raw["Height_cm"].apply(to_numeric)
    if "Weight_kg" in raw.columns:
        raw["Weight_kg"] = raw["Weight_kg"].apply(to_numeric)

    # Standardize common Yes/No/status style categorical columns
    for col in raw.columns:
        if col in {"UHID", "Patient_Name", "Mobile_No"}:
            continue
        sample = raw[col].dropna().astype(str).str.lower().unique()
        if len(sample) and set(sample) <= {"yes", "no", "male", "female", "other", "normal",
                                             "underweight", "overweight", "obese", "good",
                                             "poor", "fair", "final", "pending", "draft"}:
            raw[col] = raw[col].apply(normalize_categorical_case)

    # Camp detection + filter
    camp_col = detect_camp_column(raw, config.get("camp_column_candidates", []))
    target_camp = normalize_camp_text(config.get("camp_filter", "Pediatric Camp"))
    if camp_col is None:
        issues.append(LoadIssue(
            fname, "WARNING",
            "No camp/program column detected among candidates "
            f"{config.get('camp_column_candidates')}. Records from this file will only be "
            "kept if their UHID also appears in a Pediatric Camp roster from another file."
        ))
        raw["_camp_match"] = False
        raw["_camp_source"] = "unknown"
    else:
        raw["_camp_source"] = camp_col
        raw["_camp_match"] = raw[camp_col].apply(normalize_camp_text) == target_camp

    raw["_source_file"] = fname
    return raw


def load_long_vitals_csv(path: str, config: dict, issues: List[LoadIssue]) -> Optional[pd.DataFrame]:
    """Vitals exports are in long format: one row per (UHID, visit, vital
    parameter). Pivot into one row per (UHID, Visit Id) with vital
    parameters as columns."""
    fname = os.path.basename(path)
    try:
        raw = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[])
    except Exception as exc:  # noqa: BLE001
        issues.append(LoadIssue(fname, "ERROR", f"Could not read file: {exc}"))
        return None

    if raw.empty:
        issues.append(LoadIssue(fname, "WARNING", "File is empty; skipped."))
        return None

    raw.columns = normalize_columns(raw.columns)
    raw = coalesce_duplicate_columns(raw)
    raw = raw.loc[:, ~raw.columns.str.contains(r"^Unnamed", case=False, regex=True)]

    if "UHID" not in raw.columns:
        issues.append(LoadIssue(fname, "ERROR", "No UHID column found; file skipped (UHID is required)."))
        return None

    missing_tokens = config.get("missing_value_tokens", [])
    for col in raw.columns:
        raw[col] = raw[col].apply(lambda v: normalize_missing_tokens(v, missing_tokens))
        raw[col] = raw[col].apply(normalize_whitespace)

    raw = raw[raw["UHID"].notna()].copy()
    raw["UHID"] = raw["UHID"].str.strip().str.upper()

    if "Age" in raw.columns:
        raw["Age_Years"] = raw["Age"].apply(parse_plain_age)
    if "Gender" in raw.columns:
        raw["Gender"] = raw["Gender"].apply(normalize_categorical_case)

    visit_key = "Visit_Id" if "Visit_Id" in raw.columns else None
    group_cols = ["UHID"] + ([visit_key] if visit_key else [])

    # Keep one demographic snapshot per group
    demo_cols = [c for c in ["UHID", "Patient_Name", "Gender", "Age_Years", "Mobile_No",
                              "Visit_Type", "Visit_Date_Time"] if c in raw.columns]
    demo = raw[demo_cols + ([visit_key] if visit_key and visit_key not in demo_cols else [])] \
        .drop_duplicates(subset=group_cols) if group_cols else raw[demo_cols].drop_duplicates()

    if "Vital_Parameter_Name" not in raw.columns or "Vital_Value" not in raw.columns:
        issues.append(LoadIssue(fname, "WARNING", "Vitals file missing parameter/value columns; skipped pivot."))
        return None

    pivot_source = raw[group_cols + ["Vital_Parameter_Name", "Vital_Value"]].dropna(subset=["Vital_Parameter_Name"])
    pivot = pivot_source.pivot_table(
        index=group_cols, columns="Vital_Parameter_Name", values="Vital_Value", aggfunc="first"
    ).reset_index()

    merged = demo.merge(pivot, on=group_cols, how="right") if demo_cols else pivot

    rename_map = {
        "Height": "Height_cm", "Weight": "Weight_kg", "Temperature": "Temperature",
        "SPO2": "SpO2", "Pulse Rate": "Pulse_Rate", "BP(Systolic)": "BP_Systolic",
        "BP(Diastolic)": "BP_Diastolic",
    }
    merged = merged.rename(columns=rename_map)

    for col in ["Height_cm", "Weight_kg", "Temperature", "SpO2", "Pulse_Rate", "BP_Systolic", "BP_Diastolic"]:
        if col in merged.columns:
            merged[col] = merged[col].apply(to_numeric)

    # No camp column exists in this export type: it will be joined against
    # the confirmed Pediatric Camp roster from other files by UHID later.
    merged["_camp_match"] = False
    merged["_camp_source"] = "unknown (vitals export has no camp field)"
    merged["_source_file"] = fname
    issues.append(LoadIssue(
        fname, "INFO",
        f"Long-format vitals file pivoted into {len(merged)} UHID/visit rows. "
        "No camp field present; these rows will be joined to the Pediatric Camp "
        "roster identified from other files, by UHID."
    ))
    return merged


def is_long_vitals_file(path: str) -> bool:
    try:
        header = pd.read_csv(path, nrows=0, dtype=str).columns
    except Exception:  # noqa: BLE001
        return False
    slugs = {slugify_header(c) for c in header}
    return "vital parameter name" in slugs and "vital value" in slugs


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def load_all_csvs(csv_dir: str, config: dict) -> LoadResult:
    result = LoadResult()
    files = discover_csv_files(csv_dir)
    if not files:
        result.issues.append(LoadIssue("(none)", "ERROR", f"No CSV files found in '{csv_dir}'."))
        return result

    logger.info(f"Found {len(files)} CSV files")
    for path in files:
        if is_long_vitals_file(path):
            df = load_long_vitals_csv(path, config, result.issues)
        else:
            df = load_wide_form_csv(path, config, result.issues)
        if df is not None and not df.empty:
            result.frames.append(df)
            logger.info(f"[INFO] Loaded {len(df):,} records from {os.path.basename(path)}")
    return result


def combine_and_filter_pediatric(frames: List[pd.DataFrame], config: dict, issues: List[LoadIssue]) -> pd.DataFrame:
    """Combine all loaded frames, establish the confirmed Pediatric Camp
    roster (UHIDs explicitly tagged with the camp filter), then join in
    supplementary rows (e.g. vitals) for UHIDs on that roster."""
    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True, sort=False)

    roster_uhids = set(combined.loc[combined["_camp_match"], "UHID"].unique())
    if not roster_uhids:
        issues.append(LoadIssue(
            "(all files)", "ERROR",
            "No records matched the Pediatric Camp filter in any file with a detected camp "
            "column. Check config/config.json 'camp_filter' and 'camp_column_candidates'."
        ))
        return pd.DataFrame()

    # Keep: rows explicitly matched, OR rows from camp-less files whose UHID is on the roster
    keep_mask = combined["_camp_match"] | combined["UHID"].isin(roster_uhids)
    pediatric = combined.loc[keep_mask].copy()

    excluded_unknown = combined.loc[~keep_mask & (combined["_camp_source"] == "unknown"), "_source_file"].value_counts()
    for fname, count in excluded_unknown.items():
        issues.append(LoadIssue(
            fname, "INFO",
            f"{count} record(s) excluded (UHID not on the confirmed Pediatric Camp roster)."
        ))

    return pediatric
