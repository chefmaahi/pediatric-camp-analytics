"""
pediatric_bmi.py
-----------------
Recalculates BMI directly from Height + Weight, and classifies it using a
PEDIATRIC (age- and sex-specific) BMI-for-age reference instead of fixed
adult BMI thresholds.

IMPORTANT / METHOD DOCUMENTATION (see also README.md):

This module uses an approximate, integer-age, sex-specific BMI-for-age
percentile-band table (5th / 85th / 95th percentile cutoffs), constructed
in the style of the CDC (2000) / WHO growth-chart references, covering
ages 2-19 years. It is NOT a full LMS (Lambda-Mu-Sigma) percentile
calculator, which is what clinical growth-chart software uses to compute
an exact BMI-for-age percentile for a fractional age. The band table here
is intended to give a clear, auditable, reasonable pediatric classification
for camp/dashboard reporting purposes.

For clinical decisions, refer to the official WHO Child Growth Standards
/ WHO Growth Reference 5-19 years, or the CDC growth charts, using an
LMS-based calculator.

Classification bands (standard pediatric convention):
    BMI < 5th percentile             -> Underweight
    5th percentile <= BMI < 85th     -> Normal
    85th percentile <= BMI < 95th    -> Overweight
    BMI >= 95th percentile           -> Obese

Records for children outside the supported age range (2-19 years) are
flagged "Needs Review" rather than guessed.
"""
from __future__ import annotations

from typing import Optional, Tuple

import pandas as pd

REFERENCE_NAME = (
    "Approximate CDC/WHO-style sex-specific BMI-for-age percentile bands "
    "(5th/85th/95th), integer ages 2-19. See scripts/pediatric_bmi.py and "
    "README.md for full method and limitations."
)

MIN_AGE = 2
MAX_AGE = 19

# age -> (p5, p85, p95) in kg/m^2
BOYS_BMI_BANDS = {
    2: (14.7, 17.1, 18.0), 3: (14.3, 16.9, 17.9), 4: (14.0, 16.8, 17.9),
    5: (13.8, 16.8, 18.1), 6: (13.7, 17.0, 18.4), 7: (13.7, 17.4, 19.1),
    8: (13.8, 17.9, 19.9), 9: (13.9, 18.6, 20.9), 10: (14.1, 19.3, 22.0),
    11: (14.4, 20.1, 23.0), 12: (14.8, 20.9, 24.0), 13: (15.2, 21.6, 24.9),
    14: (15.7, 22.2, 25.8), 15: (16.2, 22.8, 26.6), 16: (16.6, 23.3, 27.3),
    17: (17.0, 23.8, 27.8), 18: (17.3, 24.2, 28.2), 19: (17.6, 24.6, 28.6),
}

GIRLS_BMI_BANDS = {
    2: (14.5, 17.0, 18.0), 3: (14.1, 16.9, 18.0), 4: (13.8, 16.9, 18.2),
    5: (13.6, 17.1, 18.6), 6: (13.5, 17.3, 19.2), 7: (13.5, 17.8, 20.0),
    8: (13.7, 18.3, 21.0), 9: (13.9, 19.0, 22.1), 10: (14.2, 19.7, 23.2),
    11: (14.6, 20.5, 24.2), 12: (15.1, 21.3, 25.1), 13: (15.6, 22.0, 25.9),
    14: (16.1, 22.6, 26.6), 15: (16.6, 23.1, 27.2), 16: (17.0, 23.6, 27.8),
    17: (17.3, 24.0, 28.3), 18: (17.6, 24.4, 28.8), 19: (17.8, 24.8, 29.2),
}


def calculate_bmi(height_cm: Optional[float], weight_kg: Optional[float]) -> Optional[float]:
    """Return BMI rounded to 1 decimal, or None if inputs are missing/invalid."""
    if height_cm is None or weight_kg is None:
        return None
    try:
        h_cm = float(height_cm)
        w_kg = float(weight_kg)
    except (TypeError, ValueError):
        return None
    if h_cm <= 0 or w_kg <= 0:
        return None
    # Sanity bounds: plausible pediatric height range 40-200 cm
    if not (40 <= h_cm <= 200):
        return None
    h_m = h_cm / 100.0
    bmi = w_kg / (h_m ** 2)
    return round(bmi, 1)


def classify_bmi(bmi: Optional[float], age_years: Optional[float], gender: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Classify BMI using the pediatric age/sex band table.

    Returns (BMI_Status, BMI_Percentile_Band) where BMI_Percentile_Band is
    a human-readable band such as "85th-95th" (this table gives bands, not
    an exact percentile — see module docstring).
    """
    if bmi is None:
        return None, None
    if age_years is None or gender is None:
        return "Needs Review", None

    try:
        age_int = int(round(float(age_years)))
    except (TypeError, ValueError):
        return "Needs Review", None

    if age_int < MIN_AGE or age_int > MAX_AGE:
        return "Needs Review", None

    gender_norm = str(gender).strip().lower()
    if gender_norm.startswith("m"):
        bands = BOYS_BMI_BANDS
    elif gender_norm.startswith("f"):
        bands = GIRLS_BMI_BANDS
    else:
        return "Needs Review", None

    age_int = min(max(age_int, MIN_AGE), MAX_AGE)
    p5, p85, p95 = bands[age_int]

    if bmi < p5:
        status = "Underweight"
        band = f"<5th"
    elif bmi < p85:
        status = "Normal"
        band = "5th-85th"
    elif bmi < p95:
        status = "Overweight"
        band = "85th-95th"
    else:
        status = "Obese"
        band = ">=95th"
    return status, band


def recompute_bmi_columns(df: pd.DataFrame,
                           height_col: str = "Height_cm",
                           weight_col: str = "Weight_kg",
                           age_col: str = "Age_Years",
                           gender_col: str = "Gender") -> pd.DataFrame:
    """Add/replace BMI, BMI_Status, BMI_Percentile_Band, BMI_Reference columns
    on df using calculate_bmi / classify_bmi, driven by Height + Weight + Age
    + Gender (never trusting any pre-existing BMI/BMI Status column)."""
    df = df.copy()
    bmis, statuses, bands = [], [], []
    for _, row in df.iterrows():
        bmi = calculate_bmi(row.get(height_col), row.get(weight_col))
        status, band = classify_bmi(bmi, row.get(age_col), row.get(gender_col))
        bmis.append(bmi)
        statuses.append(status)
        bands.append(band)
    df["BMI"] = bmis
    df["BMI_Status"] = statuses
    df["BMI_Percentile_Band"] = bands
    df["BMI_Reference"] = REFERENCE_NAME
    return df
