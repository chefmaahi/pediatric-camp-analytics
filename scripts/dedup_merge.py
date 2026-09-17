"""
dedup_merge.py
---------------
Detects duplicate UHIDs across the combined dataset and merges them into a
single record per UHID, preferring non-null values and logging exactly what
was merged / where conflicts occurred, so the process is fully auditable.

Never silently drops duplicate rows.
"""
from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd

NON_MERGE_COLS = {"UHID", "_camp_match", "_camp_source", "_source_file"}


def _pick_value(series: pd.Series):
    """Prefer the first non-null value (rows are assumed already ordered
    with most-recently-loaded / most-complete file first isn't guaranteed,
    so we simply prefer any non-null value; if multiple distinct non-null
    values exist, we still take the first and record the conflict
    separately)."""
    vals = [v for v in series if pd.notna(v) and str(v).strip() != ""]
    return vals[0] if vals else np.nan


def merge_duplicate_uhids(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (merged_df, merge_log_df)."""
    if df.empty:
        return df, pd.DataFrame(columns=[
            "UHID", "Number_of_duplicate_records", "Merged_columns",
            "Conflicting_columns", "Resolution"
        ])

    merge_cols = [c for c in df.columns if c not in NON_MERGE_COLS]
    log_rows = []
    merged_records = []

    for uhid, group in df.groupby("UHID", sort=False):
        n = len(group)
        merged_row = {"UHID": uhid}
        merged_cols_used = []
        conflicting_cols = []

        for col in merge_cols:
            values = group[col]
            non_null = [v for v in values if pd.notna(v) and str(v).strip() != ""]
            unique_non_null = sorted(set(str(v) for v in non_null))
            if n > 1 and len(non_null) > 0:
                merged_cols_used.append(col)
            if len(unique_non_null) > 1:
                conflicting_cols.append(col)
            merged_row[col] = _pick_value(values)

        # carry through bookkeeping columns
        merged_row["_camp_match"] = bool(group["_camp_match"].any()) if "_camp_match" in group else True
        merged_row["_source_files"] = ";".join(sorted(set(group["_source_file"]))) if "_source_file" in group else ""

        merged_records.append(merged_row)

        if n > 1:
            log_rows.append({
                "UHID": uhid,
                "Number_of_duplicate_records": n,
                "Merged_columns": ";".join(merged_cols_used) if merged_cols_used else "",
                "Conflicting_columns": ";".join(conflicting_cols) if conflicting_cols else "",
                "Resolution": "prefer_non_null (first non-null value kept per column)"
                              if not conflicting_cols else
                              "prefer_non_null; conflicting values existed, first non-null kept "
                              "(see Conflicting_columns)",
            })

    merged_df = pd.DataFrame(merged_records)
    log_df = pd.DataFrame(log_rows, columns=[
        "UHID", "Number_of_duplicate_records", "Merged_columns",
        "Conflicting_columns", "Resolution"
    ])
    return merged_df, log_df
