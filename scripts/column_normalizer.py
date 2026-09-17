"""
column_normalizer.py
---------------------
Reusable utilities for standardizing column names coming from many
differently-formatted source CSV exports (different templates, different
export tools, different whitespace/casing quirks).

This module is deliberately generic: it does NOT hard-code assumptions
about any single file. New source files with new column-name variants can
be supported by adding entries to CANONICAL_COLUMN_MAP below.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, Iterable, List

import pandas as pd


def _strip_zero_width(text: str) -> str:
    """Remove zero-width spaces and other invisible unicode characters
    that frequently sneak into exported CSV headers."""
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


def slugify_header(raw: str) -> str:
    """Turn a raw, messy header into a normalized comparison key.

    Examples:
        "  UHID "      -> "uhid"
        "Height (cm):" -> "height cm"
        "BMI Status:"  -> "bmi status"
    """
    if raw is None:
        return ""
    text = str(raw)
    text = _strip_zero_width(text)
    text = text.replace("\n", " ").replace("\r", " ")
    text = text.strip()
    # Strip pandas' auto-generated duplicate-header suffix (e.g. "Others .1")
    # so that repeated blocks of the same field within one export row map
    # back to the same canonical column and get coalesced later.
    text = re.sub(r"\.\d+$", "", text).strip()
    text = re.sub(r"[:\-_/]+", " ", text)
    text = re.sub(r"[^\w\s]", " ", text)  # drop punctuation
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


# Canonical column name -> list of slugified variants that should map to it.
# `slugify_header` is applied to both sides before comparison, so variants
# here can be written in a readable form.
CANONICAL_COLUMN_MAP: Dict[str, List[str]] = {
    "UHID": ["uhid", "u h i d", "patient uhid", "uhid no"],
    "Patient_Name": ["patient name", "name", "student name", "child name"],
    "Mobile_No": ["mobile no", "mobile number", "phone", "contact no"],
    "Age_Gender_Raw": ["age gender"],
    "Visit_Id": ["visit id admissionid", "visit id", "admission id", "visitid"],
    "Template_Name": ["template name"],
    "Status": ["status"],
    "Created_By": ["created by"],
    "Modified_By": ["modification by", "modified by"],
    "Created_DateTime": ["created date time", "created datetime"],
    "Modified_DateTime": ["modified date time", "modified datetime"],
    "Visit_Type": ["visit type"],
    "Facility": ["facility"],
    "Gender": ["gender", "sex"],
    "Age": ["age"],
    "Height_cm": ["height cm", "height"],
    "Weight_kg": ["weight kg", "weight"],
    "BMI": ["bmi"],
    "BMI_Status": ["bmi status"],
    "Temperature": ["temperature", "temp"],
    "SpO2": ["spo2", "sp o2", "oxygen saturation"],
    "Pulse_Rate": ["pulse rate", "pulse", "heart rate"],
    "BP_Systolic": ["bp systolic", "blood pressure systolic"],
    "BP_Diastolic": ["bp diastolic", "blood pressure diastolic"],
    "Vision_Right_Eye": ["right eye", "vision using snellen ishihara charts distant vision right eye"],
    "Vision_Left_Eye": ["left eye"],
    "Near_Vision": ["near vision jaeger n notation"],
    "Color_Vision": ["color vision ishihara"],
    "Hearing_Right_Ear": ["hearing rinne test right ear"],
    "Hearing_Left_Ear": ["left ear"],
    "Acute_Illness": ["acute illness past month"],
    "Chronic_Illness": ["chronic illness"],
    "Receiving_Regular_Treatment": ["receiving regular treatment"],
    "Physical_Disability": ["physical disability if any"],
    "Referral_Needed": ["referral needed"],
    "Nutritional_General_Health": ["nutritional general health"],
    "Dental_Caries": ["dental caries"],
    "Deep_Dental_Caries": ["deep dental caries"],
    "Grossly_Decayed": ["grossly decayed"],
    "Gingival_Problems": ["any gingival problems"],
    "Skeletal_Problems": ["any skeletal problems"],
    "Advised_Extraction": ["advised extraction"],
    "Advised_Scaling": ["advised scaling"],
    "Advised_Restorations": ["advised restorations"],
    "Dental_Remarks": ["remarks"],
    "Tag": ["tag"],
    "Vital_Parameter_Name": ["vital parameter name"],
    "Vital_Value": ["vital value"],
    "Vitals_DateTime": ["vitals date time"],
    "Visit_Date_Time": ["visit date time"],
    "Employer_Name": ["employer name"],
}

# Reverse index: slug -> canonical name, built once at import time.
_SLUG_TO_CANONICAL: Dict[str, str] = {}
for canonical, variants in CANONICAL_COLUMN_MAP.items():
    for variant in variants:
        _SLUG_TO_CANONICAL[slugify_header(variant)] = canonical


def normalize_columns(columns: Iterable[str]) -> List[str]:
    """Map a list of raw column headers to canonical names.

    Columns that don't match any known canonical variant are kept, but
    cleaned up (trimmed, collapsed whitespace) so they still display sensibly
    and can be inspected/added to the map later.

    Duplicate resulting names are suffixed with __2, __3, ... so information
    is never silently lost; the cleaning step later coalesces these back
    together when appropriate (e.g. duplicated BMI blocks in one export row).
    """
    seen: Dict[str, int] = {}
    result: List[str] = []
    for raw in columns:
        slug = slugify_header(raw)
        canonical = _SLUG_TO_CANONICAL.get(slug)
        if canonical is None:
            # Fall back to a readable, title-cased version of the raw header
            canonical = slug.title().replace(" ", "_") if slug else "Unnamed"
        count = seen.get(canonical, 0)
        seen[canonical] = count + 1
        if count == 0:
            result.append(canonical)
        else:
            result.append(f"{canonical}__{count + 1}")
    return result


def coalesce_duplicate_columns(df: pd.DataFrame) -> pd.DataFrame:
    """After normalize_columns(), some canonical names may appear multiple
    times (suffixed __2, __3...) because the source export repeated a block
    of fields within a single row. Coalesce these into a single column,
    preferring the first non-null/non-empty value found left-to-right.
    """
    base_names = sorted({c.split("__")[0] for c in df.columns})
    out = pd.DataFrame(index=df.index)
    for base in base_names:
        group_cols = [c for c in df.columns if c == base or c.startswith(f"{base}__")]
        if len(group_cols) == 1:
            out[base] = df[group_cols[0]]
        else:
            sub = df[group_cols].replace({"": pd.NA})
            out[base] = sub.bfill(axis=1).iloc[:, 0]
    return out
