"""Join a HotCRP all-reviews text export to log_extraction.py's JSON.

No network, third-party packages, AI, or content inference is used. Requires
Python 3.10+. Extracted JSON contains metadata and counts, not review text.

Usage:
    python reviews_extraction.py source/aisec26-reviews.txt \
        output/aisec26-log_parsed.json -o output

Callable from a future main.py:
    from reviews_extraction import extract_review_details
    json_path = extract_review_details(text_filename, log_json_filename, out_dir)
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# An AoE timestamp represents UTC-12 throughout the year.
AOE = timezone(timedelta(hours=-12))

PAPER_HEADER = re.compile(r"^Submission #(\d+):(?:\s*.*)?$")
REVIEW_HEADER = re.compile(r"^Review #(\d+)([A-Z]+) \[([^\]]+)\]$")
COMMENT_HEADER = re.compile(r"^Comment(?:\s+(@(?:A)?\d+))?\s+by\s+(.+)$")
EMAIL = re.compile(r"<([^<>\s]+@[^<>\s]+)>")
# Count Unicode word-like tokens while ignoring Markdown punctuation, asterisks,
# repeated dashes, and other separators. Retain apostrophes and hyphens within
# a word; the same rule is used for every review and comment.
WORD = re.compile(r"\b\w+(?:[-'\u2019]\w+)*\b", re.UNICODE)
# HotCRP displays checkbox/dropdown responses as e.g. '2. Medium' or 'C. Weak'.
CHOICE = re.compile(r"^(?:\d+|[A-Za-z])\.\s+\S")
UNDERLINE = re.compile(r"^(?:-{3,}|={3,})$")
STAR_SEPARATOR = re.compile(r"^(?:\*\s+){8,}\*$")


def normalize_email(email: str | None) -> str | None:
    value = (email or "").strip().casefold()
    return value or None


def canonical_email(email: str | None, aliases: dict[str, str]) -> str | None:
    result = normalize_email(email)
    seen: set[str] = set()
    while result in aliases:
        if result in seen:
            raise ValueError(f"Cycle in HotCRP email aliases: {result}")
        seen.add(result)
        result = aliases[result]
    return result


def wordcount(text: str) -> int:
    """Deterministic word count used for ALL reviewer-authored text."""
    return len(WORD.findall(text))


def after_decoration(lines: list[str]) -> list[str]:
    """Trim blank lines and HotCRP's between-paper star separators."""
    out = list(lines)
    while out and (not out[-1].strip() or STAR_SEPARATOR.fullmatch(out[-1].strip())):
        out.pop()
    while out and not out[0].strip():
        out.pop(0)
    return out


def is_rule(line: str, rule: str) -> bool:
    stripped = line.strip()
    return len(stripped) >= 3 and set(stripped) == {rule}


def section_starts(lines: list[str]) -> list[dict[str, Any]]:
    """Identify real top-level headings by their adjacent underline rules."""
    starts: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        paper = PAPER_HEADER.fullmatch(line)
        if paper:
            # Paper titles may wrap to a second or third line.
            has_rule = any(
                is_rule(lines[k], "=")
                for k in range(index + 1, min(index + 5, len(lines)))
            )
            if has_rule:
                starts.append({"kind": "paper", "start": index,
                               "paper": int(paper.group(1))})
                continue

        review = REVIEW_HEADER.fullmatch(line)
        if review and index + 1 < len(lines) and is_rule(lines[index + 1], "="):
            starts.append({
                "kind": "review", "start": index,
                "paper": int(review.group(1)), "letter": review.group(2),
                "round": review.group(3),
            })
            continue

        comment = COMMENT_HEADER.fullmatch(line)
        if comment and index + 1 < len(lines) and is_rule(lines[index + 1], "-"):
            emails = EMAIL.findall(comment.group(2))
            starts.append({
                "kind": "comment", "start": index,
                "export_label": comment.group(1),
                "raw_author_email": normalize_email(emails[-1] if emails else None),
            })

    if not starts or starts[0]["kind"] != "paper":
        raise ValueError("Text does not start with a recognizable HotCRP submission.")

    seen_papers: set[int] = set()
    current_paper: int | None = None
    for event in starts:
        if event["kind"] == "paper":
            current_paper = event["paper"]
            if current_paper in seen_papers:
                raise ValueError(f"Repeated submission block: #{current_paper}")
            seen_papers.add(current_paper)
        else:
            if current_paper is None:
                raise ValueError("Review or comment preceding a submission header")
            if event["kind"] == "review" and event["paper"] != current_paper:
                raise ValueError(
                    f"Review #{event['paper']}{event['letter']} belongs "
                    f"to paper #{current_paper}'s block"
                )
            event["paper"] = current_paper

    for index, event in enumerate(starts):
        event["end"] = starts[index + 1]["start"] if index + 1 < len(starts) else len(lines)
        event["source_line"] = event["start"] + 1
    return starts


def parse_updated_time(value: str) -> str:
    text = " ".join(value.strip().split())
    for fmt in ("%b %d, %Y, %I:%M:%S %p AoE", "%b %d, %Y, %I:%M %p AoE"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=AOE).isoformat(timespec="seconds")
        except ValueError:
            continue
    raise ValueError(f"Unrecognized HotCRP AoE update timestamp {value!r}")


def candidate_field_positions(lines: list[str]) -> list[tuple[int, str]]:
    """Potential form labels (also recognizes user-written Markdown subheadings)."""
    positions: list[tuple[int, str]] = []
    for i in range(len(lines) - 1):
        name = lines[i].strip()
        if (name and lines[i] == name and len(name) <= 120
                and is_rule(lines[i + 1], "-")):
            positions.append((i, name))
    return positions


def parse_review_fields(
    lines: list[str], form_names: set[str] | None = None
) -> list[dict[str, Any]]:
    """Split venue-specific review form fields and count only written answers.

    Repeated form labels are inferred across all reviews. One-off Markdown
    subheadings inside a reviewer's free-text answer are left WITHIN the answer,
    so headings written by reviewers count as their own words.

    Single-line answers prefixed '2. ...', 'A. ...' are coded choices, not
    free text. Multi-line numbered prose is never treated as a coded choice.
    """
    positions = [
        (i, name) for i, name in candidate_field_positions(lines)
        if form_names is None or name in form_names
    ]

    fields: list[dict[str, Any]] = []
    for n, (position, name) in enumerate(positions):
        start = position + 2
        end = positions[n + 1][0] if n + 1 < len(positions) else len(lines)
        body = after_decoration(lines[start:end])
        nonblank = [s.strip() for s in body if s.strip()]
        is_choice = len(nonblank) == 1 and bool(CHOICE.match(nonblank[0]))
        count = 0 if is_choice else wordcount("\n".join(body))
        fields.append({
            "name": name,
            "answer_kind": "choice" if is_choice else "free_text",
            "wordcount": count,
        })
    return fields


def parse_text_export(text_filename: str | Path) -> dict[int, dict[str, Any]]:
    """Parse text export, never requiring a specific review form schema."""
    path = Path(text_filename).expanduser()
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    parts = section_starts(lines)

    # Infer the form schema from repeated labels across different reviews.
    # This avoids treating a reviewer's Markdown subheading (e.g. Strengths)
    # as form metadata. For an export with just one review, accept all labels.
    review_parts = [part for part in parts if part["kind"] == "review"]
    form_heading_counts: Counter[str] = Counter()
    for part in review_parts:
        section = lines[part["start"]:part["end"]]
        form_heading_counts.update(
            {name for _, name in candidate_field_positions(section[2:])}
        )
    min_support = 2 if len(review_parts) > 1 else 1
    form_names = {
        name for name, appearances in form_heading_counts.items()
        if appearances >= min_support
    }

    papers: dict[int, dict[str, Any]] = {}
    for part in parts:
        pid = part["paper"]
        content = lines[part["start"]:part["end"]]
        if part["kind"] == "paper":
            papers[pid] = {"paper_id": pid, "reviews": [], "comments": []}
            continue

        if part["kind"] == "review":
            author_line = next((s for s in content[:16] if s.startswith("* Reviewer:")), None)
            emails = EMAIL.findall(author_line) if author_line else []
            if not emails:
                raise ValueError(
                    f"No review author email in export at line {part['source_line']}"
                )
            update_line = next((s for s in content[:16] if s.startswith("* Updated:")), None)
            updated = None
            if update_line:
                updated = parse_updated_time(update_line.removeprefix("* Updated:").strip())
            fields = parse_review_fields(content[2:], form_names)
            if not fields:
                raise ValueError(
                    f"No review form fields found at line {part['source_line']}"
                )
            papers[pid]["reviews"].append({
                "export_id": f"{pid}{part['letter']}",
                "review_letter": part["letter"],
                "round": part["round"],
                "source_line": part["source_line"],
                "raw_author_email": normalize_email(emails[-1]),
                "text_updated_at": updated,
                "wordcount": sum(field["wordcount"] for field in fields),
                "fields": fields,
            })
            continue

        if part["kind"] == "comment":
            # Header line followed by dashed underline. Everything after that
            # is the comment BODY (not the header or any Markdown decoration).
            body_lines = after_decoration(content[2:])
            papers[pid]["comments"].append({
                "export_label": part["export_label"],
                "source_line": part["source_line"],
                "raw_author_email": part["raw_author_email"],
                "wordcount": wordcount("\n".join(body_lines)),
            })

    return papers


def _attach_reviews(
    paper_id: int,
    reviews: list[dict[str, Any]],
    logged: list[dict[str, Any]],
    aliases: dict[str, str],
    pc_emails: set[str],
    warnings: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    log_by_email: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
    for lr in logged:
        log_by_email[canonical_email(lr.get("email"), aliases)].append(lr)
    matched_ids: set[int] = set()

    for r in reviews:
        author = canonical_email(r["raw_author_email"], aliases)
        r["author"] = author
        r["is_pc_member"] = author in pc_emails
        r["match_status"] = "unmatched"
        r["log_review_id"] = None
        r["assigned_at"] = None
        r["first_submitted_at"] = None
        r["last_edited_at"] = r["text_updated_at"]
        r["last_edit_source"] = "text_updated_field" if r["text_updated_at"] else "unknown"
        r["hotcrp_length"] = None
        r["first_submission_hotcrp_length"] = None

        matches = log_by_email.get(author, [])
        if len(matches) != 1:
            warnings.append(
                f"Review #{r['export_id']} ({author}) has "
                f"{len(matches)} matching log review records; timestamps left unresolved."
            )
            continue

        lr = matches[0]
        if lr["review_id"] in matched_ids:
            warnings.append(
                f"Review #{r['export_id']} would reuse log review ID "
                f"{lr['review_id']}; not matching twice."
            )
            continue
        matched_ids.add(lr["review_id"])
        r["match_status"] = "matched"
        r["log_review_id"] = lr["review_id"]
        r["assigned_at"] = lr.get("assigned_at")
        r["first_submitted_at"] = lr.get("first_submitted_at")
        r["hotcrp_length"] = lr.get("hotcrp_length")
        r["first_submission_hotcrp_length"] = lr.get("first_submission_hotcrp_length")
        r["currently_submitted_in_log"] = lr.get("currently_submitted")
        r["submission_events"] = lr.get("submission_events", [])

        # 'Updated:' is rendered by HotCRP if the review was subsequently
        # modified. If not present, the latest logged submission is the safest
        # available fallback, but must be marked as an inference.
        if not r["text_updated_at"]:
            r["last_edited_at"] = lr.get("last_submitted_at") or r["first_submitted_at"]
            r["last_edit_source"] = "last_submission_no_updated_marker"
        else:
            last_submit = lr.get("last_submitted_at")
            if last_submit and datetime.fromisoformat(last_submit) > datetime.fromisoformat(r["text_updated_at"]):
                warnings.append(
                    f"Review #{r['export_id']} has update marker older than last "
                    f"submission; using later submission."
                )
                r["last_edited_at"] = last_submit
                r["last_edit_source"] = "last_submission_after_text_update"
            if r["first_submitted_at"] and datetime.fromisoformat(r["last_edited_at"]) < datetime.fromisoformat(r["first_submitted_at"]):
                warnings.append(f"Review #{r['export_id']} has last edit before initial submission.")

    extras = [lr for lr in logged if lr["review_id"] not in matched_ids]
    return reviews, extras


def _attach_comments(
    paper_id: int,
    comments: list[dict[str, Any]],
    logged: list[dict[str, Any]],
    aliases: dict[str, str],
    pc_emails: set[str],
    warnings: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Reconcile per author and occurrence (HotCRP's @N != log comment ID).

    This is exact when the author count agrees and each author's comments are
    exported in posting order. If counts differ for an author, leave their
    records unmatched instead of guessing which of their comments is missing.
    """
    active_logs = [lc for lc in logged if not lc.get("deleted_at")]
    logs_by_author: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
    text_by_author: dict[str | None, list[dict[str, Any]]] = defaultdict(list)

    for lc in active_logs:
        logs_by_author[canonical_email(lc.get("author"), aliases)].append(lc)
    for c in comments:
        author = canonical_email(c["raw_author_email"], aliases)
        c["author"] = author
        c["is_pc_member"] = author in pc_emails
        c["log_comment_id"] = None
        c["posted_at"] = None
        c["posting_time_source"] = None
        c["match_status"] = "unmatched"
        text_by_author[author].append(c)

    # An author's consecutive comment occurrences are matched in order.
    # The entire text/log comment sequences may differ in interleaving,
    # for example author-facing comments appear at the end of a thread.
    matched_ids: set[int] = set()
    for author, texts in text_by_author.items():
        logs = logs_by_author.get(author, [])
        if len(texts) != len(logs):
            warnings.append(
                f"Paper {paper_id}: {author} has {len(texts)} visible "
                f"comments but {len(logs)} active logged comments; "
                "their timestamps were not inferred."
            )
            continue
        for t, lc in zip(texts, logs):
            t["log_comment_id"] = lc["comment_id"]
            t["posted_at"] = lc["timestamp"]
            t["posting_time_source"] = lc.get("timestamp_source", "first_logged_comment_edit")
            t["match_status"] = "matched_by_author_and_occurrence"
            t["thread_from_log"] = lc.get("thread")
            matched_ids.add(lc["comment_id"])

    # Diagnose interleaving mismatches even if all author-occurrence matches
    # are individually possible. Never assume text is globally chronological.
    matched_timeline = [
        datetime.fromisoformat(c["posted_at"])
        for c in comments if c["posted_at"]
    ]
    if matched_timeline != sorted(matched_timeline):
        warnings.append(
            f"Paper {paper_id}: visible comments are not globally ordered by "
            "posting time; matched by author and occurrence instead."
        )

    # Deleted / missing comments remain separately available as chronology
    # evidence, but they do NOT receive invented word counts.
    extras = [lc for lc in logged if lc["comment_id"] not in matched_ids]
    return comments, extras


def process_review_details(
    text_filename: str | Path,
    log_json_filename: str | Path,
) -> dict[str, Any]:
    """Build the JSON-ready result from two local files, without writing."""
    text_path = Path(text_filename).expanduser()
    log_path = Path(log_json_filename).expanduser()
    texts = parse_text_export(text_path)
    log = json.loads(log_path.read_text(encoding="utf-8-sig"))
    if not isinstance(log.get("papers"), dict):
        raise ValueError("Log JSON must have a 'papers' object.")
    if not isinstance(log.get("pc_members"), dict):
        raise ValueError("Log JSON must have a 'pc_members' object.")
    aliases = {
        normalize_email(k): normalize_email(v)
        for k, v in log.get("email_aliases", {}).items()
    }
    pc_members = log["pc_members"]
    pc_emails = {normalize_email(p) for p in pc_members}
    warnings: list[str] = []
    result_papers: dict[str, dict[str, Any]] = {}
    extra_reviews: list[dict[str, Any]] = []
    extra_comments: list[dict[str, Any]] = []
    log_only_papers = []

    for pid in sorted(texts):
        parsed = texts[pid]
        lp = log["papers"].get(str(pid), {})
        matched_reviews, unpaired_reviews = _attach_reviews(
            pid, parsed["reviews"], lp.get("reviewers", []),
            aliases, pc_emails, warnings,
        )
        matched_comments, unpaired_comments = _attach_comments(
            pid, parsed["comments"], lp.get("comments", []),
            aliases, pc_emails, warnings,
        )
        for review in unpaired_reviews:
            extra_reviews.append({"paper_id": pid, **review})
        for comment in unpaired_comments:
            extra_comments.append({"paper_id": pid, **comment})

        result_papers[str(pid)] = {
            "paper_id": pid,
            "has_log_record": bool(lp),
            "currently_submitted_in_log": lp.get("currently_submitted"),
            "reviews": matched_reviews,
            "comments": matched_comments,
            # Keep complete assignment intervals for the later per-round
            # autonomous-discussion-initiator calculation.
            "assignments": lp.get("assignments", []),
            # Historical/deleted comments can affect who intervened first;
            # users must exclude these from ordinary comment word statistics.
            "log_only_comments": unpaired_comments,
        }

    for pid, lp in sorted(log["papers"].items(), key=lambda kv: int(kv[0])):
        if int(pid) not in texts:
            log_only_papers.append(int(pid))
            for review in lp.get("reviewers", []):
                extra_reviews.append({"paper_id": int(pid), **review})
            for comment in lp.get("comments", []):
                extra_comments.append({"paper_id": int(pid), **comment})

    all_reviews = [r for p in result_papers.values() for r in p["reviews"]]
    all_comments = [c for p in result_papers.values() for c in p["comments"]]
    return {
        "schema_version": 1,
        "sources": {
            "hotcrp_text_export": text_path.name,
            "parsed_log_json": log_path.name,
        },
        "methodology": {
            "wordcount": (
                "Count Unicode word tokens, keeping internal apostrophes/hyphens; "
                "exclude form labels, one-line coded choice responses, and comment headers."
            ),
            "comment_timestamps": (
                "The earliest logged edit of a comment, not a separately logged "
                "creation event, is treated as its original posting time."
            ),
            "review_last_edit": (
                "Use the review export's explicit AoE Updated timestamp when "
                "present; otherwise fall back to the latest logged submission "
                "and label it as an inference."
            ),
            "matching": (
                "Reviews: (paper, normalized author email). Comments: "
                "(paper, normalized author email, occurrence among that author's "
                "comments). Different export/log review and comment IDs are retained."
            ),
            "non_pc_comments": (
                "Retained for discussion chronology, but excluded from eligible-PC "
                "activity totals downstream."
            ),
            "log_only_comments": (
                "Unmatched log comments including deletions are stored separately; "
                "they have no available final text word count."
            ),
        },
        "pc_members": pc_members,
        "email_aliases": aliases,
        "statistics": {
            "papers_in_text": len(result_papers),
            "papers_only_in_log": len(log_only_papers),
            "reviews_in_text": len(all_reviews),
            "reviews_matched_to_log": sum(r["match_status"] == "matched" for r in all_reviews),
            "reviews_explicitly_updated": sum(r["text_updated_at"] is not None for r in all_reviews),
            "reviews_by_eligible_pc": sum(r["is_pc_member"] for r in all_reviews),
            "comments_in_text": len(all_comments),
            "comments_matched_to_log": sum(c["match_status"] != "unmatched" for c in all_comments),
            "comments_by_eligible_pc": sum(c["is_pc_member"] for c in all_comments),
            "log_reviews_not_in_text": len(extra_reviews),
            "log_comments_not_in_text": len(extra_comments),
        },
        "warnings": warnings,
        "log_only_papers": log_only_papers,
        "log_reviews_not_in_text": extra_reviews,
        "log_comments_not_in_text": extra_comments,
        "papers": result_papers,
    }


def extract_review_details(
    text_filename: str | Path,
    log_json_filename: str | Path,
    output_folder: str | Path | None = None,
) -> Path:
    """Run the extractor and save <text_stem>_extracted.json."""
    text_path = Path(text_filename).expanduser()
    result = process_review_details(text_path, log_json_filename)
    directory = Path(output_folder).expanduser() if output_folder else text_path.parent
    directory.mkdir(parents=True, exist_ok=True)
    output_path = directory / f"{text_path.stem}_extracted.json"
    # Stable keys and ordering make identical runs byte-for-byte reproducible.
    with output_path.open("w", encoding="utf-8", newline="\n") as target:
        json.dump(result, target, indent=2, ensure_ascii=False)
        target.write("\n")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Join HotCRP review/comment text to locally extracted log timelines."
    )
    parser.add_argument("text_filename", help="HotCRP all-reviews .txt export")
    parser.add_argument("log_json_filename", help="JSON from log_extraction.py")
    parser.add_argument("-o", "--output-folder", default=None)
    args = parser.parse_args()
    output = extract_review_details(
        args.text_filename, args.log_json_filename, args.output_folder
    )
    print(f"Saved: {output}")


if __name__ == "__main__":
    main()
