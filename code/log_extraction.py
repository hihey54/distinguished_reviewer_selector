"""Extract paper, review, assignment and comment timelines from HotCRP logs.

Runs entirely offline with Python's standard library (Python 3.10+).

Usage:
    python log_extraction.py source/pc.csv source/hotcrp-log.csv -o output

The PC CSV is authoritative for eligibility. However, ALL log commenters and
assignment holders (including chairs/non-PC accounts) are retained, because
future discussion-initiation calculations need to observe chair interventions.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

REVIEW_EVENT = re.compile(r"^Review\s+(\d+)\s+(.+)$")
COMMENT_EVENT = re.compile(r"^Comment\s+(\d+)\s+(.+)$")
ASSIGNED_EVENT = re.compile(
    r"^(assigned|self-assigned):\s*(.*?)(?:,\s*round\s+(.+))?$"
)
SUBMITTED_EVENT = re.compile(r"^(?:submitted:|edited, submitted:)")
UPDATED_EVENT = re.compile(r"^edited, updated:")
UPDATED_DRAFT_EVENT = re.compile(r"^edited, updated draft:")
LENGTH_EVENT = re.compile(r"\b(\d+)\s+words?\s*$", re.IGNORECASE)
TRANSFER_EVENT = re.compile(r"^transferred to\s+(\S+)")


def normalized_email(value: str | None) -> str:
    """Case-insensitive, whitespace-insensitive identifier for HotCRP users."""
    return (value or "").strip().casefold()


def read_pc_members(filename: str | Path) -> dict[str, dict[str, str]]:
    """Read eligible PC members. Membership comes only from this input."""
    path = Path(filename).expanduser()
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames or "email" not in reader.fieldnames:
            raise ValueError("PC CSV must have an 'email' column.")

        pc_members: dict[str, dict[str, str]] = {}
        for row_number, row in enumerate(reader, start=2):
            email = normalized_email(row.get("email"))
            if not email:
                raise ValueError(f"Missing PC email at CSV row {row_number}.")
            if email in pc_members:
                raise ValueError(f"Duplicate PC email in input: {email}")
            pc_members[email] = {
                "given_name": (row.get("given_name") or "").strip(),
                "family_name": (row.get("family_name") or "").strip(),
            }
    return pc_members


def read_log(filename: str | Path) -> list[dict[str, Any]]:
    """Parse chronological events, respecting their explicit UTC offsets."""
    path = Path(filename).expanduser()
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        required = {"date", "email", "affected_email", "paper", "action"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(
                "HotCRP log must contain: " + ", ".join(sorted(required))
            )
        events = []
        for csv_row, row in enumerate(reader, start=2):
            raw_date = (row.get("date") or "").strip()
            try:
                # HotCRP exports, e.g. '2026-08-25 04:00:00 -1200'.
                when = datetime.strptime(raw_date, "%Y-%m-%d %H:%M:%S %z")
            except ValueError as exc:
                raise ValueError(
                    f"Invalid timezone-aware date at log CSV row "
                    f"{csv_row}: {raw_date!r}"
                ) from exc
            paper_text = (row.get("paper") or "").strip()
            if paper_text and not paper_text.isdecimal():
                raise ValueError(
                    f"Invalid paper ID at log CSV row {csv_row}: "
                    f"{paper_text!r}"
                )
            events.append(
                {
                    "when": when,
                    "timestamp": when.isoformat(timespec="seconds"),
                    "csv_row": csv_row,
                    "paper": int(paper_text) if paper_text else None,
                    "actor": normalized_email(row.get("email")),
                    "affected": normalized_email(row.get("affected_email")),
                    "actor_roles": (row.get("roles") or "").strip(),
                    "action": (row.get("action") or "").strip(),
                }
            )

    # HotCRP logs are normally newest first. For equal timestamps, reverse
    # original row order to retain the log's implied chronological ordering.
    events.sort(key=lambda event: (event["when"], -event["csv_row"]))
    return events


def account_aliases(
        events: list[dict[str, Any]], pc_emails: set[str]
) -> dict[str, str]:
    """Resolve explicit 'transferred to' account changes for PC matching.

    These account transfers are not paper reassignment events; the original
    email remains recorded on each comment/assignment for auditability.
    """
    targets: dict[str, str] = {}
    for event in events:
        action = event["action"]
        if not (REVIEW_EVENT.match(action) or COMMENT_EVENT.match(action)):
            continue
        transferred = re.match(r"^(?:Review|Comment) \d+ transferred to\s+(\S+)", action)
        if not transferred:
            continue
        old_email = event["actor"]
        new_email = event["affected"] or normalized_email(transferred.group(1))
        if not old_email or not new_email or old_email == new_email:
            continue
        if old_email in pc_emails and new_email in pc_emails:
            raise ValueError(
                f"Account transfer connects two eligible PC accounts: "
                f"{old_email} -> {new_email}; please check manually."
            )
        if old_email in targets and targets[old_email] != new_email:
            raise ValueError(
                f"Conflicting email transfers for {old_email}: "
                f"{targets[old_email]} / {new_email}"
            )
        targets[old_email] = new_email

    def resolved(email: str) -> str:
        seen = set()
        while email in targets:
            if email in seen:
                raise ValueError(f"Cycle in account transfers involving {email}")
            seen.add(email)
            email = targets[email]
        return email

    return {old: resolved(old) for old in sorted(targets)}


def new_review(paper_id: int, review_id: int) -> dict[str, Any]:
    return {
        "paper_id": paper_id,
        "review_id": review_id,
        "assignments": [],
        "first_submitted_at": None,
        "first_submission_hotcrp_length": None,
        "last_submitted_at": None,
        "hotcrp_length": None,
        "submitted_by": None,
        "first_submitted_by": None,
        "submitted_by_original_email": None,
        "roles_at_first_submission": None,
        "currently_submitted": False,
        "ever_submitted": False,
        "submission_events": [],
        "last_draft_hotcrp_length": None,
        "deleted_at": None,
    }


def new_paper(paper_id: int) -> dict[str, Any]:
    return {
        "paper_id": paper_id,
        "first_submitted_at": None,
        "last_submitted_at": None,
        "currently_submitted": False,
        "withdrawn_at": None,
        "review_states": {},
        "comments_by_id": {},
    }


def get_review(state: dict[str, Any], review_id: int) -> dict[str, Any]:
    reviews = state["review_states"]
    if review_id not in reviews:
        reviews[review_id] = new_review(state["paper_id"], review_id)
    return reviews[review_id]


def event_length(action: str) -> int | None:
    match = LENGTH_EVENT.search(action)
    return int(match.group(1)) if match else None


def choose_assignment(
        review: dict[str, Any], email: str, submission_date: str
) -> dict[str, Any] | None:
    """Choose assignment active at first submission, if it exists."""
    options = [
        assignment
        for assignment in review["assignments"]
        if assignment["email"] == email
    ]
    submitted = datetime.fromisoformat(submission_date)
    active = [
        assignment
        for assignment in options
        if datetime.fromisoformat(assignment["assigned_at"]) <= submitted
           and (assignment["unassigned_at"] is None
                or submitted < datetime.fromisoformat(assignment["unassigned_at"]))
    ]
    # A gap is not silently interpreted as a valid assignment.
    return active[-1] if active else None


def process_log(
        pc_filename: str | Path, log_filename: str | Path
) -> dict[str, Any]:
    """Extract a deterministic JSON-ready structure; no files are written."""
    pc_members = read_pc_members(pc_filename)
    events = read_log(log_filename)
    aliases = account_aliases(events, set(pc_members))

    def canonical(email: str) -> str:
        return aliases.get(email, email)

    papers: dict[int, dict[str, Any]] = {}
    warnings: list[str] = []
    counters: Counter[str] = Counter()

    for event in events:
        paper_id = event["paper"]
        if paper_id is None:
            continue

        if paper_id not in papers:
            papers[paper_id] = new_paper(paper_id)
        paper = papers[paper_id]
        action = event["action"]
        when = event["timestamp"]
        actor = canonical(event["actor"])
        affected = canonical(event["affected"])
        csv_row = event["csv_row"]

        if action.startswith(("Paper started, submitted:", "Paper submitted:")):
            if paper["first_submitted_at"] is None:
                paper["first_submitted_at"] = when
            paper["last_submitted_at"] = when
            paper["currently_submitted"] = True
            counters["paper_submission_events"] += 1
            continue
        if action.startswith("Paper withdrawn"):
            paper["withdrawn_at"] = when
            paper["currently_submitted"] = False
            counters["paper_withdrawal_events"] += 1
            continue

        review_match = REVIEW_EVENT.match(action)
        if review_match:
            review_id = int(review_match.group(1))
            detail = review_match.group(2)
            review = get_review(paper, review_id)

            assigned_match = ASSIGNED_EVENT.match(detail)
            if assigned_match:
                is_self_assignment = assigned_match.group(1) == "self-assigned"
                assignee = actor if is_self_assignment else affected or None
                if assignee is None:
                    warnings.append(
                        f"Unknown assignee: paper {paper_id}, review "
                        f"{review_id}, log row {csv_row}."
                    )
                # End a prior active assignment before a new assignment.
                for assignment in review["assignments"]:
                    if assignment["unassigned_at"] is None:
                        assignment["unassigned_at"] = when
                        assignment["end_reason"] = "reassigned"

                review["assignments"].append(
                    {
                        "review_id": review_id,
                        "email": assignee,
                        "original_email": (
                            event["actor"] if is_self_assignment
                            else event["affected"] or None
                        ),
                        "is_pc_member": assignee in pc_members if assignee else False,
                        "assigned_at": when,
                        "unassigned_at": None,
                        "end_reason": None,
                        "assignment_type": assigned_match.group(2).strip(),
                        "round": (assigned_match.group(3) or "").strip() or None,
                        "log_row": csv_row,
                    }
                )
                counters["assignment_events"] += 1
                continue

            if detail == "unassigned":
                active = [
                    assignment for assignment in review["assignments"]
                    if assignment["unassigned_at"] is None
                ]
                matching = [a for a in active if a["email"] == affected]
                to_close = matching[-1] if matching else (active[-1] if active else None)
                if to_close is not None:
                    to_close["unassigned_at"] = when
                    to_close["end_reason"] = "unassigned"
                    if affected and to_close["email"] != affected:
                        warnings.append(
                            f"Unassignment assignee mismatch: paper {paper_id}, "
                            f"review {review_id}, log row {csv_row}."
                        )
                else:
                    warnings.append(
                        f"Unassignment without preceding assignment: "
                        f"paper {paper_id}, review {review_id}, log row {csv_row}."
                    )
                counters["unassignment_events"] += 1
                continue

            if SUBMITTED_EVENT.match(detail):
                length = event_length(detail)
                if not review["ever_submitted"]:
                    review["first_submitted_at"] = when
                    review["first_submission_hotcrp_length"] = length
                    review["submitted_by_original_email"] = event["actor"]
                    review["first_submitted_by"] = actor or None
                    review["roles_at_first_submission"] = event["actor_roles"]
                review["ever_submitted"] = True
                review["submitted_by"] = actor or None
                review["last_submitted_at"] = when
                review["currently_submitted"] = True
                review["deleted_at"] = None
                review["hotcrp_length"] = length
                review["submission_events"].append(
                    {"timestamp": when, "hotcrp_length": length,
                     "kind": "submitted", "log_row": csv_row}
                )
                counters["review_submission_events"] += 1
                continue

            if UPDATED_DRAFT_EVENT.match(detail):
                review["last_draft_hotcrp_length"] = event_length(detail)
                counters["review_draft_updates"] += 1
                continue

            if UPDATED_EVENT.match(detail):
                length = event_length(detail)
                if length is not None and review["currently_submitted"]:
                    review["hotcrp_length"] = length
                counters["review_submitted_updates"] += 1
                continue

            if detail == "unsubmitted draft":
                review["currently_submitted"] = False
                review["submission_events"].append(
                    {"timestamp": when, "hotcrp_length": None,
                     "kind": "unsubmitted", "log_row": csv_row}
                )
                counters["review_unsubmit_events"] += 1
                continue

            if detail == "deleted":
                review["currently_submitted"] = False
                review["deleted_at"] = when
                for assignment in review["assignments"]:
                    if assignment["unassigned_at"] is None:
                        assignment["unassigned_at"] = when
                        assignment["end_reason"] = "deleted"
                counters["review_delete_events"] += 1
                continue

            if detail.startswith("changed:"):
                # Round changes do not imply an unassignment/reassignment.
                for assignment in reversed(review["assignments"]):
                    if assignment["unassigned_at"] is None:
                        new_round = re.search(r"\bto\s+[^,]+,\s+round\s+(.*)$", detail)
                        if new_round:
                            assignment["round"] = new_round.group(1).strip()
                        break
                counters["review_round_change_events"] += 1
                continue

            if TRANSFER_EVENT.match(detail):
                counters["review_transfer_events"] += 1
                continue

            warnings.append(
                f"Unhandled review event: paper {paper_id}, review "
                f"{review_id}, log row {csv_row}: {detail}"
            )
            continue

        comment_match = COMMENT_EVENT.match(action)
        if comment_match:
            comment_id = int(comment_match.group(1))
            detail = comment_match.group(2)
            comments = paper["comments_by_id"]

            if "edited" in detail:
                if comment_id not in comments:
                    comments[comment_id] = {
                        "comment_id": comment_id,
                        "timestamp": when,
                        "timestamp_source": "first_comment_edit_in_log",
                        "author": actor or None,
                        "original_author": event["actor"] or None,
                        "roles_at_creation": event["actor_roles"],
                        "is_pc_member": actor in pc_members,
                        "thread": (
                            "submission" if "on submission thread" in detail
                            else "review"
                        ),
                        "last_edited_at": when,
                        "deleted_at": None,
                        "log_row": csv_row,
                    }
                    counters["comments_first_seen"] += 1
                else:
                    comments[comment_id]["last_edited_at"] = when
                counters["comment_edit_events"] += 1
                continue

            if "deleted" in detail:
                if comment_id in comments:
                    comments[comment_id]["deleted_at"] = when
                else:
                    warnings.append(
                        f"Comment deletion without prior editing: paper "
                        f"{paper_id}, comment {comment_id}, log row {csv_row}."
                    )
                counters["comment_delete_events"] += 1
                continue

            if TRANSFER_EVENT.match(detail):
                counters["comment_transfer_events"] += 1
                continue

            warnings.append(
                f"Unhandled comment event: paper {paper_id}, comment "
                f"{comment_id}, log row {csv_row}: {detail}"
            )

    result_papers: dict[str, dict[str, Any]] = {}
    all_reviews = 0
    submitted_reviews = 0
    unknown_assignment_dates = 0
    all_comments = 0
    for paper_id in sorted(papers):
        paper = papers[paper_id]
        submitted_states = [
            review for review in paper["review_states"].values()
            if review["ever_submitted"]
        ]
        # A logged submitted review is strong evidence even if a paper's
        # submission event is missing from a partial HotCRP log.
        if paper["first_submitted_at"] is None and not submitted_states:
            continue

        review_output = []
        assignment_output = []
        for review_id in sorted(paper["review_states"]):
            review = paper["review_states"][review_id]
            assignment_output.extend(review["assignments"])
            if not review["ever_submitted"]:
                continue

            email = review["submitted_by"]
            first_author = review["first_submitted_by"]
            assigned = choose_assignment(
                review, first_author, review["first_submitted_at"]
            ) if first_author and review["first_submitted_at"] else None
            if assigned is None:
                unknown_assignment_dates += 1
                warnings.append(
                    f"Missing matching active assignment for submitted review "
                    f"{review_id} by {first_author} on paper {paper_id}."
                )
            review_output.append(
                {
                    "review_id": review_id,
                    "email": email,
                    "first_submission_author": first_author,
                    "original_email": review["submitted_by_original_email"],
                    "roles_at_first_submission": review["roles_at_first_submission"],
                    "is_pc_member": email in pc_members if email else False,
                    "assigned_at": assigned["assigned_at"] if assigned else None,
                    "first_submitted_at": review["first_submitted_at"],
                    "last_submitted_at": review["last_submitted_at"],
                    "hotcrp_length": review["hotcrp_length"],
                    "first_submission_hotcrp_length": (
                        review["first_submission_hotcrp_length"]
                    ),
                    "currently_submitted": review["currently_submitted"],
                    "deleted_at": review["deleted_at"],
                    "submission_events": review["submission_events"],
                }
            )

        comments = list(paper["comments_by_id"].values())
        reviews_sorted = sorted(
            review_output,
            key=lambda x: (
                datetime.fromisoformat(x["first_submitted_at"]), x["review_id"]
            )
        )
        assignments_sorted = sorted(
            assignment_output,
            key=lambda x: (
                datetime.fromisoformat(x["assigned_at"]), x["review_id"]
            )
        )
        comments_sorted = sorted(
            comments, key=lambda x: (
                datetime.fromisoformat(x["timestamp"]), x["log_row"]
            )
        )

        all_reviews += len(reviews_sorted)
        submitted_reviews += sum(r["currently_submitted"] for r in reviews_sorted)
        all_comments += len(comments_sorted)
        result_papers[str(paper_id)] = {
            "paper_id": paper_id,
            "first_submitted_at": paper["first_submitted_at"],
            "last_submitted_at": paper["last_submitted_at"],
            "currently_submitted": paper["currently_submitted"],
            "withdrawn_at": paper["withdrawn_at"],
            "submission_inferred_from_review": (
                    paper["first_submitted_at"] is None
            ),
            "reviewers": reviews_sorted,
            "assignments": assignments_sorted,
            "comments": comments_sorted,
        }

    return {
        "schema_version": 1,
        "sources": {
            "pc_csv": Path(pc_filename).name,
            "hotcrp_log_csv": Path(log_filename).name,
        },
        "notes": {
            "timestamp_format": "ISO 8601 with original HotCRP UTC offset",
            "comment_timestamps": (
                "The first logged Comment ... edited event is used as a "
                "proxy for posting time; HotCRP does not label creation "
                "separately in this export."
            ),
            "pc_eligibility": "Only members of the supplied PC CSV are eligible.",
            "account_transfers": (
                "Email aliases from explicit HotCRP transfers are used to "
                "match PC members, while original emails remain available."
            ),
        },
        "pc_members": {email: pc_members[email] for email in sorted(pc_members)},
        "email_aliases": aliases,
        "statistics": {
            "log_events": len(events),
            "eligible_pc_members": len(pc_members),
            "papers_with_submission_evidence": len(result_papers),
            "review_records_ever_submitted": all_reviews,
            "review_records_currently_submitted": submitted_reviews,
            "reviews_without_matching_assignment": unknown_assignment_dates,
            "comment_records": all_comments,
            "other_event_counts": dict(sorted(counters.items())),
            "warnings": len(warnings),
        },
        "warnings": warnings,
        "papers": result_papers,
    }


def extract_log_data(
        pc_filename: str | Path,
        log_filename: str | Path,
        output_folder: str | Path | None = None,
) -> Path:
    """Run extraction and save <log_filename>_parsed.json; return its path."""
    data = process_log(pc_filename, log_filename)
    directory = (
        Path(output_folder).expanduser()
        if output_folder is not None
        else Path(pc_filename).expanduser().parent
    )
    directory.mkdir(parents=True, exist_ok=True)
    output_path = directory / (Path(log_filename).stem + "_parsed.json")
    with output_path.open("w", encoding="utf-8") as destination:
        json.dump(data, destination, indent=2, ensure_ascii=False)
        destination.write("\n")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract paper, review and comment timelines from HotCRP log."
    )
    parser.add_argument("pc_filename", help="PC roster CSV (eligible members)")
    parser.add_argument("log_filename", help="HotCRP activity log CSV")
    parser.add_argument(
        "-o", "--output-folder", default=None,
        help="Output directory (defaults to directory of PC CSV)",
    )
    args = parser.parse_args()
    output_path = extract_log_data(
        args.pc_filename, args.log_filename, args.output_folder
    )
    print(f"Saved: {output_path}")
    with output_path.open(encoding="utf-8") as source:
        statistics = json.load(source)["statistics"]
    print(
        f"Papers: {statistics['papers_with_submission_evidence']} | "
        f"Submitted reviews: {statistics['review_records_currently_submitted']} | "
        f"Comments: {statistics['comment_records']} | "
        f"Warnings: {statistics['warnings']}"
    )


if __name__ == "__main__":
    main()
