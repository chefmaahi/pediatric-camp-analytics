#!/usr/bin/env python3
"""
etl.py
------
Main entry point for the Pediatric Camp Analytics ETL pipeline.

Usage:
    python scripts/etl.py                 # run the pipeline once
    python scripts/etl.py --watch         # run once, then watch csv/ for changes
    python scripts/etl.py --config path/to/config.json

Pipeline stages:
    1. Discover & load all CSV files in csv/
    2. Normalize columns and values
    3. Filter to Pediatric Camp records (case-insensitive, whitespace-normalized)
    4. Deduplicate & merge by UHID
    5. Recalculate pediatric BMI + BMI Status from Height/Weight/Age/Gender
    6. Validate medical fields
    7. Compute analytics (KPIs + chart data)
    8. Write CSV/JSON outputs, Excel report
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

import pandas as pd  # noqa: E402

from data_cleaning import load_all_csvs, combine_and_filter_pediatric, LoadIssue  # noqa: E402
from dedup_merge import merge_duplicate_uhids  # noqa: E402
from pediatric_bmi import recompute_bmi_columns  # noqa: E402
from validation import add_validation_flags, build_data_quality_report  # noqa: E402
from data_analysis import build_summary, build_dashboard_json  # noqa: E402
from generate_reports import write_excel_report  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("pediatric_etl")


def load_config(config_path: str) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def resolve_path(config: dict, key: str) -> str:
    value = config[key]
    return value if os.path.isabs(value) else os.path.join(PROJECT_ROOT, value)


def run_pipeline(config_path: str) -> dict:
    config = load_config(config_path)
    csv_dir = resolve_path(config, "csv_directory")
    output_dir = resolve_path(config, "output_directory")
    excel_dir = resolve_path(config, "excel_directory")
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(excel_dir, exist_ok=True)

    meta = {"errors": [], "warnings": []}

    # 1-2. Load + normalize
    load_result = load_all_csvs(csv_dir, config)
    for issue in load_result.issues:
        line = f"[{issue.level}] {issue.file}: {issue.message}"
        if issue.level == "ERROR":
            logger.error(line)
            meta["errors"].append(line)
        elif issue.level == "WARNING":
            logger.warning(line)
            meta["warnings"].append(line)
        else:
            logger.info(line)

    if not load_result.frames:
        logger.error("[ERROR] No usable CSV data was loaded. Aborting pipeline.")
        meta["success"] = False
        return meta

    total_loaded = sum(len(f) for f in load_result.frames)
    logger.info(f"[INFO] Loaded {total_loaded:,} raw records across {len(load_result.frames)} file(s)")

    # 3. Filter to Pediatric Camp
    pediatric_issues: list[LoadIssue] = []
    pediatric_df = combine_and_filter_pediatric(load_result.frames, config, pediatric_issues)
    for issue in pediatric_issues:
        line = f"[{issue.level}] {issue.file}: {issue.message}"
        (logger.error if issue.level == "ERROR" else logger.info)(line)
        if issue.level == "ERROR":
            meta["errors"].append(line)

    if pediatric_df.empty:
        logger.error("[ERROR] No Pediatric Camp records found after filtering. Aborting.")
        meta["success"] = False
        return meta

    logger.info(f"[INFO] Pediatric Camp records: {len(pediatric_df):,}")

    # 4. Deduplicate & merge by UHID
    n_unique_before = pediatric_df["UHID"].nunique()
    n_dupe_uhids = int((pediatric_df["UHID"].value_counts() > 1).sum())
    merged_df, merge_log_df = merge_duplicate_uhids(pediatric_df)
    logger.info(f"[INFO] Unique UHIDs: {n_unique_before:,}")
    logger.info(f"[INFO] Duplicate UHIDs detected: {n_dupe_uhids:,}")
    logger.info(f"[INFO] Duplicate records merged into {len(merged_df):,} master records")

    # 5. Recalculate pediatric BMI (never trust source BMI column)
    merged_df = recompute_bmi_columns(merged_df)
    logger.info("[INFO] Pediatric BMI and BMI Status recalculated from Height/Weight/Age/Gender")

    # 6. Validation
    merged_df = add_validation_flags(merged_df)
    data_quality_df = build_data_quality_report(merged_df)
    logger.info("[INFO] Missing values normalized and validation flags applied")

    # 7. Analytics
    meta.update({
        "success": True,
        "csv_files_processed": len(load_result.frames),
        "records_loaded": total_loaded,
        "pediatric_camp_records": len(pediatric_df),
        "unique_uhids": n_unique_before,
        "duplicate_uhids": n_dupe_uhids,
        "records_merged_into": len(merged_df),
    })
    summary = build_summary(merged_df, config, meta)
    dashboard_json = build_dashboard_json(merged_df, config)

    # 8. Write outputs
    export_cols = [c for c in merged_df.columns if not c.startswith("_")]
    cleaned_csv_path = os.path.join(output_dir, "cleaned_data.csv")
    merged_df[export_cols].to_csv(cleaned_csv_path, index=False)

    with open(os.path.join(output_dir, "pediatric_camp_data.json"), "w", encoding="utf-8") as f:
        json.dump(dashboard_json, f, indent=2, default=str)

    with open(os.path.join(output_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)

    data_quality_df.to_csv(os.path.join(output_dir, "data_quality_report.csv"), index=False)
    merge_log_df.to_csv(os.path.join(output_dir, "duplicate_merge_log.csv"), index=False)
    logger.info("[INFO] CSV/JSON outputs written to output/")

    excel_path = write_excel_report(merged_df, summary, data_quality_df, merge_log_df, excel_dir)
    logger.info(f"[INFO] Excel report generated: {os.path.relpath(excel_path, PROJECT_ROOT)}")

    logger.info("[INFO] Dashboard JSON generated")
    logger.info("[INFO] ETL completed successfully")

    return meta


def watch_mode(config_path: str):
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        logger.error(
            "[ERROR] watch mode requires the 'watchdog' package. "
            "Install it with: pip install watchdog"
        )
        sys.exit(1)

    config = load_config(config_path)
    csv_dir = resolve_path(config, "csv_directory")

    class Handler(FileSystemEventHandler):
        def on_any_event(self, event):
            if event.is_directory:
                return
            if not event.src_path.lower().endswith(".csv"):
                return
            logger.info(f"[INFO] Detected change: {event.src_path}. Re-running pipeline...")
            time.sleep(0.5)  # let the file finish writing
            try:
                run_pipeline(config_path)
            except Exception as exc:  # noqa: BLE001
                logger.error(f"[ERROR] Pipeline run failed: {exc}")

    logger.info(f"[INFO] Watching '{csv_dir}' for new/changed CSV files. Press Ctrl+C to stop.")
    observer = Observer()
    observer.schedule(Handler(), csv_dir, recursive=False)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


def main():
    parser = argparse.ArgumentParser(description="Pediatric Camp Analytics ETL pipeline")
    parser.add_argument("--watch", action="store_true", help="Watch csv/ for changes and re-run automatically")
    parser.add_argument("--config", default=os.path.join(PROJECT_ROOT, "config", "config.json"),
                         help="Path to config.json")
    args = parser.parse_args()

    meta = run_pipeline(args.config)

    if args.watch:
        watch_mode(args.config)
    elif not meta.get("success"):
        sys.exit(1)


if __name__ == "__main__":
    main()
