# TOP REVIEWER: one self-contained Jupyter cell, using the existing `df`.
import json
import math
import pandas as pd
from IPython.display import display

# ---- USER CONFIGURATION --------------------------------------------------
BID_THRESHOLD = None          # Set an integer, e.g. 20. None = NOT YET EVALUATED.
TOP_PERCENT = 0.30
REQUIRED_POINTS = 6

# Optional manual inputs. None = unknown; an empty set/dict = verified none.
FIREFIGHTER_EMAILS = None     # e.g. {"one@university.edu", "two@company.com"}
UPVOTE_SCORES = None          # e.g. {"one@university.edu": 2, ...}
# UPVOTE_SCORES must cover EVERY reviewer with >=1 submitted review,
# including zeros; choose ONE consistent measure (e.g. total upvotes).
# --------------------------------------------------------------------------


def as_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return json.loads(value) if value.strip() else []
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return []
    raise ValueError(f"Expected a JSON list, got: {value!r}")


def timestamp(value):
    if not value:
        return None
    try:
        result = pd.Timestamp(value)
        return result.tz_convert("UTC") if result.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def topic_flag(value):
    value = str(value).strip().casefold()
    return {"true": True, "false": False}.get(value)  # N/A -> unknown


def on_time_flag(delays, review_count):
    if review_count == 0:
        return False
    # A known late submission is sufficient to fail, even if another
    # submission has incomplete timestamps.
    if any(isinstance(d, (int, float)) and not isinstance(d, bool)
           and d > 0 for d in delays):
        return False
    if len(delays) != review_count or any(
        isinstance(d, bool) or not isinstance(d, (int, float))
        or not math.isfinite(d) for d in delays
    ):
        return None
    return True


def early_all_flag(details, review_count):
    """All non-exempt reviews at least 48h before their ORIGINAL deadlines.

    Reviews assigned in the seven days before the deadline (or later)
    are exempt. At least one non-exempt review is required for a point.
    """
    if review_count == 0:
        return False
    if len(details) != review_count:
        return None
    evaluated = 0
    incomplete = False
    failed = False
    for review in details:
        assigned = timestamp(review.get("assigned_at"))
        submitted = timestamp(review.get("first_submitted_at"))
        deadline = timestamp(review.get("deadline"))
        if assigned is None or submitted is None or deadline is None:
            incomplete = True
            continue
        if deadline - assigned <= pd.Timedelta(days=7):
            continue  # late assignment: review exempt for THIS criterion
        evaluated += 1
        if submitted > deadline - pd.Timedelta(hours=48):
            failed = True
    if failed:
        return False
    if incomplete:
        return None
    return evaluated > 0


def top_cutoff(values, fraction=TOP_PERCENT):
    """Smallest score in top ceil(fraction*N); ties at cutoff included."""
    if not values:
        return None
    n = max(1, math.ceil(fraction * len(values)))
    return sorted(values, reverse=True)[n - 1]


# Read already-loaded DataFrame (JSON fields may already be decoded).
people = []
for _, row in df.iterrows():
    email = str(row["email"]).strip().casefold()
    reviews = as_list(row["reviews"])
    details = as_list(row["review_details"])
    delays = as_list(row["delays"])
    lengths = as_list(row["review_length"])
    bids = as_list(row["bids"])
    initiations = as_list(row["discussion_initiator"])
    n_reviews = len(reviews)

    valid_lengths = (
        n_reviews > 0 and len(lengths) == n_reviews
        and all(isinstance(w, (int, float)) and not isinstance(w, bool)
                and math.isfinite(w) and w >= 0 for w in lengths)
    )
    avg_length = (sum(lengths) / n_reviews) if valid_lengths else None
    comments = pd.to_numeric(row["all_comments_totCount"], errors="coerce")

    people.append({
        "reviewer": str(row["given_name"]).strip() + " " + str(row["family_name"]).strip(),
        "email": email,
        "n_bids": len(bids),
        "topics_set": topic_flag(row["topics?"]),
        "n_reviews": n_reviews,
        "n_comments": int(comments) if pd.notna(comments) else None,
        "avg_review_words": avg_length,
        "n_discussions_initiated": len(initiations),
        "no_late_reviews": on_time_flag(delays, n_reviews),
        "all_reviews_48h_early": early_all_flag(details, n_reviews),
    })

if len({r["email"] for r in people}) != len(people):
    raise ValueError("Duplicate PC email addresses in df")
if not (0 < TOP_PERCENT <= 1):
    raise ValueError("TOP_PERCENT must be between 0 and 1")
if BID_THRESHOLD is not None and (
    isinstance(BID_THRESHOLD, bool) or not isinstance(BID_THRESHOLD, int)
    or BID_THRESHOLD < 1
):
    raise ValueError("BID_THRESHOLD must be an integer >= 1, or None")

# Comments: rank against ALL eligible PC members (including zeros).
comment_cutoff = top_cutoff([
    r["n_comments"] for r in people if r["n_comments"] is not None
])

# Mean review length: rank among people who submitted >=1 review.
review_cutoff = top_cutoff([
    r["avg_review_words"] for r in people
    if r["n_reviews"] > 0 and r["avg_review_words"] is not None
])

# Upvotes are optional, but partial reporting would bias the percentile.
upvote_cutoff = None
if UPVOTE_SCORES is not None:
    upvotes = {str(k).strip().casefold(): v for k, v in UPVOTE_SCORES.items()}
    active_emails = {r["email"] for r in people if r["n_reviews"] > 0}
    missing = active_emails - set(upvotes)
    if missing:
        raise ValueError(
            f"UPVOTE_SCORES must cover all reviewers; missing {len(missing)} "
            f"(e.g., {sorted(missing)[0]})"
        )
    if any(not isinstance(upvotes[e], (int, float)) or isinstance(upvotes[e], bool)
           or not math.isfinite(upvotes[e]) or upvotes[e] < 0 for e in active_emails):
        raise ValueError("Upvote scores must be nonnegative finite numbers")
    upvote_cutoff = top_cutoff([upvotes[e] for e in active_emails])

firefighters = (
    None if FIREFIGHTER_EMAILS is None
    else {str(e).strip().casefold() for e in FIREFIGHTER_EMAILS}
)

criterion_names = [
    "C1_bids", "C2_topics", "C3_never_late", "C4_top30_comments",
    "C5_at_least_3_reviews", "C6_top30_avg_review_length",
    "C7_top30_upvotes", "C8_discussion_initiated",
    "C9_all_nonexempt_reviews_48h_early", "C10_extra_review",
]

results = []
for r in people:
    u = upvotes.get(r["email"]) if UPVOTE_SCORES is not None else None
    flags = [
        (r["n_bids"] >= BID_THRESHOLD) if BID_THRESHOLD is not None else None,
        r["topics_set"],
        r["no_late_reviews"],
        (r["n_comments"] > 0 and r["n_comments"] >= comment_cutoff)
        if r["n_comments"] is not None and comment_cutoff is not None else None,
        r["n_reviews"] >= 3,
        (r["avg_review_words"] > 0 and r["avg_review_words"] >= review_cutoff)
        if r["avg_review_words"] is not None and review_cutoff is not None else
        (False if r["n_reviews"] == 0 else None),
        (u > 0 and u >= upvote_cutoff) if u is not None and upvote_cutoff is not None
        else (False if UPVOTE_SCORES is not None and r["n_reviews"] == 0 else None),
        r["n_discussions_initiated"] >= 1,
        r["all_reviews_48h_early"],
        (r["email"] in firefighters) if firefighters is not None else None,
    ]
    known_points = sum(flag is True for flag in flags)
    unknown_count = sum(flag is None for flag in flags)
    possible_points = known_points + unknown_count
    status = (
        "QUALIFIED" if known_points >= REQUIRED_POINTS else
        "POSSIBLE" if possible_points >= REQUIRED_POINTS else
        "CANNOT QUALIFY"
    )
    results.append({
        **r,
        **dict(zip(criterion_names, flags)),
        "known_points": known_points,
        "unknown_criteria": unknown_count,
        "possible_points": possible_points,
        "status": status,
    })

top_reviewers_df = pd.DataFrame(results).sort_values(
    ["known_points", "possible_points", "email"],
    ascending=[False, False, True],
).reset_index(drop=True)

print(f"Top {TOP_PERCENT:.0%} cutoff: {comment_cutoff} comments; "
      f"{review_cutoff:.1f} average review words")
if BID_THRESHOLD is None:
    print("BID_THRESHOLD is unset: bids remain UNKNOWN, not false.")
if UPVOTE_SCORES is None or FIREFIGHTER_EMAILS is None:
    print("Manual upvotes / extra-review criteria remain UNKNOWN until provided.")
print("\nStatus counts:")
display(top_reviewers_df["status"].value_counts().rename_axis("status").to_frame("PC members"))
print("\nConfirmed and possible TOP REVIEWER candidates:")
display(top_reviewers_df.loc[
    top_reviewers_df["status"].ne("CANNOT QUALIFY"),
    ["reviewer", "email", "known_points", "unknown_criteria", "possible_points", "status"]
    + criterion_names
])

# Optionally export the complete, inspectable matrix:
# top_reviewers_df.to_csv("output/top_reviewer_criteria.csv", index=False)
