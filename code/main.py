"""Run the local HotCRP reviewer-data extraction and metrics pipeline.

Put this file beside:
    pc_extraction.py, bids_extraction.py, log_extraction.py,
    reviews_extraction.py, pc_metrics.py

Put the FOUR downloaded HotCRP exports in data/:
    - PC roster CSV (typically *pcinfo*.csv)
    - paper preferences / bids CSV (typically *allprefs*.csv)
    - activity log CSV (typically *log*.csv)
    - submissions/reviews/comments text export (typically *reviews*.txt)

Edit the settings below, especially MIN_REVIEWS, ROUND_STARTS and DEADLINES,
then, from the project root, run:
    python code/main.py

OR leave the settings unchanged and supply values via command-line arguments:
    python code/main.py --min-reviews 2 \
        --round-starts 2026-07-31T00:00:00-12:00 \
        --deadlines 2026-08-25T23:59:59-12:00

The dates in the example are NOT verified venue dates.
Python 3.10+; standard library only; everything runs locally and deterministically.
"""

from __future__ import annotations

import argparse
import fnmatch
from pathlib import Path
from typing import Any

from pc_extraction import extract_pc_topics
from bids_extraction import extract_bids
from log_extraction import extract_log_data
from reviews_extraction import extract_review_details
from pc_metrics import build_schedule, generate_pc_metrics


# ============================================================================
# EDITABLE SETTINGS -- CLI arguments override these values when supplied.
# Paths are relative to the working directory (usually the project root).
# ============================================================================

INPUT_DIR = "data\\aisec26"
OUTPUT_DIR = "output"

# Set a filename/path to use a specific export, or leave None to locate it
# automatically by its filename under INPUT_DIR.
PC_CSV = "aisec26-pcinfo.csv"            # e.g. "aisec26-pcinfo.csv"
PREFERENCES_CSV = "aisec26-allprefs.csv"   # e.g. "aisec26-allprefs.csv"
LOG_CSV = "aisec26-log.csv"           # e.g. "aisec26-log.csv"
REVIEWS_TXT = "aisec26-reviews.txt"       # e.g. "aisec26-reviews.txt"

# REQUIRED: real venue reviewing schedule; one item per reviewing round.
# Review thresholds must be strictly increasing. Times must include timezone.
MIN_REVIEWS = [2]        # e.g. [2, 4, 5] for three discussion windows
ROUND_STARTS = ["2026-07-31T00:00:00-12:00"]        # e.g. ["2026-07-31T00:00:00-12:00"]
DEADLINES = ["2026-08-25T23:59:59-12:00"]           # e.g. ["2026-08-25T23:59:59-12:00"]

# Discussion/timeliness parameters. Adjust for your venue.
EMERGENCY_GRACE_DAYS = 2.0
MIN_REVIEW_WORDS = 50
MIN_COMMENT_WORDS = 15

# ============================================================================


# Filename patterns are checked in order, from most to least specific.
# If more than one export matches the same pattern, selection fails safely.
EXPORT_PATTERNS: dict[str, tuple[str, ...]] = {
    "PC roster CSV": ("*pcinfo*.csv", "*pc-info*.csv", "*pc_info*.csv"),
    "preferences CSV": ("*allprefs*.csv", "*prefs*.csv", "*preference*.csv"),
    "activity log CSV": ("*-log.csv", "*log*.csv"),
    "reviews TXT": ("*-reviews.txt", "*reviews*.txt"),
}


def input_path(value: str | Path | None, directory: Path, label: str) -> Path:
    """Find a downloaded export or resolve an explicit filename/path."""
    if value is not None:
        given = Path(value).expanduser()
        options = [given]
        if not given.is_absolute():
            options.append(directory / given)
        for candidate in options:
            if candidate.is_file():
                return candidate.resolve()
        raise FileNotFoundError(
            f"{label} not found: {value!s}. Checked the provided path "
            f"and inside {directory}."
        )

    if not directory.is_dir():
        raise FileNotFoundError(
            f"Input directory {directory} does not exist. "
            "Create it and put the four HotCRP exports there, or use --input-dir."
        )

    files = sorted((p for p in directory.iterdir() if p.is_file()),
                   key=lambda p: p.name.casefold())
    for pattern in EXPORT_PATTERNS[label]:
        matches = [p for p in files
                   if fnmatch.fnmatchcase(p.name.casefold(), pattern.casefold())]
        if len(matches) == 1:
            return matches[0].resolve()
        if len(matches) > 1:
            filenames = ", ".join(p.name for p in matches)
            raise ValueError(
                f"Multiple candidates for {label} in {directory}: {filenames}. "
                "Specify the correct file using the corresponding CLI option "
                "or editable setting."
            )
    raise FileNotFoundError(
        f"Cannot find {label} in {directory}. "
        "Supply its filename/path via the CLI or the editable settings."
    )


def run_pipeline(
    *,
    input_dir: str | Path = INPUT_DIR,
    output_dir: str | Path = OUTPUT_DIR,
    pc_csv: str | Path | None = PC_CSV,
    preferences_csv: str | Path | None = PREFERENCES_CSV,
    log_csv: str | Path | None = LOG_CSV,
    reviews_txt: str | Path | None = REVIEWS_TXT,
    min_reviews: list[int] | None = None,
    round_starts: list[str] | None = None,
    deadlines: list[str] | None = None,
    emergency_grace_days: float = EMERGENCY_GRACE_DAYS,
    min_review_words: int = MIN_REVIEW_WORDS,
    min_comment_words: int = MIN_COMMENT_WORDS,
) -> dict[str, Path]:
    """Generate topic/bid annotations, timelines, and per-PC metrics.

    Returns named output paths. Does not call any external service or AI.
    """
    min_reviews = MIN_REVIEWS if min_reviews is None else min_reviews
    round_starts = ROUND_STARTS if round_starts is None else round_starts
    deadlines = DEADLINES if deadlines is None else deadlines

    if not round_starts or not deadlines:
        raise ValueError(
            "Reviewing round dates are required. Edit ROUND_STARTS and DEADLINES "
            "at the top of main.py, or pass --round-starts and --deadlines."
        )

    # Validate the entire venue schedule BEFORE creating any output files.
    build_schedule(min_reviews, round_starts, deadlines, emergency_grace_days)
    if min_review_words < 0 or min_comment_words < 0:
        raise ValueError("Minimum review/comment wordcounts must be nonnegative")

    source = Path(input_dir).expanduser()
    target = Path(output_dir).expanduser()

    # Resolve every required source before beginning the pipeline.
    pc = input_path(pc_csv, source, "PC roster CSV")
    prefs = input_path(preferences_csv, source, "preferences CSV")
    log = input_path(log_csv, source, "activity log CSV")
    reviews = input_path(reviews_txt, source, "reviews TXT")

    if len({p.resolve() for p in (pc, prefs, log, reviews)}) != 4:
        raise ValueError("The four HotCRP inputs must refer to distinct files")
    if source.resolve() == target.resolve():
        raise ValueError("INPUT_DIR and OUTPUT_DIR must be different folders")

    target.mkdir(parents=True, exist_ok=True)
    print("HotCRP input files:", flush=True)
    for label, path in (("PC roster", pc), ("Preferences", prefs),
                        ("Activity log", log), ("Reviews/comments", reviews)):
        print(f"  {label}: {path}", flush=True)

    print("\n[1/5] Checking PC topic completion...", flush=True)
    topics_file = extract_pc_topics(pc, target)

    print("[2/5] Collecting PC bids...", flush=True)
    topics_bids_file = extract_bids(topics_file, prefs, target)

    print("[3/5] Extracting activity log timeline...", flush=True)
    log_json_file = extract_log_data(pc, log, target)

    print("[4/5] Matching review/comment texts to the log...", flush=True)
    reviews_json_file = extract_review_details(reviews, log_json_file, target)

    print("[5/5] Computing per-PC metrics...", flush=True)
    metrics_file, metrics_summary_file = generate_pc_metrics(
        source_folder=target,
        min_reviews=min_reviews,
        round_starts=round_starts,
        deadlines=deadlines,
        emergency_grace_days=emergency_grace_days,
        min_review_words=min_review_words,
        min_comment_words=min_comment_words,
        output_folder=target,
        pc_filename=topics_bids_file,
        reviews_json_filename=reviews_json_file,
    )

    outputs = {
        "pc_topics": topics_file,
        "pc_topics_bids": topics_bids_file,
        "log_timeline": log_json_file,
        "review_details": reviews_json_file,
        "pc_metrics": metrics_file,
        "pc_metrics_summary": metrics_summary_file,
    }

    print("\nCompleted. Main outputs:", flush=True)
    for name in ("pc_metrics", "pc_metrics_summary"):
        print(f"  {name}: {outputs[name]}", flush=True)
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run five local HotCRP extraction/metrics modules.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--input-dir", default=INPUT_DIR,
                        help="Folder containing four downloaded HotCRP files")
    parser.add_argument("-o", "--output-dir", default=OUTPUT_DIR,
                        help="Destination directory for ALL outputs")
    parser.add_argument("--pc", default=PC_CSV, help="PC roster CSV path or filename")
    parser.add_argument("--prefs", default=PREFERENCES_CSV,
                        help="Bids/preferences CSV path or filename")
    parser.add_argument("--log", default=LOG_CSV, help="Activity log CSV path or filename")
    parser.add_argument("--reviews", default=REVIEWS_TXT,
                        help="All-reviews TXT path or filename")
    parser.add_argument("--min-reviews", nargs="+", type=int, default=None,
                        help="Increasing review thresholds, e.g. 2 4 5")
    parser.add_argument("--round-starts", nargs="+", default=None,
                        help="ISO 8601 start times with UTC offsets, one per round")
    parser.add_argument("--deadlines", nargs="+", default=None,
                        help="ISO 8601 deadlines with UTC offsets, one per round")
    parser.add_argument("--emergency-grace-days", type=float,
                        default=EMERGENCY_GRACE_DAYS)
    parser.add_argument("--min-review-words", type=int, default=MIN_REVIEW_WORDS)
    parser.add_argument("--min-comment-words", type=int, default=MIN_COMMENT_WORDS)
    args = parser.parse_args()
    try:
        run_pipeline(
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            pc_csv=args.pc,
            preferences_csv=args.prefs,
            log_csv=args.log,
            reviews_txt=args.reviews,
            min_reviews=args.min_reviews,
            round_starts=args.round_starts,
            deadlines=args.deadlines,
            emergency_grace_days=args.emergency_grace_days,
            min_review_words=args.min_review_words,
            min_comment_words=args.min_comment_words,
        )
    except (ValueError, FileNotFoundError, NotADirectoryError) as exc:
        parser.exit(2, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
