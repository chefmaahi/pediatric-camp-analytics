"""
validation.py
--------------
Validates key numerical medical fields against plausible pediatric ranges
and produces a Data_Quality_Flag per record (Valid / Missing / Potentially
Invalid / Needs Review). Never deletes questionable values -- the original
value is always retained alongside the flag.
"""
from __future__ import annotations

import pandas as pd

DEFAULT_RANGES = {
    "Age_Years": (0, 19),
    "Height_cm": (40, 200),
    "Weight_kg": (5, 120),
    "Temperature": (90, 106),
    "SpO2": (70, 100),
    "Pulse_Rate": (40, 180),
}


def flag_value(value, low, high) -> str:
    if pd.isna(value):
        return "Missing"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "Needs Review"
    if low <= v <= high:
        return "Valid"
    return "Potentially Invalid"


def add_validation_flags(df: pd.DataFrame, ranges: dict | None = None) -> pd.DataFrame:
    df = df.copy()
    ranges = ranges or DEFAULT_RANGES
    for col, (low, high) in ranges.items():
        if col in df.columns:
            df[f"{col}_Quality_Flag"] = df[col].apply(lambda v: flag_value(v, low, high))
    return df


def build_data_quality_report(df: pd.DataFrame) -> pd.DataFrame:
    """One row per column: total records, missing count/%, unique values, dtype."""
    rows = []
    total = len(df)
    for col in df.columns:
        if col.startswith("_"):
            continue
        missing = int(df[col].isna().sum())
        rows.append({
            "Column": col,
            "Total_Records": total,
            "Missing_Values": missing,
            "Missing_Percentage": round(100 * missing / total, 2) if total else 0,
            "Unique_Values": int(df[col].nunique(dropna=True)),
            "Data_Type": str(df[col].dtype),
        })
    return pd.DataFrame(rows)
