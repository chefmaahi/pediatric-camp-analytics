"""
data_analysis.py
-----------------
Computes KPI cards and chart-ready aggregates from the cleaned master
dataset. Adapts automatically to whichever columns are actually present  --
a KPI or chart is only produced if the underlying data supports it.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict

import numpy as np
import pandas as pd


def _age_group(age: float | None) -> str | None:
    if age is None or (isinstance(age, float) and np.isnan(age)):
        return None
    if age <= 5:
        return "0-5"
    if age <= 10:
        return "6-10"
    if age <= 15:
        return "11-15"
    if age <= 18:
        return "16-18"
    return "19+"


def build_kpis(df: pd.DataFrame) -> Dict[str, Any]:
    kpis: Dict[str, Any] = {"Total_Pediatric_Students": int(len(df))}

    if "Gender" in df.columns:
        counts = df["Gender"].value_counts(dropna=True)
        kpis["Male_Students"] = int(counts.get("Male", 0))
        kpis["Female_Students"] = int(counts.get("Female", 0))
        other = int(len(df)) - int(counts.get("Male", 0)) - int(counts.get("Female", 0))
        kpis["Other_Unknown_Gender"] = max(other, 0)

    if "Age_Years" in df.columns and df["Age_Years"].notna().any():
        kpis["Average_Age"] = round(float(df["Age_Years"].dropna().mean()), 1)

    if "BMI" in df.columns and df["BMI"].notna().any():
        kpis["Average_BMI"] = round(float(df["BMI"].dropna().mean()), 1)

    if "BMI_Status" in df.columns:
        bmi_counts = df["BMI_Status"].value_counts(dropna=True)
        kpis["Normal_BMI_Count"] = int(bmi_counts.get("Normal", 0))
        kpis["Underweight_Count"] = int(bmi_counts.get("Underweight", 0))
        kpis["Overweight_Count"] = int(bmi_counts.get("Overweight", 0))
        kpis["Obese_Count"] = int(bmi_counts.get("Obese", 0))

    kpis["Students_Screened"] = int(len(df))

    if "Dental_Caries" in df.columns:
        kpis["Dental_Screening_Count"] = int(df["Dental_Caries"].notna().sum() +
                                              (df["Dental_Caries"].isna() & df.get("Dental_Remarks", pd.Series(dtype=object)).notna()).sum())
    if "Vision_Right_Eye" in df.columns or "Vision_Left_Eye" in df.columns:
        cols = [c for c in ["Vision_Right_Eye", "Vision_Left_Eye"] if c in df.columns]
        kpis["Vision_Screening_Count"] = int(df[cols].notna().any(axis=1).sum())

    if "Referral_Needed" in df.columns:
        kpis["Referral_Needed_Count"] = int((df["Referral_Needed"] == "Yes").sum())

    return kpis


def build_chart_data(df: pd.DataFrame) -> Dict[str, Any]:
    charts: Dict[str, Any] = {}

    if "Gender" in df.columns:
        vc = df["Gender"].fillna("Not Available").value_counts()
        charts["gender_distribution"] = {"labels": vc.index.tolist(), "values": [int(v) for v in vc.values]}

    if "Age_Years" in df.columns:
        groups = df["Age_Years"].apply(_age_group)
        order = ["0-5", "6-10", "11-15", "16-18", "19+"]
        vc = groups.value_counts()
        vc = vc.reindex([o for o in order if o in vc.index])
        charts["age_group_distribution"] = {"labels": vc.index.tolist(), "values": [int(v) for v in vc.values]}

    if "BMI_Status" in df.columns:
        order = ["Underweight", "Normal", "Overweight", "Obese", "Needs Review"]
        vc = df["BMI_Status"].fillna("Not Available").value_counts()
        vc = vc.reindex([o for o in order if o in vc.index]).dropna()
        charts["bmi_status_distribution"] = {"labels": vc.index.tolist(), "values": [int(v) for v in vc.values]}

    screening_labels, screening_values = [], []
    if "Dental_Caries" in df.columns or "Dental_Remarks" in df.columns:
        cols = [c for c in ["Dental_Caries", "Dental_Remarks"] if c in df.columns]
        screening_labels.append("Dental")
        screening_values.append(int(df[cols].notna().any(axis=1).sum()))
    if "Vision_Right_Eye" in df.columns or "Vision_Left_Eye" in df.columns:
        cols = [c for c in ["Vision_Right_Eye", "Vision_Left_Eye"] if c in df.columns]
        screening_labels.append("Vision")
        screening_values.append(int(df[cols].notna().any(axis=1).sum()))
    if "Height_cm" in df.columns and "Weight_kg" in df.columns:
        screening_labels.append("Height/Weight")
        screening_values.append(int((df["Height_cm"].notna() & df["Weight_kg"].notna()).sum()))
    if screening_labels:
        charts["screening_statistics"] = {"labels": screening_labels, "values": screening_values}

    if "Created_DateTime" in df.columns or "Visit_Date_Time" in df.columns:
        date_col = "Created_DateTime" if "Created_DateTime" in df.columns else "Visit_Date_Time"
        try:
            parsed = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True, format="mixed")
            dates = parsed.dt.date.dropna()
            vc = dates.value_counts().sort_index()
            charts["camp_date_distribution"] = {
                "labels": [str(d) for d in vc.index],
                "values": [int(v) for v in vc.values],
            }
        except Exception:  # noqa: BLE001
            pass

    return charts


def build_summary(df: pd.DataFrame, config: dict, meta: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "dashboard_title": config.get("dashboard_title"),
        "dashboard_subtitle": config.get("dashboard_subtitle"),
        "kpis": build_kpis(df),
        "charts": build_chart_data(df),
        "pipeline_meta": meta,
    }


PRIVATE_COLUMNS = {"Patient_Name", "Mobile_No", "Email"}


def build_dashboard_json(df: pd.DataFrame, config: dict) -> Dict[str, Any]:
    """Aggregated + (optionally stripped) patient-level records for the
    website. When privacy_mode is true, direct identifiers are removed and
    the UHID itself is not exposed on the public dashboard payload."""
    privacy_mode = config.get("privacy_mode", True)

    public_cols = [c for c in df.columns if not c.startswith("_")]
    if privacy_mode:
        public_cols = [c for c in public_cols if c not in PRIVATE_COLUMNS and c != "UHID"]

    records = df[public_cols].replace({np.nan: None}).to_dict(orient="records")
    return {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "privacy_mode": privacy_mode,
        "record_count": len(records),
        "records": records,
    }
