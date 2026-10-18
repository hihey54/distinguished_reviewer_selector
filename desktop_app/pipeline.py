"""Import and execute the existing five-stage HotCRP pipeline without subprocesses."""
from __future__ import annotations
from pathlib import Path
import sys


def _existing_pipeline():
    # In source checkouts, the pipeline lives in repo_root/code. PyInstaller
    # collects main.py and its imports at build time using --paths code.
    if not getattr(sys, "frozen", False):
        code = Path(__file__).resolve().parent.parent / "code"
        if str(code) not in sys.path:
            sys.path.insert(0, str(code))
    from main import build_schedule, run_pipeline
    return build_schedule, run_pipeline


def verify_schedule(min_reviews, round_starts, deadlines, grace_days):
    build_schedule, _ = _existing_pipeline()
    return build_schedule(min_reviews, round_starts, deadlines, grace_days)


def run_hotcrp(*, input_dir, output_dir, pc, prefs, log, reviews,
               thresholds, starts, deadlines, grace, min_review_words, min_comment_words):
    _, run_pipeline = _existing_pipeline()
    return run_pipeline(
        input_dir=input_dir, output_dir=output_dir,
        pc_csv=pc, preferences_csv=prefs, log_csv=log, reviews_txt=reviews,
        min_reviews=thresholds, round_starts=starts, deadlines=deadlines,
        emergency_grace_days=grace, min_review_words=min_review_words,
        min_comment_words=min_comment_words,
    )
