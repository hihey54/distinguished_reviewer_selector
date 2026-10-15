"""Create deterministic per-PC reviewer metrics from local HotCRP extraction outputs.

Requires Python 3.10+; standard library only. No network, AI, or external calls.

The input directory must contain exactly one *topics_bids.csv (preferred),
plus exactly one *reviews_extracted.json. You may override auto-discovery with
--pc-file / --reviews-json.

Examples:
    python pc_metrics.py output --min-reviews 2 3 \
        --round-starts 2026-07-31T00:00:00-12:00 2026-08-29T00:00:00-12:00 \
        --deadlines 2026-08-25T23:59:59-12:00 2026-09-07T23:59:59-12:00 \
        --emergency-grace-days 2 --min-review-words 100 \
        --min-comment-words 30 -o output

All date/time strings must be ISO 8601 with an explicit UTC offset.
The examples are illustrative, not actual venue schedule dates.

Public Python API:
    generate_pc_metrics(source_folder, min_reviews, round_starts, deadlines,
                        emergency_grace_days, min_review_words,
                        min_comment_words, output_folder=None,
                        pc_filename=None, reviews_json_filename=None)
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DAY_SECONDS = 86400
METRIC_LIST_COLUMNS = (
    "reviews", "assignments", "time-to-review", "delays",
    "review_length", "hotcrp_length", "review_details",
    "discussion_initiator",
)
COMMENT_COLUMNS = (
    "comments", "comments_length", "extra_comments", "extra_comments_length"
)


def email_key(value: str | None) -> str:
    return (value or "").strip().casefold()


def date_time(value: str, label: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"Invalid {label}: {value!r}; expected ISO 8601 with timezone") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(
            f"Timezone required for {label}: {value!r}; "
            "use e.g. 2026-08-25T23:59:59-12:00"
        )
    return dt.astimezone(timezone.utc)


def optional_time(value: str | None, label: str) -> datetime | None:
    return date_time(value, label) if value else None


def iso_utc(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z") if value else None


def signed_days(delta_seconds: float) -> int:
    """Round fractional days away from zero, preserving negative earliness."""
    if delta_seconds == 0:
        return 0
    return (1 if delta_seconds > 0 else -1) * math.ceil(abs(delta_seconds) / DAY_SECONDS)


def json_cell(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=False, separators=(",", ":"))


def choose_file(folder: Path, patterns: tuple[str, ...], role: str) -> Path:
    for pattern in patterns:
        options = sorted(p for p in folder.glob(pattern) if p.is_file())
        if len(options) == 1:
            return options[0]
        if len(options) > 1:
            raise ValueError(
                f"Multiple candidates for {role} matching {pattern!r}: "
                + ", ".join(p.name for p in options)
                + "; specify an explicit file path."
            )
    raise FileNotFoundError(
        f"Cannot locate {role} in {folder}. Looked for: {', '.join(patterns)}."
    )


def load_input_files(
    source_folder: str | Path,
    pc_filename: str | Path | None,
    reviews_json_filename: str | Path | None,
) -> tuple[Path, Path, list[dict[str, str]], dict[str, Any]]:
    folder = Path(source_folder).expanduser()
    if not folder.is_dir():
        raise NotADirectoryError(f"Input directory does not exist: {folder}")
    pc_path = (
        Path(pc_filename).expanduser() if pc_filename else
        choose_file(folder, ("*_topics_bids.csv", "*_bids.csv"), "PC CSV with topics and bids")
    )
    review_path = (
        Path(reviews_json_filename).expanduser() if reviews_json_filename else
        choose_file(folder, ("*reviews_extracted.json",), "enriched reviews JSON")
    )
    with pc_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"Missing CSV header in {pc_path}")
        required = {"email", "given_name", "family_name", "bids"}
        missing = required - set(reader.fieldnames)
        if missing:
            raise ValueError(
                f"PC CSV {pc_path} lacks required columns: {sorted(missing)}"
            )
        rows = list(reader)

        # Handle PC files where bids_extraction.py was run directly on the
        # original HotCRP roster, skipping pc_extraction.py. The raw topic
        # fields are still present, so the same completion rule is reliable.
        if "topics?" not in reader.fieldnames:
            topic_columns = [
                column for column in reader.fieldnames
                if column and column.strip().casefold().startswith("topic:")
            ]
            if not topic_columns:
                raise ValueError(
                    f"PC CSV {pc_path} has neither a 'topics?' column nor "
                    "original 'topic:' columns. Cannot distinguish an "
                    "export without topics from incomplete input. "
                    "Supply a PC CSV produced by pc_extraction.py."
                )
            for row in rows:
                row["topics?"] = str(all(
                    (value := row.get(column)) is not None
                    and value.strip().casefold() not in {"", "none"}
                    for column in topic_columns
                ))
            print(
                f"Info: '{pc_path.name}' lacks 'topics?'; inferred completion "
                f"from {len(topic_columns)} original topic columns.",
                file=sys.stderr,
            )
    seen: set[str] = set()
    for i, row in enumerate(rows, start=2):
        if None in row:
            raise ValueError(f"Unexpected extra CSV fields at row {i} of {pc_path}")
        email = email_key(row.get("email"))
        if not email or email in seen:
            raise ValueError(f"Missing/duplicate PC email {email!r} at row {i}")
        seen.add(email)
    with review_path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data.get("papers"), dict) or not isinstance(data.get("pc_members"), dict):
        raise ValueError("Expected a JSON object from reviews_extraction.py with papers/pc_members")
    if data.get("schema_version") != 1:
        raise ValueError("This script currently supports reviews_extraction.py schema_version=1")
    return pc_path, review_path, rows, data


def parse_bids(text: str | None, email: str) -> list[int]:
    try:
        bids = json.loads((text or "").strip())
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON bids list for {email}") from exc
    if not isinstance(bids, list) or any(isinstance(i, bool) or not isinstance(i, int) for i in bids):
        raise ValueError(f"Bids must be a JSON list of paper IDs for {email}")
    return sorted(set(bids))


def parse_topics(text: str | None, email: str) -> str:
    value = (text or "").strip().casefold()
    if value in ("true", "false"):
        return value.capitalize()
    if value in ("n/a", "na", "not applicable"):
        return "N/A"
    raise ValueError(f"Invalid 'topics?' for {email}: {text!r}; expected True, False or N/A")


def build_schedule(
    min_reviews: list[int],
    round_starts: list[str],
    deadlines: list[str],
    emergency_grace_days: float,
) -> list[dict[str, Any]]:
    if not min_reviews or len(min_reviews) != len(round_starts) or len(min_reviews) != len(deadlines):
        raise ValueError("min_reviews, round_starts, and deadlines must have equal, nonzero lengths")
    if any(isinstance(i, bool) or not isinstance(i, int) or i <= 0 for i in min_reviews):
        raise ValueError("min_reviews must consist of positive integers")
    if min_reviews != sorted(set(min_reviews)):
        raise ValueError("min_reviews must be strictly increasing, e.g. [2, 4, 5]")
    if not math.isfinite(emergency_grace_days) or emergency_grace_days < 0:
        raise ValueError("emergency_grace_days must be finite and nonnegative")
    starts = [date_time(s, f"round start {i + 1}") for i, s in enumerate(round_starts)]
    ends = [date_time(s, f"round deadline {i + 1}") for i, s in enumerate(deadlines)]
    for i, (start, deadline) in enumerate(zip(starts, ends)):
        if start >= deadline:
            raise ValueError(f"Round {i + 1}: start must precede deadline")
        if i and starts[i - 1] >= start:
            raise ValueError("Round starts must be strictly increasing")
        if i + 1 < len(starts) and deadline >= starts[i + 1]:
            raise ValueError(f"Round {i + 1}: deadline must precede next round start")
    return [
        {
            "index": i + 1,
            "min_reviews": min_reviews[i],
            "start": start,
            "deadline": deadline,
            "next_start": starts[i + 1] if i + 1 < len(starts) else None,
            "grace_cutoff": deadline - timedelta(days=emergency_grace_days),
        }
        for i, (start, deadline) in enumerate(zip(starts, ends))
    ]


def round_for_assignment(assigned: datetime, schedule: list[dict[str, Any]]) -> dict[str, Any]:
    """The last round that has started by assignment time; pre-round -> round 1."""
    found = schedule[0]
    for rnd in schedule:
        if assigned >= rnd["start"]:
            found = rnd
        else:
            break
    return found


def review_timeliness(
    assigned_at: str | None,
    submitted_at: str | None,
    schedule: list[dict[str, Any]],
    warnings: list[str],
    description: str,
) -> dict[str, Any]:
    assigned = optional_time(assigned_at, f"assignment of {description}")
    submitted = optional_time(submitted_at, f"first submission of {description}")
    if assigned is None or submitted is None:
        warnings.append(f"{description}: missing assignment/submission timestamp; duration unavailable")
        return {
            "round": None, "round_start": None, "deadline": None,
            "effective_deadline": None, "late_assignment": None,
            "emergency_open_ended": None,
            "assigned_before_round_start": None,
            "time_to_review_days": None, "time_to_review_days_exact": None,
            "delay_days": None, "delay_days_exact": None,
        }
    round_info = round_for_assignment(assigned, schedule)
    if submitted < assigned:
        warnings.append(f"{description}: submitted before assigned; cannot compute duration")
        time_days = None
        exact_days = None
    else:
        time_days = math.ceil((submitted - assigned).total_seconds() / DAY_SECONDS)
        exact_days = round((submitted - assigned).total_seconds() / DAY_SECONDS, 6)
    before_start = assigned < round_info["start"]
    if before_start:
        warnings.append(f"{description}: assignment predates first configured round start")

    # Assignments within the grace window (including *after* the ordinary
    # deadline) are emergency assignments. A configured next round supplies
    # a hard cutoff: its start time. With no next round, no emergency cutoff
    # was provided by the user; an emergency review cannot be called late
    # relative to the deadline that preceded its assignment.
    late_assignment = assigned >= round_info["grace_cutoff"]
    next_start = round_info["next_start"]
    emergency_open_ended = late_assignment and next_start is None
    effective_due = (
        next_start if late_assignment and next_start is not None else
        None if emergency_open_ended else round_info["deadline"]
    )

    if emergency_open_ended and submitted > round_info["deadline"]:
        # No user-configured cutoff exists for late final-round assignments.
        # Their post-deadline submissions are excused, not measured against
        # the deadline that occurred before they were assigned.
        delay = 0
        delay_exact = 0.0
    elif late_assignment and next_start is not None and submitted > round_info["deadline"] and submitted <= next_start:
        # Emergency submission after the regular deadline, before next round.
        delay = 0
        delay_exact = 0.0
    else:
        # Before the original deadline, always use that deadline to measure
        # early submissions, even for emergency assignments.
        reference = (next_start if late_assignment and next_start is not None
                     and submitted > round_info["deadline"]
                     else round_info["deadline"])
        seconds = (submitted - reference).total_seconds()
        delay = signed_days(seconds)
        delay_exact = round(seconds / DAY_SECONDS, 6)
    return {
        "round": round_info["index"],
        "round_start": iso_utc(round_info["start"]),
        "deadline": iso_utc(round_info["deadline"]),
        "effective_deadline": iso_utc(effective_due),
        "late_assignment": late_assignment,
        "emergency_open_ended": emergency_open_ended,
        "assigned_before_round_start": before_start,
        "time_to_review_days": time_days,
        "time_to_review_days_exact": exact_days,
        "delay_days": delay,
        "delay_days_exact": delay_exact,
    }


def active_at(
    assignments: list[dict[str, Any]], author: str,
    when: datetime, aliases: dict[str, str]
) -> bool:
    """Check whether author holds ANY assignment on the paper at this instant."""
    for a in assignments:
        if canonical_email(a.get("email"), aliases) != author:
            continue
        start = optional_time(a.get("assigned_at"), "assignment start")
        end = optional_time(a.get("unassigned_at"), "assignment end")
        if start is not None and start <= when and (end is None or when < end):
            return True
    return False


def canonical_email(raw: str | None, aliases: dict[str, str]) -> str:
    email = email_key(raw)
    seen: set[str] = set()
    while email in aliases:
        if email in seen:
            raise ValueError(f"Account alias cycle involving {email}")
        seen.add(email)
        email = email_key(aliases[email])
    return email


def paper_comments(paper: dict[str, Any], aliases: dict[str, str]) -> list[dict[str, Any]]:
    """Current comments + logged/deleted comments for chronology, one per ID."""
    timeline: list[dict[str, Any]] = []
    ids: set[int] = set()
    for c in paper.get("comments", []):
        if c.get("log_comment_id") is not None:
            ids.add(c["log_comment_id"])
        timeline.append({
            "author": canonical_email(c.get("author") or c.get("raw_author_email"), aliases),
            "timestamp": c.get("posted_at"),
            "wordcount": c.get("wordcount"),
            "id": c.get("log_comment_id"),
            "source": "visible",
        })
    for c in paper.get("log_only_comments", []):
        if c.get("comment_id") in ids:
            continue
        timeline.append({
            "author": canonical_email(c.get("author"), aliases),
            "timestamp": c.get("timestamp"),
            "wordcount": None,
            "id": c.get("comment_id"),
            "source": "log_only",
        })
    timeline.sort(key=lambda c: (
        optional_time(c["timestamp"], "comment posting time") or datetime.max.replace(tzinfo=timezone.utc),
        c["id"] if c["id"] is not None else -1,
    ))
    return timeline


def find_initiators(
    papers: dict[str, Any], schedule: list[dict[str, Any]],
    aliases: dict[str, str], eligible_emails: set[str],
    min_review_words: int, min_comment_words: int, warnings: list[str],
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Find at most one initiator per (paper, reviewing-threshold round).

    Windows are disjoint: (time of Nth submitted review, time of next
    threshold) and for the final round (threshold, infinity).
    Unmatched/deleted reviewer comments with unknown wordcount prevent a
    definitive award if encountered before the first confirmed initiator.
    """
    winners: dict[str, list[dict[str, Any]]] = defaultdict(list)
    decisions: list[dict[str, Any]] = []
    for paper_id in sorted(papers, key=int):
        paper = papers[paper_id]
        reviews = []
        unknown_review_times = 0
        for r in paper.get("reviews", []):
            if r.get("currently_submitted_in_log") is False:
                continue
            at = optional_time(r.get("first_submitted_at"), f"paper {paper_id} review first submission")
            if at is not None:
                reviews.append((at, r))
            else:
                unknown_review_times += 1
        reviews.sort(key=lambda x: (x[0], x[1].get("log_review_id") or 0))
        all_comments = paper_comments(paper, aliases)
        assignments = paper.get("assignments", [])

        for i, rnd in enumerate(schedule):
            threshold = rnd["min_reviews"]
            decision: dict[str, Any] = {
                "paper_id": int(paper_id), "round": i + 1,
                "min_reviews": threshold, "threshold_at": None,
                "next_threshold_at": None, "status": "threshold_not_reached",
                "winner": None, "comment_id": None, "comment_at": None,
                "reason": None,
            }
            # Unknown review-submission times can shift the threshold time
            # and change who was first to comment. Do not guess their position.
            if unknown_review_times and len(reviews) + unknown_review_times >= threshold:
                decision["status"] = "unresolved_missing_review_time"
                decision["reason"] = "at least one submitted review lacks a first submission time"
                warnings.append(f"Paper {paper_id}, discussion round {i + 1}: review chronology incomplete")
                decisions.append(decision)
                continue
            if len(reviews) < threshold:
                decisions.append(decision)
                continue
            threshold_time = reviews[threshold - 1][0]
            next_threshold = schedule[i + 1]["min_reviews"] if i + 1 < len(schedule) else None
            end_time = reviews[next_threshold - 1][0] if next_threshold and len(reviews) >= next_threshold else None
            decision["threshold_at"] = iso_utc(threshold_time)
            decision["next_threshold_at"] = iso_utc(end_time)
            decision["status"] = "no_qualifying_comment"
            # Do not credit an apparent winner while there is a comment whose
            # original timestamp is unknown; it might have intervened first.
            if any(not c["timestamp"] for c in all_comments):
                decision["status"] = "unresolved_missing_comment_time"
                decision["reason"] = "at least one comment has no posting time"
                warnings.append(f"Paper {paper_id}, discussion round {i + 1}: comment chronology incomplete")
                decisions.append(decision)
                continue
            # Timestamp precision is to the second: cannot establish whether
            # a comment was before/after a review at exactly the same second.
            if any(date_time(c["timestamp"], "comment time") == threshold_time for c in all_comments):
                decision["status"] = "unresolved_tied_threshold_timestamp"
                decision["reason"] = "comment posted at same second as threshold review"
                warnings.append(f"Paper {paper_id}, discussion round {i + 1}: tied review/comment timestamp")
                decisions.append(decision)
                continue

            for c in all_comments:
                when = optional_time(c["timestamp"], f"paper {paper_id} comment")
                if when is None:
                    # Unknown chronology can invalidate attribution. Since
                    # no safe placement is possible, withhold any award.
                    decision["status"] = "unresolved_missing_comment_time"
                    decision["reason"] = "at least one comment has no posting time"
                    break
                if when <= threshold_time:
                    continue
                if end_time is not None and when >= end_time:
                    break
                author = c["author"]
                if not author or not active_at(assignments, author, when, aliases):
                    decision["status"] = "blocked_nonreviewer_comment"
                    decision["comment_at"] = iso_utc(when)
                    decision["comment_id"] = c["id"]
                    decision["reason"] = "first non-assigned comment in round"
                    break
                if c["wordcount"] is not None and c["wordcount"] < min_comment_words:
                    continue
                # Only reviews already submitted by this same assigned author
                # can qualify; later submitted reviews are not retroactive.
                authored = [r for at, r in reviews if at < when and canonical_email(r.get("author"), aliases) == author]
                if not authored:
                    continue
                sufficient = False
                unknown_length = False
                for r in authored:
                    first_length = r.get("first_submission_hotcrp_length")
                    if first_length is None:
                        unknown_length = True
                    elif first_length >= min_review_words:
                        sufficient = True
                if not sufficient and not unknown_length:
                    continue
                if c["wordcount"] is None or (unknown_length and not sufficient):
                    decision["status"] = "unresolved_missing_wordcount"
                    decision["comment_at"] = iso_utc(when)
                    decision["comment_id"] = c["id"]
                    decision["reason"] = "reviewer comment or original review wordcount unavailable"
                    break
                # A qualified commenter can be ineligible for an award (e.g.,
                # external reviewer removed from the award roster).
                decision["comment_at"] = iso_utc(when)
                decision["comment_id"] = c["id"]
                if author not in eligible_emails:
                    decision["status"] = "initiator_ineligible"
                    decision["reason"] = "first eligible discussion initiator not on award roster"
                    break
                decision["status"] = "awarded"
                decision["winner"] = author
                winners[author].append({
                    "paper_id": int(paper_id), "round": i + 1,
                    "min_reviews": threshold, "comment_at": iso_utc(when),
                })
                break
            if decision["status"].startswith("unresolved"):
                warnings.append(
                    f"Paper {paper_id}, discussion round {i + 1}: "
                    f"{decision['status']}; no award assigned"
                )
            decisions.append(decision)
    return winners, decisions


def distribution(values: list[int | float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "sum": 0, "mean": None, "std": None, "min": None, "max": None}
    return {
        "count": len(values),
        "sum": sum(values),
        "mean": round(statistics.mean(values), 4),
        "std": round(statistics.pstdev(values), 4),
        "min": min(values),
        "max": max(values),
    }


def compute_metrics(
    pc_rows: list[dict[str, str]], data: dict[str, Any],
    schedule: list[dict[str, Any]], min_review_words: int,
    min_comment_words: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if min_review_words < 0 or min_comment_words < 0:
        raise ValueError("Word-count thresholds must be nonnegative")
    aliases = {email_key(k): email_key(v) for k, v in data.get("email_aliases", {}).items()}
    # PC CSV, not the PC list saved earlier in JSON, defines award eligibility.
    eligible = {email_key(row["email"]) for row in pc_rows}
    warnings: list[str] = list(data.get("warnings", []))
    saved_pc = {email_key(email) for email in data["pc_members"]}
    missing = sorted(eligible - saved_pc)
    if missing:
        warnings.append(f"PC members not in earlier log roster: {len(missing)}; activity may be incomplete")
    papers = data["papers"]
    initiators, decisions = find_initiators(
        papers, schedule, aliases, eligible, min_review_words, min_comment_words, warnings,
    )

    review_records: dict[str, list[dict[str, Any]]] = defaultdict(list)
    # Paper -> PC -> review for establishing ordinary comment group.
    reviewed_papers: set[tuple[str, int]] = set()
    for paper_id in sorted(papers, key=int):
        paper = papers[paper_id]
        for review in paper.get("reviews", []):
            if review.get("currently_submitted_in_log") is False:
                continue
            author = canonical_email(review.get("author"), aliases)
            if author not in eligible:
                continue
            pid = int(paper_id)
            timing = review_timeliness(
                review.get("assigned_at"), review.get("first_submitted_at"),
                schedule, warnings, f"paper {paper_id} review {review.get('export_id')}",
            )
            entry: dict[str, Any] = {
                "paper_id": pid, "review_id": review.get("log_review_id"),
                "export_id": review.get("export_id"),
                "first_submitted_at": review.get("first_submitted_at"),
                "last_edited_at": review.get("last_edited_at"),
                "last_edit_source": review.get("last_edit_source"),
                "assigned_at": review.get("assigned_at"),
                "wordcount": review.get("wordcount"),
                "hotcrp_length": review.get("hotcrp_length"),
                "first_submission_hotcrp_length": review.get("first_submission_hotcrp_length"),
                **timing,
            }
            review_records[author].append(entry)
            reviewed_papers.add((author, pid))

    # Per-PC paper dictionaries retain all visible comments in their proper,
    # mutually-exclusive class: assigned+reviewed, or extra.
    regular_lengths: dict[str, dict[int, list[int]]] = defaultdict(lambda: defaultdict(list))
    extra_lengths: dict[str, dict[int, list[int]]] = defaultdict(lambda: defaultdict(list))
    ignored_after_unassignment: dict[str, int] = defaultdict(int)
    for paper_id in sorted(papers, key=int):
        paper = papers[paper_id]
        pid = int(paper_id)
        for c in paper.get("comments", []):
            author = canonical_email(c.get("author"), aliases)
            if author not in eligible:
                continue
            length = c.get("wordcount")
            if not isinstance(length, int) or length < 0:
                warnings.append(f"Paper {pid}: PC comment has missing/invalid wordcount; skipped")
                continue
            when = optional_time(c.get("posted_at"), f"paper {pid} comment")
            if when is None:
                warnings.append(f"Paper {pid}: PC comment time missing; counted as extra")
            has_review = (author, pid) in reviewed_papers
            active = when is not None and active_at(paper.get("assignments", []), author, when, aliases)
            if has_review and active:
                regular_lengths[author][pid].append(length)
            elif not has_review:
                # Extra comments are on papers without a submitted review.
                extra_lengths[author][pid].append(length)
            else:
                # Reviewed paper, but no longer an assigned reviewer: not
                # ordinary reviewing activity and not 'unreviewed' either.
                ignored_after_unassignment[author] += 1
                warnings.append(f"Paper {pid}: PC comment after unassignment ignored")

    rows: list[dict[str, Any]] = []
    all_review_words: list[int] = []
    all_time_exact: list[float] = []
    all_comment_words: list[int] = []
    per_pc_comment_averages: list[float] = []
    total_bids = 0
    for pc in pc_rows:
        email = email_key(pc["email"])
        bids = parse_bids(pc["bids"], email)
        topics = parse_topics(pc["topics?"], email)
        total_bids += len(bids)
        records = sorted(
            review_records[email],
            key=lambda rec: (rec["first_submitted_at"] or "", rec["paper_id"], rec["review_id"] or 0),
        )
        reviews = [r["paper_id"] for r in records]
        assignments = [r["assigned_at"] for r in records]
        tt_review = [r["time_to_review_days"] for r in records]
        delays = [r["delay_days"] for r in records]
        wordcounts = [r["wordcount"] for r in records]
        hotcrp_lengths = [r["hotcrp_length"] for r in records]
        review_lengths = [v for v in wordcounts if isinstance(v, int)]
        all_review_words.extend(review_lengths)
        all_time_exact.extend(
            r["time_to_review_days_exact"]
            for r in records if r["time_to_review_days_exact"] is not None
        )

        # Create zeros for every paper for which this PC submitted a review.
        regular_papers = sorted(set(reviews))
        pc_regular = {pid: regular_lengths[email].get(pid, []) for pid in regular_papers}
        pc_extra = {pid: vals for pid, vals in sorted(extra_lengths[email].items()) if vals}
        if len(pc_regular) != len(regular_papers):
            raise AssertionError("Missing per-reviewed-paper comment dictionary")
        normal_words = [v for values in pc_regular.values() for v in values]
        extra_words = [v for values in pc_extra.values() for v in values]
        combined = normal_words + extra_words
        all_comment_words.extend(combined)
        if combined:
            per_pc_comment_averages.append(statistics.mean(combined))
        initiations = sorted(
            initiators[email],
            key=lambda e: (e["paper_id"], e["round"]),
        )
        review_stats = distribution(review_lengths)
        comment_stats = distribution(combined)
        output: dict[str, Any] = {
            "given_name": pc["given_name"],
            "family_name": pc["family_name"],
            "email": pc["email"],
            "bids": bids,
            "topics?": topics,
            "reviews": reviews,
            "assignments": assignments,
            "time-to-review": tt_review,
            "delays": delays,
            "discussion_initiator": initiations,
            "comments": {str(pid): len(vals) for pid, vals in pc_regular.items()},
            "review_length": wordcounts,
            "hotcrp_length": hotcrp_lengths,
            "comments_length": {str(pid): vals for pid, vals in pc_regular.items()},
            "extra_comments": {str(pid): len(vals) for pid, vals in pc_extra.items()},
            "extra_comments_length": {str(pid): vals for pid, vals in pc_extra.items()},
            "review_details": records,
            "bids_totCount": len(bids),
            "reviews_totCount": len(reviews),
            "reviews_uniquePapers": len(set(reviews)),
            "assignments_totCount": len(assignments),
            "time-to-review_totCount": len(tt_review),
            "time-to-review_knownCount": sum(v is not None for v in tt_review),
            "delays_totCount": len(delays),
            "delays_knownCount": sum(v is not None for v in delays),
            "review_length_totCount": len(wordcounts),
            "hotcrp_length_totCount": len(hotcrp_lengths),
            "review_details_totCount": len(records),
            "discussion_initiator_totCount": len(initiations),
            "comments_totCount": len(normal_words),
            "comments_length_totCount": len(normal_words),
            "comments_length_totalWords": sum(normal_words),
            "extra_comments_totCount": len(extra_words),
            "extra_comments_length_totCount": len(extra_words),
            "extra_comments_length_totalWords": sum(extra_words),
            "all_comments_totCount": len(combined),
            "all_comments_totalWords": sum(combined),
            "comments_ignoredAfterUnassignment": ignored_after_unassignment[email],
            "review_length_totalWords": review_stats["sum"],
            "review_length_avgWords": review_stats["mean"],
            "review_length_stdWords": review_stats["std"],
            "review_length_minWords": review_stats["min"],
            "review_length_maxWords": review_stats["max"],
            "comments_avgWords": comment_stats["mean"],
            "comments_stdWords": comment_stats["std"],
            "comments_minWords": comment_stats["min"],
            "comments_maxWords": comment_stats["max"],
            "time-to-review_avgDays": (
                round(statistics.mean(r["time_to_review_days_exact"] for r in records
                                      if r["time_to_review_days_exact"] is not None), 4)
                if any(r["time_to_review_days_exact"] is not None for r in records) else None
            ),
            "delays_lateCount": sum(v is not None and v > 0 for v in delays),
            "delays_onTimeOrExcusedCount": sum(v == 0 for v in delays),
            "delays_earlyCount": sum(v is not None and v < 0 for v in delays),
        }
        for rnd in schedule:
            output[f"discussion_initiator_round{rnd['index']}_totCount"] = sum(
                item["round"] == rnd["index"] for item in initiations
            )
        rows.append(output)

    totals = {
        "eligible_pc_members": len(rows),
        "total_reviews": sum(row["reviews_totCount"] for row in rows),
        "emergency_reviews_without_following_round_cutoff": sum(
            entry["emergency_open_ended"] is True
            for records in review_records.values() for entry in records
        ),
        "review_wordcounts": distribution(all_review_words),
        "review_time_days_exact": distribution(all_time_exact),
        "total_bids": total_bids,
        "mean_bids_per_pc_including_zero": round(total_bids / len(rows), 4) if rows else None,
        "total_regular_comments": sum(row["comments_totCount"] for row in rows),
        "total_extra_comments": sum(row["extra_comments_totCount"] for row in rows),
        "comment_wordcounts_all_pc": distribution(all_comment_words),
        "mean_all_comments_per_pc_including_zero": (
            round(len(all_comment_words) / len(rows), 4) if rows else None
        ),
        "mean_individual_pc_comment_wordcount_among_commenters": (
            round(statistics.mean(per_pc_comment_averages), 4) if per_pc_comment_averages else None
        ),
        "pc_members_who_initiated_at_least_one_round": sum(
            bool(row["discussion_initiator_totCount"]) for row in rows
        ),
        "total_awarded_initiation_rounds": sum(
            row["discussion_initiator_totCount"] for row in rows
        ),
        "discussions_by_status": {
            status: sum(d["status"] == status for d in decisions)
            for status in sorted(set(d["status"] for d in decisions))
        },
    }
    audit = {
        "schema_version": 1,
        "config": {
            "rounds": [{
                "index": r["index"], "min_reviews": r["min_reviews"],
                "start": iso_utc(r["start"]), "deadline": iso_utc(r["deadline"]),
                "next_start": iso_utc(r["next_start"]),
                "grace_cutoff": iso_utc(r["grace_cutoff"]),
            } for r in schedule],
            "min_review_words_at_first_submission_hotcrp": min_review_words,
            "min_comment_words": min_comment_words,
            "comments_qualifying_as_regular": (
                "Authored by an eligible PC member on a paper they have a currently "
                "submitted review for, while their assignment is active at posting time. "
                "Comments on unreviewed papers are extra; comments on reviewed "
                "papers posted after unassignment are audit-only ignored."
            ),
            "missing_comment_or_initial_review_length": (
                "Withhold initiator award if an earlier, potentially qualifying "
                "assigned-reviewer contribution cannot be verified."
            ),
            "threshold_window": "(Nth_review_submission_time, next_threshold_time); nonoverlapping",
            "round_assignment": "Latest configured reviewing round start <= assigned_at",
            "review_delay": (
                "Signed whole days rounded away from zero. If assigned on/after "
                "deadline minus emergency_grace, a submission after the regular "
                "deadline but on/before the next configured round start has delay "
                "0; later submissions are late relative to that start. If no next "
                "round exists, there is no configured emergency cutoff: the "
                "late-assigned review receives delay 0 after the regular deadline "
                "and is flagged emergency_open_ended=true, not judged against a "
                "deadline that preceded its assignment. Provide a later round "
                "start when a strict emergency cutoff is required."
            ),
            "std_deviation": "population (pstdev)",
        },
        "venue_totals": totals,
        "discussion_rounds": decisions,
        "warnings": sorted(set(warnings)),
    }
    return rows, audit


def generate_pc_metrics(
    source_folder: str | Path,
    min_reviews: list[int],
    round_starts: list[str],
    deadlines: list[str],
    emergency_grace_days: float,
    min_review_words: int,
    min_comment_words: int,
    output_folder: str | Path | None = None,
    pc_filename: str | Path | None = None,
    reviews_json_filename: str | Path | None = None,
) -> tuple[Path, Path]:
    """Write (pc_metrics.csv, pc_metrics_summary.json); return their paths."""
    schedule = build_schedule(min_reviews, round_starts, deadlines, emergency_grace_days)
    pc_path, reviews_path, pc_rows, data = load_input_files(
        source_folder, pc_filename, reviews_json_filename
    )
    rows, audit = compute_metrics(pc_rows, data, schedule, min_review_words, min_comment_words)
    audit["sources"] = {"pc_csv": str(pc_path), "reviews_json": str(reviews_path)}
    audit["config"]["emergency_grace_days"] = emergency_grace_days
    output_dir = Path(output_folder).expanduser() if output_folder else Path(source_folder).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "pc_metrics.csv"
    audit_path = output_dir / "pc_metrics_summary.json"
    if rows:
        with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            for row in rows:
                writer.writerow({k: json_cell(v) if isinstance(v, (list, dict)) else (v if v is not None else "")
                                 for k, v in row.items()})
    else:
        raise ValueError("No eligible PC members in input CSV")
    with audit_path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(audit, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return csv_path, audit_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source_folder", help="Folder containing PC CSV and reviews_extracted.json")
    parser.add_argument("--min-reviews", type=int, nargs="+", required=True,
                        help="Increasing review-count thresholds, e.g. 2 4 5")
    parser.add_argument("--round-starts", nargs="+", required=True,
                        help="ISO 8601 dates/times with explicit UTC offsets")
    parser.add_argument("--deadlines", nargs="+", required=True,
                        help="ISO 8601 dates/times with explicit UTC offsets")
    parser.add_argument("--emergency-grace-days", type=float, required=True)
    parser.add_argument("--min-review-words", type=int, required=True,
                        help="First submission HotCRP length needed to initiate")
    parser.add_argument("--min-comment-words", type=int, required=True,
                        help="Comment wordcount needed to initiate")
    parser.add_argument("--pc-file", default=None, help="Override discovered PC CSV")
    parser.add_argument("--reviews-json", default=None, help="Override discovered extracted reviews JSON")
    parser.add_argument("-o", "--output-folder", default=None,
                        help="Defaults to source_folder")
    args = parser.parse_args()
    csv_path, summary_path = generate_pc_metrics(
        source_folder=args.source_folder, min_reviews=args.min_reviews,
        round_starts=args.round_starts, deadlines=args.deadlines,
        emergency_grace_days=args.emergency_grace_days,
        min_review_words=args.min_review_words,
        min_comment_words=args.min_comment_words,
        output_folder=args.output_folder,
        pc_filename=args.pc_file, reviews_json_filename=args.reviews_json,
    )
    print(f"PC metrics: {csv_path}")
    print(f"Audit/venue summary: {summary_path}")
    with summary_path.open(encoding="utf-8") as f:
        summary = json.load(f)
    print(f"PC members: {summary['venue_totals']['eligible_pc_members']}; "
          f"reviews: {summary['venue_totals']['total_reviews']}; "
          f"awarded initiation rounds: {summary['venue_totals']['total_awarded_initiation_rounds']}; "
          f"warnings: {len(summary['warnings'])}")


if __name__ == "__main__":
    main()
