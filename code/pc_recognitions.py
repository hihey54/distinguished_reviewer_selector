"""Award deterministic distinguished-PC recognitions from pc_metrics.csv.

Python 3.10+; standard library only. Processes local files only; no AI/network.

Example:
    python pc_recognitions.py output/pc_metrics.csv -o output
    python pc_recognitions.py output/pc_metrics.csv -o output \
        --firefighters-csv source/firefighters.csv

Manual firefighters CSV: required column `email`, optional `paper_ids` (a JSON
array of integer paper IDs), optional `notes`. The file should list ONLY
people independently verified as having submitted extra/emergency reviews.
Without that file, FIREFIGHTER is NOT EVALUATED, not automatically False.

Public API:
    generate_recognitions(metrics_filename, output_folder=None,
                          firefighters_csv=None, top_n=5)
Returns (pc_recognitions_csv, recognition_winners_csv, recognition_summary_json).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AWARD_NAMES = (
    "COMMENTER", "SPEEDY", "ELABORATE", "COMMITTED", "FIREFIGHTER", "ALACRITOUS"
)
REQUIRED_COLUMNS = {
    "given_name", "family_name", "email", "bids", "topics?", "reviews",
    "delays", "review_details", "review_length", "discussion_initiator",
    "comments_length", "extra_comments_length",
}


def canonical_email(value: str | None) -> str:
    return (value or "").strip().casefold()


def load_json_cell(value: str | None, column: str, email: str) -> Any:
    try:
        return json.loads(value if value is not None else "")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{email}: invalid JSON in column {column!r}") from exc


def int_list(value: Any, column: str, email: str, *, nullable: bool = False,
             nonnegative: bool = True) -> list[int | None]:
    if not isinstance(value, list):
        raise ValueError(f"{email}: {column} must be a JSON list")
    output = []
    for item in value:
        if nullable and item is None:
            output.append(None)
        elif type(item) is int and (not nonnegative or item >= 0):
            output.append(item)
        else:
            raise ValueError(f"{email}: {column} contains invalid item {item!r}")
    return output


def parse_date(value: str | None, description: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Invalid timestamp for {description}: {value!r}") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"Missing time zone in timestamp for {description}: {value!r}")
    return dt.astimezone(timezone.utc)


def parse_comment_wordcounts(value: Any, column: str, email: str) -> list[int | None]:
    if not isinstance(value, dict):
        raise ValueError(f"{email}: {column} must be a JSON object mapping paper IDs to wordcount lists")
    result: list[int | None] = []
    for paper, lengths in value.items():
        if not str(paper).isdigit():
            raise ValueError(f"{email}: invalid paper ID {paper!r} in {column}")
        result.extend(int_list(lengths, f"{column}[{paper}]", email, nullable=True))
    return result


def topic_status(value: str | None, email: str) -> bool | None:
    key = (value or "").strip().casefold()
    if key == "true":
        return True
    if key == "false":
        return False
    if key in ("n/a", "na", "not applicable"):
        return None
    raise ValueError(f"{email}: 'topics?' must be True, False, or N/A; found {value!r}")


def read_metrics(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    with path.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        fields = set(reader.fieldnames or ())
        if not fields:
            raise ValueError(f"Empty or headerless CSV: {path}")
        if missing := REQUIRED_COLUMNS - fields:
            raise ValueError(f"Missing columns from {path}: {sorted(missing)}")
        raw_rows = list(reader)

    records = []
    found_emails = set()
    for line_number, row in enumerate(raw_rows, start=2):
        if None in row:
            raise ValueError(f"Malformed CSV line {line_number}: unexpected fields")
        email = canonical_email(row.get("email"))
        if not email or email in found_emails:
            raise ValueError(f"Missing or duplicate eligible-PC email in line {line_number}: {email!r}")
        found_emails.add(email)

        bids = int_list(load_json_cell(row["bids"], "bids", email), "bids", email)
        reviews = int_list(load_json_cell(row["reviews"], "reviews", email), "reviews", email)
        delays = int_list(load_json_cell(row["delays"], "delays", email),
                          "delays", email, nullable=True, nonnegative=False)
        review_words = int_list(load_json_cell(row["review_length"], "review_length", email),
                                "review_length", email, nullable=True)
        details = load_json_cell(row["review_details"], "review_details", email)
        initiations = load_json_cell(row["discussion_initiator"], "discussion_initiator", email)
        comments = parse_comment_wordcounts(load_json_cell(row["comments_length"],
                                                            "comments_length", email),
                                            "comments_length", email)
        extra_comments = parse_comment_wordcounts(load_json_cell(row["extra_comments_length"],
                                                                  "extra_comments_length", email),
                                                  "extra_comments_length", email)
        if not isinstance(details, list) or any(not isinstance(x, dict) for x in details):
            raise ValueError(f"{email}: review_details must be a list of JSON objects")
        if not isinstance(initiations, list) or any(not isinstance(x, dict) for x in initiations):
            raise ValueError(f"{email}: discussion_initiator must be a list of JSON objects")
        if not (len(reviews) == len(delays) == len(review_words) == len(details)):
            raise ValueError(f"{email}: reviews, delays, review_length, and review_details must align")
        for i, detail in enumerate(details):
            if detail.get("paper_id") != reviews[i]:
                raise ValueError(f"{email}: review_details[{i}] paper ID differs from reviews[{i}]")
            if detail.get("wordcount") != review_words[i]:
                raise ValueError(f"{email}: review_details[{i}] wordcount differs from review_length[{i}]")
            if detail.get("delay_days") != delays[i]:
                raise ValueError(f"{email}: review_details[{i}] delay differs from delays[{i}]")

        initiator_papers: set[int] = set()
        initiative_rounds: set[tuple[int, int]] = set()
        for entry in initiations:
            paper_id, rnd = entry.get("paper_id"), entry.get("round")
            if type(paper_id) is not int or type(rnd) is not int or paper_id <= 0 or rnd <= 0:
                raise ValueError(f"{email}: invalid paper_id / round in discussion_initiator")
            if (paper_id, rnd) in initiative_rounds:
                raise ValueError(f"{email}: duplicate initiator credit for paper {paper_id} round {rnd}")
            initiative_rounds.add((paper_id, rnd))
            initiator_papers.add(paper_id)

        if not reviews:
            timely_status = "no_reviews"
        elif any(isinstance(v, int) and v > 0 for v in delays):
            timely_status = "late"
        elif any(v is None for v in delays):
            timely_status = "unknown"
            warnings.append(f"{email}: cannot verify lateness for all reviews")
        else:
            timely_status = "on_time"
        all_words = comments + extra_comments
        comments_known = all(x is not None for x in all_words)
        reviews_known = all(x is not None for x in review_words)
        if not comments_known:
            warnings.append(f"{email}: unknown comment wordcount; excluded from COMMENTER")
        if not reviews_known:
            warnings.append(f"{email}: unknown review wordcount; excluded from ELABORATE")

        if row.get("all_comments_totalWords", "").strip() and comments_known:
            source_total = int(row["all_comments_totalWords"])
            if sum(all_words) != source_total:
                raise ValueError(f"{email}: computed total comment words differs from summary column")
        if row.get("review_length_totalWords", "").strip() and reviews_known:
            source_total = int(row["review_length_totalWords"])
            if sum(review_words) != source_total:
                raise ValueError(f"{email}: computed total review words differs from summary column")

        # SPEEDY: sort ALL submitted reviews chronologically and use the second.
        speedy_info = None
        if len(reviews) >= 2 and timely_status == "on_time":
            timestamps = [parse_date(detail.get("first_submitted_at"),
                                     f"{email} review on paper {detail['paper_id']}")
                          for detail in details]
            if any(dt is None for dt in timestamps):
                warnings.append(f"{email}: missing review timestamp; excluded from SPEEDY")
            else:
                ordered = sorted(zip(timestamps, details), key=lambda item: (
                    item[0], item[1]["paper_id"], item[1].get("review_id") or 0
                ))
                second_at, second = ordered[1]
                start = parse_date(second.get("round_start"),
                                   f"{email} second review's round start")
                if start is None:
                    warnings.append(f"{email}: missing round start of second review; excluded from SPEEDY")
                else:
                    original_seconds = (second_at - start).total_seconds()
                    if original_seconds < 0:
                        warnings.append(
                            f"{email}: second review precedes its configured round start; "
                            "ranking time clamped to 0 hours"
                        )
                    speedy_info = {
                        "paper_id": second["paper_id"],
                        "round": second.get("round"),
                        "second_submitted_at": second_at.isoformat(timespec="seconds"),
                        "round_start": start.isoformat(timespec="seconds"),
                        "elapsed_seconds": max(original_seconds, 0.0),
                        "raw_elapsed_seconds": original_seconds,
                        "before_round_start": original_seconds < 0,
                    }

        records.append({
            "given_name": (row.get("given_name") or "").strip(),
            "family_name": (row.get("family_name") or "").strip(),
            "email": email,
            "bids_count": len(bids),
            "topics_complete": topic_status(row.get("topics?"), email),
            "review_count": len(reviews),
            "timeliness_status": timely_status,
            "late_review_count": sum(x is not None and x > 0 for x in delays),
            "review_words": sum(x for x in review_words if x is not None),
            "review_words_known": reviews_known,
            "comment_words": sum(x for x in all_words if x is not None),
            "comments_known": comments_known,
            "comment_count": len(all_words),
            "initiator_papers": sorted(initiator_papers),
            "initiator_round_credits": len(initiations),
            "speedy_info": speedy_info,
        })
    return records, sorted(set(warnings))


def read_firefighters(file: Path, eligible: set[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    with file.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames or "email" not in reader.fieldnames:
            raise ValueError("Manual FIREFIGHTER CSV must include an 'email' column")
        for line, row in enumerate(reader, start=2):
            if None in row:
                raise ValueError(f"Extra fields in FIREFIGHTER CSV at line {line}")
            email = canonical_email(row.get("email"))
            if not email or email not in eligible:
                raise ValueError(f"Unknown FIREFIGHTER email on line {line}: {email!r}")
            if email in result:
                raise ValueError(f"Duplicate FIREFIGHTER email: {email}")
            ids_text = (row.get("paper_ids") or "").strip()
            if ids_text:
                paper_ids = int_list(load_json_cell(ids_text, "paper_ids", email),
                                     "paper_ids", email)
                if len(set(paper_ids)) != len(paper_ids):
                    raise ValueError(f"{email}: duplicate manual FIREFIGHTER paper ID")
                paper_ids.sort()
            else:
                paper_ids = []
            result[email] = {"paper_ids": paper_ids, "notes": (row.get("notes") or "").strip()}
    return result


def _winner(award: str, item: dict[str, Any], metric: str, value: float | int | str,
            details: dict[str, Any] | None = None, rank: int | None = None) -> dict[str, Any]:
    return {
        "award": award, "rank": rank,
        "given_name": item["given_name"], "family_name": item["family_name"],
        "email": item["email"], "metric": metric, "value": value,
        "details": details or {},
    }


def compute_awards(records: list[dict[str, Any]], firefighters: dict[str, dict[str, Any]] | None,
                   top_n: int) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    if type(top_n) is not int or top_n < 1:
        raise ValueError("top_n must be a positive integer")
    notes: list[str] = []
    result: dict[str, list[dict[str, Any]]] = {award: [] for award in AWARD_NAMES}

    # Sort by the requested score; ties use the normalized email deterministically.
    commenters = sorted((x for x in records if x["comments_known"] and x["comment_words"] > 0),
                        key=lambda x: (-x["comment_words"], x["email"]))
    for rank, person in enumerate(commenters[:top_n], 1):
        result["COMMENTER"].append(_winner(
            "COMMENTER", person, "cumulative_comment_words", person["comment_words"],
            {"comments_count": person["comment_count"]}, rank=rank
        ))
    if len(commenters) > top_n and commenters[top_n - 1]["comment_words"] == commenters[top_n]["comment_words"]:
        notes.append("COMMENTER: tie at top-N cutoff resolved by email")

    speedy = sorted((x for x in records if x["speedy_info"] is not None),
                    key=lambda x: (x["speedy_info"]["elapsed_seconds"], x["email"]))
    for rank, person in enumerate(speedy[:top_n], 1):
        info = person["speedy_info"]
        result["SPEEDY"].append(_winner(
            "SPEEDY", person, "hours_after_round_start_of_second_review",
            round(info["elapsed_seconds"] / 3600, 6),
            {"paper_id": info["paper_id"], "round": info["round"],
             "second_submitted_at": info["second_submitted_at"],
             "round_start": info["round_start"],
             "submitted_before_round_start": info["before_round_start"]}, rank=rank
        ))
    if len(speedy) > top_n and speedy[top_n - 1]["speedy_info"]["elapsed_seconds"] == speedy[top_n]["speedy_info"]["elapsed_seconds"]:
        notes.append("SPEEDY: tie at top-N cutoff resolved by email")

    elaborate = sorted((x for x in records if x["timeliness_status"] == "on_time"
                        and x["review_count"] > 0 and x["review_words_known"]),
                       key=lambda x: (-x["review_words"], x["email"]))
    for rank, person in enumerate(elaborate[:top_n], 1):
        result["ELABORATE"].append(_winner(
            "ELABORATE", person, "cumulative_review_words", person["review_words"],
            {"reviews_count": person["review_count"]}, rank=rank
        ))
    if len(elaborate) > top_n and elaborate[top_n - 1]["review_words"] == elaborate[top_n]["review_words"]:
        notes.append("ELABORATE: tie at top-N cutoff resolved by email")

    # If the venue has NO topic configuration, all entries have topics?=N/A
    # and topic completion is not a COMMITTED requirement.
    topics_configured = any(x["topics_complete"] is not None for x in records)
    committed = sorted((x for x in records if x["timeliness_status"] == "on_time"
                        and x["review_count"] > 0
                        and (not topics_configured or x["topics_complete"] is True)
                        and x["bids_count"] > 0), key=lambda x: x["email"])
    for person in committed:
        result["COMMITTED"].append(_winner(
            "COMMITTED", person, "completed_topics_and_bids_with_no_late_reviews", "yes",
            {"bids_count": person["bids_count"], "reviews_count": person["review_count"]}
        ))

    if firefighters is not None:
        lookup = {x["email"]: x for x in records}
        for email in sorted(firefighters):
            person = lookup[email]
            result["FIREFIGHTER"].append(_winner(
                "FIREFIGHTER", person, "manually_verified_extra_review", "yes",
                firefighters[email]
            ))
    else:
        notes.append("FIREFIGHTER not evaluated: no --firefighters-csv was supplied")

    alacritous = sorted((x for x in records if len(x["initiator_papers"]) >= 2),
                       key=lambda x: (-len(x["initiator_papers"]), -x["initiator_round_credits"], x["email"]))
    for person in alacritous:
        result["ALACRITOUS"].append(_winner(
            "ALACRITOUS", person, "distinct_papers_with_initiated_discussions",
            len(person["initiator_papers"]),
            {"papers": person["initiator_papers"],
             "round_initiations": person["initiator_round_credits"]},
        ))
    return result, notes


def generate_recognitions(
    metrics_filename: str | Path,
    output_folder: str | Path | None = None,
    firefighters_csv: str | Path | None = None,
    top_n: int = 5,
) -> tuple[Path, Path, Path]:
    """Compute recognitions and save per-PC, winners, and summary outputs."""
    metrics_path = Path(metrics_filename).expanduser()
    if not metrics_path.is_file():
        raise FileNotFoundError(f"Input PC metrics CSV not found: {metrics_path}")
    records, warnings = read_metrics(metrics_path)
    fire_path = Path(firefighters_csv).expanduser() if firefighters_csv else None
    firefighters = (read_firefighters(fire_path, {x["email"] for x in records})
                    if fire_path is not None else None)
    winners, notes = compute_awards(records, firefighters, top_n)
    out_dir = Path(output_folder).expanduser() if output_folder else metrics_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    per_pc_path = out_dir / "pc_recognitions.csv"
    winners_path = out_dir / "recognition_winners.csv"
    summary_path = out_dir / "recognition_summary.json"

    # One row per award recipient. Keep recognition proof in a JSON-encoded CSV cell.
    with winners_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "award", "rank", "given_name", "family_name", "email", "metric", "value", "details"
        ])
        writer.writeheader()
        for award in AWARD_NAMES:
            for winner in winners[award]:
                writer.writerow({**winner, "details": json.dumps(winner["details"],
                                                            ensure_ascii=False, sort_keys=True)})

    # One row per eligible PC member. Award columns make filtering simple in Excel/pandas.
    by_award = {award: {w["email"]: w for w in winners[award]} for award in AWARD_NAMES}
    columns = [
        "given_name", "family_name", "email", "reviews_count", "late_review_count",
        "timeliness_status", "topics_complete", "bids_count", "review_words",
        "comments_count", "comment_words", "second_review_hours_after_start",
        "second_review_paper_id", "initiated_distinct_papers",
        "initiated_papers", "initiated_round_credits",
        "COMMENTER", "COMMENTER_rank", "SPEEDY", "SPEEDY_rank",
        "ELABORATE", "ELABORATE_rank", "COMMITTED", "FIREFIGHTER", "ALACRITOUS",
        "recognitions",
    ]
    with per_pc_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for person in records:
            email = person["email"]
            accolades = [award for award in AWARD_NAMES if email in by_award[award]]
            speed_info = person["speedy_info"]
            writer.writerow({
                "given_name": person["given_name"], "family_name": person["family_name"],
                "email": email, "reviews_count": person["review_count"],
                "late_review_count": person["late_review_count"],
                "timeliness_status": person["timeliness_status"],
                "topics_complete": ("N/A" if person["topics_complete"] is None
                                    else person["topics_complete"]),
                "bids_count": person["bids_count"],
                "review_words": person["review_words"],
                "comments_count": person["comment_count"],
                "comment_words": (person["comment_words"] if person["comments_known"] else ""),
                "second_review_hours_after_start": (
                    round(speed_info["elapsed_seconds"] / 3600, 6) if speed_info else ""),
                "second_review_paper_id": speed_info["paper_id"] if speed_info else "",
                "initiated_distinct_papers": len(person["initiator_papers"]),
                "initiated_papers": json.dumps(person["initiator_papers"]),
                "initiated_round_credits": person["initiator_round_credits"],
                "COMMENTER": email in by_award["COMMENTER"],
                "COMMENTER_rank": by_award["COMMENTER"].get(email, {}).get("rank", ""),
                "SPEEDY": email in by_award["SPEEDY"],
                "SPEEDY_rank": by_award["SPEEDY"].get(email, {}).get("rank", ""),
                "ELABORATE": email in by_award["ELABORATE"],
                "ELABORATE_rank": by_award["ELABORATE"].get(email, {}).get("rank", ""),
                "COMMITTED": email in by_award["COMMITTED"],
                "FIREFIGHTER": ("" if firefighters is None else email in by_award["FIREFIGHTER"]),
                "ALACRITOUS": email in by_award["ALACRITOUS"],
                "recognitions": json.dumps(accolades, ensure_ascii=False),
            })

    rules = {
        "COMMENTER": f"Top {top_n} by total words in regular and extra comments; positive known length required",
        "SPEEDY": (f"Top {top_n} by exact elapsed hours between the configured start of the "
                   "round of their SECOND chronological submitted review and that submission; "
                   "at least two reviews, all delays known and <= 0. Pre-start elapsed time is clamped to 0."),
        "ELABORATE": f"Top {top_n} by cumulative final review words; at least one review and no late/unknown delays",
        "COMMITTED": ("At least one explicit bid, at least one review and no late/unknown delays; "
                      "topics? must be True if the venue offers topics (all N/A disables the topic condition)"),
        "FIREFIGHTER": ("Only independently verified names from the optional firefighters CSV; "
                        "not inferred from review quantity or assignment dates"),
        "ALACRITOUS": "At least two DISTINCT paper IDs with autonomous discussion initiator credits",
    }
    summary = {
        "schema_version": 1,
        "source_csv": str(metrics_path),
        "manual_firefighters_csv": str(fire_path) if fire_path else None,
        "eligible_pc_count": len(records),
        "topics_configured": any(x["topics_complete"] is not None for x in records),
        "top_n": top_n,
        "tie_breaking": "For ranked awards, ties on the primary metric break by normalized email; exactly top_n win",
        "rules": rules,
        "award_status": {award: ("not_evaluated" if award == "FIREFIGHTER" and firefighters is None
                                 else "evaluated") for award in AWARD_NAMES},
        "award_counts": {award: (None if award == "FIREFIGHTER" and firefighters is None
                                  else len(winners[award])) for award in AWARD_NAMES},
        "winners": winners,
        "notes": notes,
        "warnings": warnings,
    }
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    return per_pc_path, winners_path, summary_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("metrics_filename", help="CSV produced by pc_metrics.py")
    parser.add_argument("-o", "--output-folder", help="Destination directory (defaults to input directory)")
    parser.add_argument("--firefighters-csv", help="Optional manually verified FIREFIGHTER CSV")
    parser.add_argument("--top-n", type=int, default=5, help="Winners per ranked category (default: 5)")
    args = parser.parse_args()
    paths = generate_recognitions(args.metrics_filename, args.output_folder,
                                  args.firefighters_csv, args.top_n)
    for path in paths:
        print(f"Saved: {path}")
    if not args.firefighters_csv:
        print("Note: FIREFIGHTER not evaluated (manual list not provided).")


if __name__ == "__main__":
    main()
