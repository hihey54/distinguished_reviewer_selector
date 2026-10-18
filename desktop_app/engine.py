"""Deterministic, standard-library recognition engine over pc_metrics.csv.

The five-stage extraction in code/main.py is authoritative for review activity,
submission timing, and discussion attribution. This module does not infer an
'extra' review: supplementary chair-provided email/paper pairs are authoritative.

The engine has no GUI dependencies and is independently unit-testable.
"""
from __future__ import annotations

import ast
import csv
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import io
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable


@dataclass(frozen=True)
class Metric:
    label: str
    kind: str = "number"  # number or boolean
    source: str | None = None  # optional source dependency


METRICS: dict[str, Metric] = {
    "bids": Metric("Paper bids"),
    "topics_explicit_count": Metric("Topic preferences explicitly set"),
    "all_topics": Metric("Preferences for ALL topics", "boolean"),
    "reviews": Metric("Submitted reviews"),
    "on_time": Metric("No unexcused late reviews", "boolean"),
    "formal_late_reviews": Metric("Formal late reviews"),
    "delay_override": Metric("Chair-certified delay exception", "boolean"),
    "comments": Metric("Posted comments"),
    "comment_words": Metric("Cumulative comment words"),
    "review_words": Metric("Cumulative review words"),
    "avg_review_words": Metric("Average review words"),
    "upvotes": Metric("Received signed upvotes", source="upvotes"),
    "distinct_papers_initiated": Metric("Distinct papers with initiated discussion"),
    "ordinary_reviews": Metric("Ordinary reviews", source="firefighters"),
    "extra_reviews": Metric("Explicitly identified extra reviews", source="firefighters"),
    "second_review_hours": Metric("Hours from round start to second review"),
    "early_all_ordinary_reviews": Metric("All ordinary reviews early (48 hours)", "boolean", "firefighters"),
    "min_ordinary_review_hours_early": Metric("Minimum hours early across ordinary reviews", source="firefighters"),
    "spotlight_voted": Metric("Voted for spotlight selection", "boolean", "spotlight"),
}

OP_LABELS = {
    "ge": "at least (>=)",
    "le": "at most (<=)",
    "eq": "equals (=)",
    "true": "is true",
    "false": "is false",
    "top_n_high": "top N (highest)",
    "top_n_low": "top N (lowest)",
    "top_pct_high": "top percent (highest)",
    "top_pct_low": "top percent (lowest)",
}

RANKING_OPS = {"top_n_high", "top_n_low", "top_pct_high", "top_pct_low"}
BOOL_OPS = {"true", "false"}


def norm(email: str | None) -> str:
    return str(email or "").strip().casefold()


def paper_key(paper: Any) -> str:
    s = str(paper).strip()
    if not re.fullmatch(r"[0-9]+", s) or int(s) <= 0:
        raise ValueError(f"Invalid positive paper ID: {paper!r}")
    return str(int(s))


def read_csv(path: str | Path, required: Iterable[str]) -> list[dict[str, str]]:
    p = Path(path)
    with p.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        fields = [v.strip() for v in (reader.fieldnames or [])]
        missing = set(required) - set(fields)
        if missing:
            raise ValueError(f"{p.name}: required CSV columns missing: {sorted(missing)}")
        rows = []
        for n, row in enumerate(reader, start=2):
            if None in row:
                raise ValueError(f"{p.name}, row {n}: excess columns")
            rows.append({str(k).strip(): str(v or "").strip() for k, v in row.items()})
        return rows


def json_value(row: dict[str, str], key: str, expect: type) -> Any:
    s = row.get(key, "").strip()
    if not s:
        return expect()
    try:
        value = json.loads(s)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {key} for {row.get('email', '')}") from exc
    if not isinstance(value, expect):
        raise ValueError(f"Expected {expect.__name__} in {key} for {row.get('email', '')}")
    return value


def numeric(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def parse_stamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        s = value.strip().replace("Z", "+00:00")
        stamp = datetime.fromisoformat(s)
        return stamp.astimezone(timezone.utc) if stamp.tzinfo else None
    except ValueError:
        return None


def email_lines(path: str | Path) -> list[str]:
    """Read one-email-per-line, single-column CSV or Python literal list."""
    raw = Path(path).read_text(encoding="utf-8-sig").strip()
    if not raw:
        return []
    text = re.sub(r"^\s*voted\s*=\s*", "", raw)
    if text.lstrip().startswith(("[", "(", "{")):
        try:
            values = ast.literal_eval(text)
        except (ValueError, SyntaxError) as exc:
            raise ValueError(f"Invalid email list in {path}") from exc
        if isinstance(values, dict):
            values = values.get("voted", values.get("emails"))
        if not isinstance(values, (set, list, tuple)):
            raise ValueError(f"Expected a list of emails in {path}")
        return [norm(v) for v in values]
    rows = [r for r in csv.reader(io.StringIO(raw)) if r and r[0].strip() and not r[0].lstrip().startswith("#")]
    if rows and rows[0][0].strip().lower() in ("email", "emails"):
        rows = rows[1:]
    if len(rows) == 1 and len(rows[0]) > 1:
        return [norm(e) for e in rows[0] if e.strip()]
    return [norm(r[0]) for r in rows]


def approved_aliases(raw_text: str) -> dict[str, str]:
    """Editable newline-separated pairs: alias_email,canonical_pc_email."""
    mapping = {}
    for n, line in enumerate(raw_text.splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = [norm(v) for v in next(csv.reader([line]))]
        if len(fields) != 2 or not all(fields):
            raise ValueError(f"Alias line {n}: use alias@email,canonical@email")
        if fields[0] in mapping and mapping[fields[0]] != fields[1]:
            raise ValueError(f"Conflicting alias for {fields[0]}")
        mapping[fields[0]] = fields[1]
    return mapping


@dataclass
class Reviewer:
    email: str
    given_name: str
    family_name: str
    affiliation: str
    metrics: dict[str, Any] = field(default_factory=dict)
    audit: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return (self.given_name + " " + self.family_name).strip()


@dataclass
class ProgramData:
    reviewers: list[Reviewer]
    topics_total: int
    available: set[str]
    excluded: list[str]
    warnings: list[str]
    raw_review_count: int

    def need(self, metric: str) -> str | None:
        if metric not in METRICS:
            raise ValueError(f"Unknown metric: {metric}")
        source = METRICS[metric].source
        if source and source not in self.available:
            return source
        return None


def _valid_emails(source: set[str], pcs: set[str], aliases: dict[str, str], label: str) -> dict[str, str]:
    out = {}
    for item in source:
        mapped = aliases.get(item, item)
        if mapped not in pcs:
            raise ValueError(f"{label}: unknown PC email {item!r}. Add an alias or fix the source.")
        out[item] = mapped
    return out


def load_program(
    pc_metrics: str | Path,
    pc_info: str | Path,
    *,
    firefighters: str | Path | None = None,
    upvotes: str | Path | None = None,
    spotlight: str | Path | None = None,
    delay_overrides: str | Path | None = None,
    discussion_corrections: str | Path | None = None,
    upvote_corrections: str | Path | None = None,
    alias_text: str = "",
    exclude_chairs: bool = True,
) -> ProgramData:
    """Join per-PC metrics to original HotCRP roster and optional chair inputs.

    Unknown manual email addresses or unmatched firefighter paper IDs fail fast.
    Chair records from the original roster are accepted but not scored when
    exclude_chairs=True. No real-venue corrections are hard-coded.
    """
    pc_rows = read_csv(pc_info, ["email", "given_name", "family_name"])
    pc_index = {norm(r["email"]): r for r in pc_rows}
    if len(pc_index) != len(pc_rows) or "" in pc_index:
        raise ValueError("PC info contains duplicate or blank reviewer emails")
    topics = [k for k in (pc_rows[0] if pc_rows else {}) if k.casefold().startswith("topic:")]
    if not topics:
        raise ValueError("No topic: columns in the HotCRP PC roster")
    metric_rows = read_csv(pc_metrics, ["email", "given_name", "family_name", "review_details", "reviews", "delays", "bids"])
    index = {norm(r["email"]): r for r in metric_rows}
    if len(index) != len(metric_rows) or "" in index:
        raise ValueError("PC metrics contain duplicate or blank emails")
    if set(index) != set(pc_index):
        raise ValueError("HotCRP PC export and pipeline metrics disagree on PC email roster. Rerun extraction with this PC info file.")
    aliases = approved_aliases(alias_text)
    for original, target in aliases.items():
        if target not in pc_index or (original in pc_index and original != target):
            raise ValueError(f"Invalid alias {original!r} -> {target!r}")
    excluded = sorted(email for email, raw in pc_index.items()
                      if exclude_chairs and "chair" in (raw.get("roles") or "").casefold().split())
    pcs = set(pc_index) - set(excluded)
    # Unknown addresses in manually provided sources are not silently ignored.
    # Entries for known excluded chairs are harmless and are discarded.
    def canonical(email: str, label: str) -> str:
        key = norm(email)
        value = aliases.get(key, key)
        if value not in pc_index:
            raise ValueError(f"{label}: unknown email {email!r}; add an email alias if needed")
        return value

    available: set[str] = set()
    warnings: list[str] = []
    extra_pairs: set[tuple[str, str]] = set()
    if firefighters:
        available.add("firefighters")
        for row in read_csv(firefighters, ["email", "paper"]):
            email = canonical(row["email"], "Extra review list")
            if email not in pcs:
                continue
            extra_pairs.add((email, paper_key(row["paper"])))
    upvote_map: dict[str, int] = {}
    upvote_original_map: dict[str, int] = {}
    if upvotes:
        available.add("upvotes")
        for row in read_csv(upvotes, ["email", "#upvotes"]):
            email = canonical(row["email"], "Upvotes")
            if email not in pcs:
                continue
            value = row["#upvotes"] or "0"
            try:
                vote = int(value)
            except ValueError as exc:
                raise ValueError(f"Invalid signed upvote integer for {email}: {value!r}") from exc
            if email in upvote_map:
                raise ValueError(f"Duplicate upvotes row for {email}")
            upvote_map[email] = vote
    upvote_original_map = dict(upvote_map)
    if upvote_corrections:
        available.add("upvotes")
        for row in read_csv(upvote_corrections, ["email", "#upvotes"]):
            email = canonical(row["email"], "Upvote corrections")
            if email not in pcs:
                continue
            try:
                upvote_map[email] = int(row["#upvotes"])
            except ValueError as exc:
                raise ValueError(f"Bad corrected upvote total for {email}") from exc
        if not upvotes:
            warnings.append("Upvote corrections supplied without a complete upvote file: unlisted reviewers have unknown upvote totals.")
            # Restrict availability unless a complete file was supplied.
            available.discard("upvotes")
    voted: set[str] = set()
    if spotlight:
        available.add("spotlight")
        for email in email_lines(spotlight):
            resolved = canonical(email, "Spotlight voters")
            if resolved in pcs:
                voted.add(resolved)
    delays: set[str] = set()
    if delay_overrides:
        for email in email_lines(delay_overrides):
            resolved = canonical(email, "Chair-approved delay exemptions")
            if resolved in pcs:
                delays.add(resolved)
    discussion_minimums: dict[str, int] = {}
    if discussion_corrections:
        for row in read_csv(discussion_corrections, ["email", "minimum_distinct_papers"]):
            email = canonical(row["email"], "Discussion corrections")
            if email not in pcs:
                continue
            try:
                minimum = int(row["minimum_distinct_papers"])
                if minimum < 1:
                    raise ValueError()
            except ValueError as exc:
                raise ValueError(f"Invalid minimum distinct-paper initiations for {email}") from exc
            discussion_minimums[email] = max(discussion_minimums.get(email, 0), minimum)
    reviewers: list[Reviewer] = []
    found_pairs: set[tuple[str, str]] = set()
    submitted_total = 0
    for email in sorted(pcs):
        row = index[email]
        raw = pc_index[email]
        bids = json_value(row, "bids", list)
        reviews = json_value(row, "reviews", list)
        delays_row = json_value(row, "delays", list)
        details = json_value(row, "review_details", list)
        initiation = json_value(row, "discussion_initiator", list)
        review_lens = json_value(row, "review_length", list)
        n_reviews = len(reviews)
        submitted_total += n_reviews
        explicit = sum(str(raw.get(t, "")).strip().lower() not in ("", "none") for t in topics)
        # Activity completeness remains explicit: an unavailable metric is never fabricated.
        known_delay = [numeric(x) for x in delays_row]
        on_time_original: bool | None = False if n_reviews == 0 else None
        if n_reviews and len(known_delay) == n_reviews:
            if any(x is not None and x > 0 for x in known_delay):
                on_time_original = False
            elif all(x is not None for x in known_delay):
                on_time_original = True
        formal_late_count = sum(x is not None and x > 0 for x in known_delay)
        override = email in delays
        on_time = True if override and n_reviews >= 1 else on_time_original
        valid_lengths = (len(review_lens) == n_reviews
                         and all(numeric(x) is not None and numeric(x) >= 0 for x in review_lens))
        total_words = sum(float(v) for v in review_lens) if valid_lengths else None
        avg_words = total_words / n_reviews if total_words is not None and n_reviews else None
        comments = json_value(row, "comments", dict)
        more_comments = json_value(row, "extra_comments", dict)
        comment_lengths = json_value(row, "comments_length", dict)
        extra_lengths = json_value(row, "extra_comments_length", dict)
        count = sum(int(v) for data in (comments, more_comments) for v in data.values())
        word_values = [numeric(v) for data in (comment_lengths, extra_lengths) for values in data.values() for v in values]
        words = sum(word_values) if all(x is not None for x in word_values) else None
        initiated = len({paper_key(r["paper_id"]) for r in initiation if r.get("paper_id") is not None})
        initiated_effective = max(initiated, discussion_minimums.get(email, 0))
        second_hours = None
        if n_reviews >= 2 and len(details) == n_reviews:
            record_pairs = []
            for d in details:
                submitted = parse_stamp(d.get("first_submitted_at"))
                if submitted is None:
                    record_pairs = []
                    break
                record_pairs.append((submitted, d))
            if len(record_pairs) == n_reviews:
                record_pairs.sort(key=lambda item: (item[0], str(item[1].get("paper_id", ""))))
                submitted, detail = record_pairs[1]
                start = parse_stamp(detail.get("round_start"))
                if start:
                    second_hours = max(0.0, (submitted - start).total_seconds() / 3600.0)
        ordinary_count = 0
        extra_count = 0
        enough_details = len(details) == n_reviews
        early = True
        ordinary_exists = False
        ordinary_early_hours: list[float] = []
        for d in details:
            if d.get("paper_id") is None:
                enough_details = False
                continue
            key = (email, paper_key(d["paper_id"]))
            found_pairs.add(key)
            is_extra = key in extra_pairs
            if is_extra:
                extra_count += 1
            else:
                ordinary_count += 1
                ordinary_exists = True
                deadline = parse_stamp(d.get("deadline"))
                submitted = parse_stamp(d.get("first_submitted_at"))
                if deadline is None or submitted is None:
                    early = None
                else:
                    ordinary_early_hours.append((deadline - submitted).total_seconds() / 3600.0)
                    if early is not None and submitted > deadline - timedelta(hours=48):
                        early = False
        # The configured EARLY threshold is evaluated using the actual review times.
        # Store per-review timing for any criteria-specific early-hour value.
        events = []
        for d in details:
            key = paper_key(d["paper_id"]) if d.get("paper_id") is not None else ""
            events.append({
                "paper": key,
                "extra": (email, key) in extra_pairs,
                "deadline": d.get("deadline"),
                "submitted": d.get("first_submitted_at"),
            })
        early_margin = (min(ordinary_early_hours) if enough_details and "firefighters" in available
                        and ordinary_exists and len(ordinary_early_hours) == ordinary_count else None)
        early = (bool(early and ordinary_exists) if early_margin is not None else None)
        metrics: dict[str, Any] = {
            "bids": len(bids),
            "topics_explicit_count": explicit,
            "all_topics": explicit == len(topics),
            "reviews": n_reviews,
            "on_time": on_time,
            "formal_late_reviews": formal_late_count,
            "delay_override": override,
            "comments": count,
            "comment_words": words,
            "review_words": total_words,
            "avg_review_words": avg_words,
            "upvotes": upvote_map.get(email, 0) if "upvotes" in available else None,
            "distinct_papers_initiated": initiated_effective,
            "ordinary_reviews": ordinary_count if "firefighters" in available and enough_details else None,
            "extra_reviews": extra_count if "firefighters" in available and enough_details else None,
            "second_review_hours": second_hours,
            "early_all_ordinary_reviews": early,
            "min_ordinary_review_hours_early": early_margin,
            "spotlight_voted": email in voted if "spotlight" in available else None,
        }
        reviewer = Reviewer(
            email=email,
            given_name=raw.get("given_name", "") or row.get("given_name", ""),
            family_name=raw.get("family_name", "") or row.get("family_name", ""),
            affiliation=raw.get("affiliation", ""),
            metrics=metrics,
            audit={
                "original_topics_all": row.get("topics?"),
                "original_late_reviews": formal_late_count,
                "delay_override": override,
                "manual_extra_papers": sorted(p for e, p in extra_pairs if e == email),
                "automated_initiated_papers": initiated,
                "discussion_minimum_override": discussion_minimums.get(email, 0),
                "upvotes_original": upvote_original_map.get(email, 0),
                "upvote_corrected": upvote_original_map.get(email, 0) != upvote_map.get(email, 0),
                "review_events": events,
                "topic_fields": {t: raw.get(t, "") for t in topics},
            },
        )
        reviewers.append(reviewer)
    if extra_pairs - found_pairs:
        missing = sorted(extra_pairs - found_pairs)
        raise ValueError(f"Extra-review CSV identifies email/paper pairs not present among submitted reviews: {missing[:12]}")
    if len(reviewers) == 0:
        raise ValueError("No eligible PC reviewers found (check chair exclusions and roster)")
    if len(excluded):
        warnings.append(f"Excluded {len(excluded)} chair-role account(s) from awards")
    return ProgramData(reviewers, len(topics), available, excluded, warnings, submitted_total)


@dataclass
class Rule:
    metric: str
    op: str
    value: float = 0
    enabled: bool = True
    population: str = "all"  # all, reviewed
    require_positive: bool = False

    def validate(self) -> None:
        if self.metric not in METRICS:
            raise ValueError(f"Unknown metric: {self.metric}")
        if self.op not in OP_LABELS:
            raise ValueError(f"Unknown operator: {self.op}")
        if self.op in BOOL_OPS and METRICS[self.metric].kind != "boolean":
            raise ValueError(f"{self.metric}: boolean comparison requires a boolean metric")
        if (self.op in RANKING_OPS or self.op in {"ge", "le", "eq"}) and METRICS[self.metric].kind != "number":
            raise ValueError(f"{self.metric}: ranking requires a numeric metric")
        if self.op in RANKING_OPS | {"ge", "le", "eq"}:
            if not math.isfinite(float(self.value)):
                raise ValueError(f"{self.metric}: invalid numeric cutoff")
            if self.op.startswith("top_n") and (self.value < 1 or float(self.value) != int(self.value)):
                raise ValueError(f"{self.metric}: top N must be a positive whole number")
            if self.op.startswith("top_pct") and not (0 < float(self.value) <= 100):
                raise ValueError(f"{self.metric}: percentile must be 0-100 (exclusive of 0)")


@dataclass
class AwardConfig:
    key: str
    description: str
    mode: str = "criteria"  # criteria, ranked, points
    filters: list[Rule] = field(default_factory=list)
    rankings: list[Rule] = field(default_factory=list)
    points: list[Rule] = field(default_factory=list)
    match_rankings: str = "any"  # any/all
    min_points: int = 8
    include_ties: bool = True

    def validate(self) -> None:
        if self.mode not in {"criteria", "ranked", "points"}:
            raise ValueError(f"Invalid award mode: {self.mode}")
        if self.mode == "ranked" and not any(x.enabled for x in self.rankings):
            raise ValueError(f"{self.key}: enable at least one ranked condition")
        if self.mode == "criteria" and not any(x.enabled for x in self.filters):
            raise ValueError(f"{self.key}: enable at least one mandatory criterion")
        if self.mode == "points" and not (1 <= self.min_points <= sum(x.enabled for x in self.points)):
            raise ValueError(f"{self.key}: points threshold must be between 1 and number of enabled scored criteria")
        for rule in self.filters + self.rankings + self.points:
            rule.validate()


def default_awards() -> list[AwardConfig]:
    """Faithful defaults for the eight final AISec recognition definitions.

    All configured rules can be switched off, removed, or expanded in the GUI.
    No venue-specific names, votes, or overrides appear in the defaults.
    """
    def r(metric: str, op: str, value: float = 0, **kw) -> Rule:
        return Rule(metric, op, value, **kw)
    return [
        AwardConfig("SPEEDY", "Rewards PC members who submit two reviews particularly quickly.", mode="ranked",
                    filters=[r("reviews", "ge", 2), r("on_time", "true")],
                    rankings=[r("second_review_hours", "top_n_low", 7)]),
        AwardConfig("COMMENTER", "Rewards sustained participation through comments and discussion.", mode="ranked",
                    rankings=[r("comment_words", "top_n_high", 7, require_positive=True),
                              r("comments", "top_n_high", 7, require_positive=True)]),
        AwardConfig("ELABORATE", "Rewards thorough reviews, measured by cumulative review wordcount.", mode="ranked",
                    filters=[r("reviews", "ge", 1), r("on_time", "true")],
                    rankings=[r("review_words", "top_n_high", 7)]),
        AwardConfig("COMMITTED", "Rewards bidding, topic preferences, reviewing, and responsible timeliness.",
                    filters=[r("bids", "ge", 10), r("all_topics", "true"),
                             r("reviews", "ge", 2), r("on_time", "true")]),
        AwardConfig("ALACRITOUS", "Rewards independently initiating discussion on distinct assigned papers.",
                    filters=[r("distinct_papers_initiated", "ge", 2)]),
        AwardConfig("FIREFIGHTER", "Rewards handling chair-identified emergency/tiebreaker reviews.",
                    filters=[r("ordinary_reviews", "ge", 2), r("extra_reviews", "ge", 1)]),
        AwardConfig("APPRECIATED", "Rewards reviewers receiving the most peer upvotes.", mode="ranked",
                    filters=[r("reviews", "ge", 1)],
                    rankings=[r("upvotes", "top_n_high", 7)]),
        AwardConfig("DISTINGUISHED", "Rewards broad excellence across independent PC contributions.", mode="points",
                    min_points=8,
                    points=[
                        r("bids", "ge", 10),
                        r("all_topics", "true"),
                        r("on_time", "true"),
                        r("comments", "top_pct_high", 30, require_positive=True),
                        r("reviews", "ge", 3),
                        r("avg_review_words", "top_pct_high", 30, require_positive=True),
                        r("upvotes", "top_pct_high", 30, population="reviewed"),
                        r("distinct_papers_initiated", "ge", 1),
                        r("min_ordinary_review_hours_early", "ge", 48),
                        r("extra_reviews", "ge", 1),
                        r("spotlight_voted", "true"),
                        r("comment_words", "top_pct_high", 30, require_positive=True),
                    ]),
    ]


@dataclass
class ReviewResult:
    config: AwardConfig
    recipients: set[str]
    rows: list[dict[str, Any]]
    warning: str = ""


def _cutoff_for(rule: Rule, population: list[Reviewer]) -> tuple[float | None, dict[str, int]]:
    """Calculate cutoff and competition ranks for a ranked rule.

    A top-N rule chooses the Nth ordered value, and includes ties if enabled.
    Top-percent rules select ceil(percent * valid population), including ties.
    """
    eligible = [p for p in population if (rule.population != "reviewed" or p.metrics["reviews"] > 0)
                and numeric(p.metrics.get(rule.metric)) is not None]
    descending = rule.op.endswith("high")
    eligible.sort(key=lambda p: ((-1 if descending else 1) * float(p.metrics[rule.metric]), p.email))
    if not eligible:
        return None, {}
    if rule.op.startswith("top_n"):
        nth = min(int(rule.value), len(eligible))
    else:
        nth = max(1, math.ceil((rule.value / 100) * len(eligible)))
    boundary = float(eligible[nth - 1].metrics[rule.metric])
    ranks: dict[str, int] = {}
    for pos, p in enumerate(eligible, start=1):
        ranks[p.email] = pos
    return boundary, ranks


def _match(rule: Rule, value: Any, cutoff: float | None,
           rank: int | None, include_ties: bool) -> bool | None:
    if value is None:
        return None
    if rule.op == "true":
        return value is True
    if rule.op == "false":
        return value is False
    x = numeric(value)
    if x is None:
        return None
    if rule.op == "ge":
        return x >= float(rule.value)
    if rule.op == "le":
        return x <= float(rule.value)
    if rule.op == "eq":
        return x == float(rule.value)
    if cutoff is None:
        return False
    if rule.require_positive and x <= 0:
        return False
    if rule.op.endswith("high"):
        qualifies = x >= cutoff
    else:
        qualifies = x <= cutoff
    if not include_ties and rule.op.startswith("top_n"):
        qualifies = rank is not None and rank <= int(rule.value)
    return qualifies


def evaluate_award(data: ProgramData, config: AwardConfig) -> ReviewResult:
    config.validate()
    active_rules = [r for r in config.filters + config.rankings + config.points if r.enabled]
    missing_sources = sorted({v for r in active_rules if (v := data.need(r.metric))})
    if missing_sources:
        raise ValueError(f"{config.key}: required optional data not supplied: {', '.join(missing_sources)}. "
                         "Choose the file(s) or turn off the relevant criteria.")
    filters = [r for r in config.filters if r.enabled]
    ranking_rules = [r for r in config.rankings if r.enabled]
    point_rules = [r for r in config.points if r.enabled]
    base: dict[str, bool | None] = {}
    for reviewer in data.reviewers:
        values = [_match(r, reviewer.metrics.get(r.metric), None, None, config.include_ties) for r in filters]
        base[reviewer.email] = (False if False in values else None if None in values else True)
    population = [p for p in data.reviewers if base[p.email] is True]
    cutoffs: list[float | None] = []
    ranks: list[dict[str, int]] = []
    for r in ranking_rules + point_rules:
        if r.op in RANKING_OPS:
            # Distinguished percentile ranks are measured across the entire PC,
            # not restricted by other points/constraints.
            group = population if config.mode == "ranked" else data.reviewers
            cutoff, rank = _cutoff_for(r, group)
        else:
            cutoff, rank = None, {}
        cutoffs.append(cutoff)
        ranks.append(rank)
    rows = []
    recipients: set[str] = set()
    for p in data.reviewers:
        email = p.email
        filt = base[email]
        detailed: dict[str, Any] = {}
        for n, r in enumerate(filters, start=1):
            detailed[f"filter_{n}:{r.metric}"] = _match(r, p.metrics.get(r.metric), None, None, config.include_ties)
        flags = []
        for i, r in enumerate(ranking_rules + point_rules):
            if r.op in RANKING_OPS and config.mode == "ranked" and filt is False:
                flag = False
            else:
                flag = _match(r, p.metrics.get(r.metric), cutoffs[i], ranks[i].get(email), config.include_ties)
                if r.op in RANKING_OPS and email not in ranks[i]:
                    flag = False if p.metrics.get(r.metric) is not None else None
            flags.append(flag)
            detailed[f"rule_{i+1}:{r.metric}"] = flag
        if config.mode == "points":
            known = sum(x is True for x in flags)
            unknown = sum(x is None for x in flags)
            meets: bool | None = (False if filt is False else
                                   True if known >= config.min_points else
                                   None if known + unknown >= config.min_points or filt is None else False)
            score: Any = f"{known}/{len(point_rules)}" + (f" (+{unknown} unknown)" if unknown else "")
        elif config.mode == "ranked":
            if config.match_rankings == "all":
                matches: bool | None = False if False in flags else None if None in flags else True
            else:
                matches = True if True in flags else None if None in flags else False
            meets = False if filt is False else None if filt is None else matches
            rank_values = [ranks[i].get(email) for i in range(len(ranking_rules))]
            score = min((n for n in rank_values if n is not None), default=None)
        else:
            meets = filt
            score = sum(x is True for x in detailed.values())
        if meets is True:
            recipients.add(email)
        details_row = {k: p.metrics.get(k) for k in METRICS}
        details_row.update(detailed)
        rows.append({"name": p.name, "email": email, "affiliation": p.affiliation,
                     "status": "SELECTED" if meets is True else "POSSIBLE" if meets is None else "NOT SELECTED",
                     "score": score, **details_row})
    rows.sort(key=lambda x: (x["status"] != "SELECTED", x["status"] == "POSSIBLE", 
                             -(int(str(x["score"]).split("/")[0]) if config.mode == "points" else 0),
                             x["score"] if config.mode == "ranked" and x["score"] is not None else math.inf,
                             x["name"].casefold(), x["email"]))
    return ReviewResult(config, recipients, rows)


def award_report(data: ProgramData, configs: list[AwardConfig]) -> tuple[list[ReviewResult], list[dict[str, Any]], dict[str, int]]:
    results = [evaluate_award(data, c) for c in configs]
    membership = {p.email: [res.config.key for res in results if p.email in res.recipients] for p in data.reviewers}
    rows = []
    for p in sorted(data.reviewers, key=lambda x: (x.family_name.casefold(), x.given_name.casefold(), x.email)):
        marks = membership[p.email]
        entry = {"given_name": p.given_name, "family_name": p.family_name,
                 "name": p.name, "email": p.email, "affiliation": p.affiliation}
        entry.update({c.key: int(c.key in marks) for c in configs})
        entry["recognitions"] = " | ".join(marks)
        entry["award_count"] = len(marks)
        rows.append(entry)
    totals = {res.config.key: len(res.recipients) for res in results}
    return results, rows, totals


def _excel_safe(value: Any) -> Any:
    """Prevent CSV-formula injection when opened in Excel or similar software."""
    if isinstance(value, str) and value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def export_csv(target: str | Path, rows: list[dict[str, Any]], configs: list[AwardConfig]) -> None:
    output = Path(target)
    output.parent.mkdir(parents=True, exist_ok=True)
    columns = ["given_name", "family_name", "name", "email", "affiliation"]
    columns += [c.key for c in configs]
    columns += ["recognitions", "award_count"]
    with output.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, columns)
        writer.writeheader()
        writer.writerows([{k: _excel_safe(v) for k, v in row.items()} for row in rows])
