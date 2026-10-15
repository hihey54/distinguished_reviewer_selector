# Code reference

This directory contains the Python implementation of the **HotCRP PC Activity Analyzer**. The software reads locally downloaded HotCRP exports, reconstructs reviewing activity, and produces per-PC statistics that can be inspected independently.

For the project overview and instructions for obtaining the HotCRP exports, see [the root README](../README.md). This document is intended for anyone maintaining the scripts, inspecting their assumptions, or running individual processing stages.

## Requirements and directory layout

- **Python 3.10+**.
- **Python standard library only** for the scripts in this directory. No package installation, network access, or AI service is needed.
- Still, the ``requirements.txt`` file (in the root folder) has useful packages for running Jupyter Notebooks to inspect results
- Four HotCRP exports: PC roster CSV, preferences CSV, activity log CSV, and reviews/comments text export.
- A venue-specific reviewing schedule, supplied either as editable variables in `main.py` or as command-line arguments.

Run commands from the **repository root**, unless you adjust the relative paths. A typical layout is:

```text
project/
├── README.md
├── code/
│   ├── README.md
│   ├── main.py
│   ├── pc_extraction.py
│   ├── bids_extraction.py
│   ├── log_extraction.py
│   ├── reviews_extraction.py
│   ├── pc_metrics.py
│   └── pc_recognitions.py       # Optional; NOT called by main.py
├── data/                         # Private, real HotCRP exports
├── sample_data/                  # Fictional sampleVenue exports
└── output/                       # Generated locally
```

The exact venue prefix is not important to the parsers, but recognizable filenames such as `sampleVenue-pcinfo.csv`, `sampleVenue-allprefs.csv`, `sampleVenue-log.csv`, and `sampleVenue-reviews.txt` allow `main.py` to find files automatically.

## End-to-end pipeline: `main.py`

`main.py` imports and calls **five** modules in order. It passes the exact intermediate file paths between stages, so later stages do not need to guess which generated file to use.

```text
sampleVenue-pcinfo.csv
    │
    └── pc_extraction.py ──> sampleVenue-pcinfo_topics.csv
                                  │
sampleVenue-allprefs.csv ──> bids_extraction.py
                                  │
                                  └──> sampleVenue-pcinfo_topics_bids.csv

sampleVenue-pcinfo.csv + sampleVenue-log.csv
    └── log_extraction.py ──> sampleVenue-log_parsed.json

sampleVenue-reviews.txt + sampleVenue-log_parsed.json
    └── reviews_extraction.py ──> sampleVenue-reviews_extracted.json

sampleVenue-pcinfo_topics_bids.csv + sampleVenue-reviews_extracted.json
    └── pc_metrics.py ──> pc_metrics.csv
                          pc_metrics_summary.json
```

### Running the entire pipeline

Edit the configuration near the top of `code/main.py`, especially the round starts and deadlines, and run:

```bash
python code/main.py
```

Or override the settings on the command line. The following example uses the **fictional two-round schedule** supplied with `sample_data/`:

```bash
python code/main.py \
    --input-dir sample_data \
    --output-dir sample_output \
    --min-reviews 2 3 \
    --round-starts 2026-07-01T00:00:00-12:00 2026-07-25T00:00:00-12:00 \
    --deadlines 2026-07-20T23:59:59-12:00 2026-08-10T23:59:59-12:00 \
    --emergency-grace-days 2 \
    --min-review-words 50 \
    --min-comment-words 15
```

Use your venue's actual schedule for real data. The sample dates are not AISec dates. For the complete argument list, run `python code/main.py --help`.

### Editable settings and CLI overrides

| In-script setting | CLI option | Meaning |
|---|---|---|
| `INPUT_DIR` | `--input-dir` | Directory containing the four exports (default: `data`). |
| `OUTPUT_DIR` | `-o`, `--output-dir` | Destination for all generated files (default: `output`). |
| `PC_CSV` | `--pc` | Optional explicit PC roster path. |
| `PREFERENCES_CSV` | `--prefs` | Optional explicit preferences path. |
| `LOG_CSV` | `--log` | Optional explicit activity-log path. |
| `REVIEWS_TXT` | `--reviews` | Optional explicit text-export path. |
| `MIN_REVIEWS` | `--min-reviews` | Strictly increasing submitted-review thresholds, one per configured round. |
| `ROUND_STARTS` | `--round-starts` | Start time of each configured round. |
| `DEADLINES` | `--deadlines` | Ordinary submission deadline of each configured round. |
| `EMERGENCY_GRACE_DAYS` | `--emergency-grace-days` | How many days before a deadline an assignment becomes emergency-exempt. |
| `MIN_REVIEW_WORDS` | `--min-review-words` | Minimum initial submitted-review length for discussion-initiation credit. |
| `MIN_COMMENT_WORDS` | `--min-comment-words` | Minimum comment length for discussion-initiation credit. |

`MIN_REVIEWS`, `ROUND_STARTS`, and `DEADLINES` must have the **same nonzero length**. The review thresholds must be strictly increasing; each round must start before its deadline; and a round's deadline must precede the following round's start. Times must use ISO 8601 with an **explicit UTC offset**, for example `2026-08-25T23:59:59-12:00` for Anywhere on Earth (AoE).

Explicit paths are interpreted relative to the current working directory or searched inside the input directory. With no explicit path, input discovery uses filename patterns in the input directory and fails if multiple candidates match. The input and output directories must be different.

The public Python entry point is `run_pipeline(...)`, which returns a dictionary of paths to the six generated files. The command-line interface prints the resolved inputs and progress through the five stages.

## Module reference

All standalone commands below assume the repository-root layout shown above. Each module can also be imported and called directly from Python.

### 1. `pc_extraction.py` — topic completion

**Inputs:** HotCRP PC roster CSV, typically `*pcinfo*.csv`. It reads all columns whose names begin with `topic:` (case-insensitive).

**Processing:** Preserves the roster and adds `topics?`:

- `True` if every topic preference cell has an explicit value.
- `False` if at least one topic cell is blank or contains `None`.
- `N/A` if the export contains no `topic:` columns at all.

**Output:** `<pc_filename_stem>_topics.csv`, in the chosen output directory. All original fields remain available for the next stage.

**Standalone invocation:**

```bash
python code/pc_extraction.py sample_data/sampleVenue-pcinfo.csv -o output
```

**Python API:** `extract_pc_topics(filename, output_folder=None) -> Path`.

### 2. `bids_extraction.py` — paper preferences

**Inputs:** A PC roster CSV (preferably the output of stage 1) and HotCRP's all-preferences CSV. The latter must contain `email`, `paper`, and `preference`.

**Processing:** Joins records using normalized email addresses and adds a `bids` column containing a JSON list of distinct, sorted paper IDs. An **explicit preference of any value counts as a bid**, including `0` or a negative preference. Blank/`None`/`null` values do not count. HotCRP's `topic_score` and conflict indicators are not considered bids. People not in the supplied PC roster are ignored.

**Output:** `<pc_filename_stem>_bids.csv`; if stage 1 ran first, this becomes `sampleVenue-pcinfo_topics_bids.csv`. Reviewers with no bids receive `[]`.

**Standalone invocation:**

```bash
python code/bids_extraction.py \
    output/sampleVenue-pcinfo_topics.csv \
    sample_data/sampleVenue-allprefs.csv \
    -o output
```

**Python API:** `extract_bids(pc_filename, prefs_filename, output_folder=None) -> Path`.

> Run the topic extractor **before** the bid extractor. Otherwise, the bid output will not already contain the `topics?` field, although `pc_metrics.py` can infer it from original topic columns when those exist.

### 3. `log_extraction.py` — reviewing chronology

**Inputs:** Original PC roster CSV and HotCRP activity-log CSV. The log must contain `date`, `email`, `affected_email`, `paper`, and `action`; it may also include fields such as `roles`. Log timestamps must include their UTC offsets.

**Processing:** Reconstructs chronological events and assignment intervals, including:

- Review assignment, self-assignment, reassignment, and unassignment.
- First and subsequent review submissions, draft/unsubmission/deletion events, and any available HotCRP-reported word lengths.
- Comment events and their earliest logged edit timestamps.
- Paper submissions/withdrawals and explicit account-transfer email aliases.

The roster defines **which PC members are eligible for statistics**, but activity by chairs or non-PC participants remains in the reconstructed chronology. Those interventions can matter when identifying who first initiated a discussion.

**Output:** `<log_filename_stem>_parsed.json`, including paper-level histories, account aliases, statistics, methodology information, and warnings.

**Standalone invocation:**

```bash
python code/log_extraction.py \
    sample_data/sampleVenue-pcinfo.csv \
    sample_data/sampleVenue-log.csv \
    -o output
```

**Python API:** `extract_log_data(pc_filename, log_filename, output_folder=None) -> Path`.

**Important timestamp limitation:** The first logged edit of a comment is used as a proxy for its posting time because this HotCRP export does not separately identify comment creation in every case.

### 4. `reviews_extraction.py` — review/comment contents and matching

**Inputs:** The HotCRP all-reviews/comments **text** export and the parsed JSON produced by `log_extraction.py`. The text export must follow the expected HotCRP section structure (submission, review, and comment headers).

**Processing:** Parses final review-form fields and comment bodies, then matches them to the chronological log. It:

- Counts words in **free-text review fields**; excludes form labels, one-line coded rating/choice responses, and decorative separators.
- Counts words in comment bodies without their headers.
- Links each review and comment to its author, paper, and available timestamps, including account aliases.
- Keeps unmatched/log-only historical records separately rather than silently treating deleted or otherwise absent records as ordinary current activity.
- Retains complete assignment intervals and chair/non-PC comments for later discussion analysis.

**Output:** `<text_filename_stem>_extracted.json`, containing per-paper reviews, comments, assignments, word counts, match statuses, statistics, and warnings.

**Standalone invocation:**

```bash
python code/reviews_extraction.py \
    sample_data/sampleVenue-reviews.txt \
    output/sampleVenue-log_parsed.json \
    -o output
```

**Python API:** `extract_review_details(text_filename, log_json_filename, output_folder=None) -> Path`.

**Important:** Review word counts describe the final exported review text; the log's HotCRP-reported word length at **first submission** is a separate quantity used to evaluate discussion initiation. These values should not be assumed identical.

### 5. `pc_metrics.py` — per-PC statistics and audit

**Inputs:** The topic-and-bid-enriched PC CSV and the enriched reviews JSON from the preceding stages, plus the reviewing schedule and thresholds. When run standalone, it looks in `source_folder` for `*_topics_bids.csv` (or `*_bids.csv`) and `*reviews_extracted.json`, unless the files are passed explicitly.

**Processing:** Produces one row per member in the supplied PC roster, including members with no submitted reviews. It computes:

- Bids and topic-completion status.
- Submitted review IDs, assignment/submission timelines, review counts, word counts, and turnaround times.
- Deadline delays, including configured emergency-assignment exceptions.
- Regular and extra comments, both counts and word lengths.
- Discussion initiation within disjoint per-paper review-count threshold windows.
- Venue-level summary statistics, discussion decisions, and processing warnings.

**Outputs:** Always named `pc_metrics.csv` and `pc_metrics_summary.json`.

**Standalone invocation:**

```bash
python code/pc_metrics.py output \
    --pc-file output/sampleVenue-pcinfo_topics_bids.csv \
    --reviews-json output/sampleVenue-reviews_extracted.json \
    --min-reviews 2 3 \
    --round-starts 2026-07-01T00:00:00-12:00 2026-07-25T00:00:00-12:00 \
    --deadlines 2026-07-20T23:59:59-12:00 2026-08-10T23:59:59-12:00 \
    --emergency-grace-days 2 \
    --min-review-words 50 \
    --min-comment-words 15 \
    -o output
```

All schedule/threshold arguments shown above are **required** when calling `pc_metrics.py` directly. `main.py` supplies them automatically when running the full pipeline.

**Python API:**

```python
csv_path, summary_path = generate_pc_metrics(
    source_folder,
    min_reviews,
    round_starts,
    deadlines,
    emergency_grace_days,
    min_review_words,
    min_comment_words,
    output_folder=None,
    pc_filename=None,
    reviews_json_filename=None,
)
```

#### Review timeliness and emergency assignments

Let `T` be the ordinary deadline and `G` the configured emergency grace period. Assignments made **before** `T - G` use the ordinary deadline. Assignments made **at or after** `T - G`, including assignments during the gap after `T`, are emergency assignments associated with that round until the next round begins.

- **Ordinary assignment:** submitting after `T` is late.
- **Emergency assignment with a following round:** a submission after `T` is excused until the **start** of the following round. Submitting later is late relative to that next-round start.
- **Emergency assignment in the final configured round:** no later cutoff exists in the schedule. A post-deadline submission receives `delay_days = 0` and is explicitly flagged `emergency_open_ended = true`; this means **excused / not assessed against an undefined deadline**, not necessarily promptly completed.
- Assignments after the next round starts are evaluated under the next round's schedule.

`delays` are **signed integer days rounded away from zero** (negative = early, `0` = on time or excused, positive = late). The aligned `time-to-review` values are rounded-up days from assignment to first submission. For precise timestamps, fractional delays, emergency flags, and the applicable deadline, use `review_details` rather than the rounded summary columns. Missing timestamp evidence yields a missing delay, not an automatic on-time classification.

#### Discussion-initiation calculation

`MIN_REVIEWS` is a list of **per-paper review-submission thresholds** (for example `[2, 3]`), not a claim that emergency assignments constitute a separate round. Once a paper reaches the Nth submitted review, its corresponding discussion window opens; a later threshold closes that window and opens the next one. The windows do not overlap.

Initiation requires an assigned reviewer who has **already submitted** a review of at least `MIN_REVIEW_WORDS` according to the first-submission HotCRP length, and then posts a comment of at least `MIN_COMMENT_WORDS`. Earlier chair/unassigned interventions can block attribution. If timestamps or original lengths prevent a reliable conclusion, the script records an unresolved decision instead of guessing.

The CSV's `discussion_initiator` column is a JSON list of credited events, each with a paper ID and round index. One person may receive credit in multiple windows on a single paper. Counting **distinct papers** is a separate downstream calculation.

#### Useful `pc_metrics.csv` columns

| Column(s) | Interpretation |
|---|---|
| `given_name`, `family_name`, `email` | PC member identity from the input roster. |
| `bids`, `topics?` | JSON paper-ID list and topic completion (`True`, `False`, or `N/A`). |
| `reviews`, `assignments` | Chronologically aligned JSON lists of reviewed paper IDs and assignment times. |
| `time-to-review`, `delays` | Aligned JSON lists of rounded day counts; may contain `null`. |
| `review_length`, `hotcrp_length` | Aligned word lengths from final text and HotCRP log, respectively. |
| `review_details` | Per-review JSON records with exact dates, round attribution, and emergency-cutoff flags. |
| `comments`, `comments_length` | JSON mappings from reviewed paper IDs to comment counts / lists of word lengths. |
| `extra_comments`, `extra_comments_length` | Same mappings for papers without a submitted review by that PC member. These are **comments**, not extra reviews. |
| `discussion_initiator` | JSON list of credited (paper, threshold-window) discussion initiations. |
| `reviews_totCount`, `review_length_totalWords`, `review_length_avgWords` | Review volume and final text statistics. |
| `all_comments_totCount`, `all_comments_totalWords` | Combined ordinary and extra PC comment totals. |
| `delays_lateCount`, `delays_earlyCount`, `delays_onTimeOrExcusedCount` | Counts of classified review delays. |

Lists/dictionaries in the CSV are **JSON-encoded strings**. If loading them with pandas, decode the appropriate cells with `json.loads`; do not parse them as ordinary Python expressions. The JSON summary contains `venue_totals`, `discussion_rounds`, `config`, and `warnings` for auditing.

### Optional: `pc_recognitions.py` — independent recognition selection

**This script is deliberately not called by `main.py`.** It consumes `pc_metrics.csv` only after the metrics pipeline has completed. Its rules are separate from the underlying activity measurements.

```bash
python code/pc_recognitions.py output/pc_metrics.csv -o output
```

With an optional, independently verified firefighter list:

```bash
python code/pc_recognitions.py output/pc_metrics.csv \
    --firefighters-csv data/firefighters_verified.csv \
    --top-n 5 \
    -o output
```

Its output files are `pc_recognitions.csv`, `recognition_winners.csv`, and `recognition_summary.json`. It computes COMMENTER, SPEEDY, ELABORATE, COMMITTED, and ALACRITOUS automatically; FIREFIGHTER is evaluated only when a manual list is supplied. `--top-n` affects ranked categories, not the all-qualifying categories.

**Manual CSV format:** `pc_recognitions.py` expects one row per reviewer with an `email` column and optional `paper_ids` (**a JSON array in a CSV cell**) and `notes` fields. This is **not the same format** as the notebook's `email,paper,notes` firefighter input, where one reviewer can have several rows. Convert between formats before passing the notebook/sample data to this script. The upvote/APPRECIATED and overall TOP REVIEWER calculations are separate notebook analyses, not part of this optional script.

**Python API:** `generate_recognitions(metrics_filename, output_folder=None, firefighters_csv=None, top_n=5) -> (Path, Path, Path)`.

## Checks, reproducibility, and troubleshooting

If the corresponding test files are present in `code/`, run their unit tests from the project root:

```bash
python -m unittest discover -s code -p "test_*.py"
```

For an end-to-end smoke test, use the fictional `sampleVenue` exports and example command at the beginning of this README. The sample also includes `sampleVenue-firefighters.csv` and `sampleVenue-upvotes.csv` for separate notebook calculations; the five-stage metrics pipeline does not read them.

Common issues:

| Symptom | What to check |
|---|---|
| Multiple matching input files | Set `--pc`, `--prefs`, `--log`, or `--reviews` explicitly, or clear duplicate downloads. |
| Missing `topics?` / bids | Run topic extraction before bid extraction; pass the combined `*_topics_bids.csv` to metrics. |
| Missing or inconsistent review/comment matches | Check `*_parsed.json`, `*_extracted.json`, and their `warnings`; inspect HotCRP export structure and available log events. |
| Unexpected late review | Inspect `review_details` (`assigned_at`, `first_submitted_at`, `deadline`, `effective_deadline`, `late_assignment`, `emergency_open_ended`), and confirm schedule/grace settings. |
| No discussion initiator | Inspect `pc_metrics_summary.json` -> `discussion_rounds` for missing thresholds, non-reviewer interventions, missing timestamps, or insufficient word counts. |
| Different totals from raw HotCRP exports | Metrics include only eligible PC members and the currently submitted/matched activity included by the parsers; deleted, non-PC, or unresolved records can be excluded from ordinary totals. |


