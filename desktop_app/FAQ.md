# PC Recognition Selector — FAQ and criteria reference

**A practical guide for program chairs using the offline desktop application.**

The **PC Recognition Selector** analyzes HotCRP exports and helps chairs define, preview, and export reviewer recognitions. It runs locally and deterministically. **The eight supplied recognitions are editable starting templates, not HotCRP-defined awards or universal standards.** Chairs decide which recognitions to enable, their thresholds, and whether any documented manual corrections are warranted.

This FAQ describes the behavior of the application in [`desktop_app/`](desktop_app/). For installation and build instructions, see [`desktop_app/README.md`](desktop_app/README.md); for the underlying command-line extractor, see the [repository README](README.md).

## Contents

1. [Getting started](#1-getting-started)
2. [Required HotCRP files](#2-required-hotcrp-files)
3. [Optional supplementary files](#3-optional-supplementary-files)
4. [Reviewing rounds and parsing settings](#4-reviewing-rounds-and-parsing-settings)
5. [The eight recognition categories](#5-the-eight-recognition-categories)
6. [Customizing thresholds and rules](#6-customizing-thresholds-and-rules)
7. [How rankings, ties, and missing information work](#7-how-rankings-ties-and-missing-information-work)
8. [Reviewing results and exporting](#8-reviewing-results-and-exporting)
9. [Privacy, fairness, and reproducibility](#9-privacy-fairness-and-reproducibility)
10. [Troubleshooting](#10-troubleshooting)

## 1. Getting started

### Q: Do I need Python, a notebook, or an internet connection?

**No**, if you download a packaged native application from the repository's **Releases** section. There are separate builds for Windows, macOS (Intel/Apple Silicon), and Linux. Extract the **whole** distribution and launch the application; do not copy only the executable out of its folder. Because the builds are unsigned, an operating-system security confirmation may be required.

If you choose to run **from source**, you need Python 3.10+ and Tkinter. From the repository root:

```bash
python desktop_app/launch.py
```

The application runs without a web service, LLM, telemetry, or network access. You still need access to your HotCRP instance **beforehand** to download its exports.

### Q: What is the workflow?

1. Read the initial instructions for downloading HotCRP exports.
2. Browse to **four required files**; add any available supplementary files and optional email aliases.
3. Enter the reviewing-round schedule and parsing parameters; click **Process locally**.
4. Choose which recognition categories you want to evaluate.
5. Visit each selected category, edit its criteria, and **Preview all reviewers**. Go backward, forward, or jump to another category; the selections persist during this run.
6. Review the final counts, inspect the full awards table, and export the CSV. Optionally export a JSON snapshot of the rule settings.

You may choose fewer than eight awards. The app does **not** require all categories to be used.

## 2. Required HotCRP files

You must obtain these four exports from a HotCRP account with appropriate chair/administrator permissions. The exact menu names and addresses can vary by HotCRP version. File names are **not important** in the desktop app; you browse to each file explicitly.

| Input in app | How to obtain it in HotCRP | Main purpose |
|---|---|---|
| **PC roster / PC Info** (`.csv`) | Users / Program Committee (often `/u/0/users/pc`) -> select members -> **Download > PC info** | PC names, emails, affiliations, roles, and `topic:` preferences |
| **Paper preferences / bids** (`.csv`) | Paper search / all submissions (often `/u/0/q=&t=all`) -> select papers -> **Download > PC review preferences** | Explicit bids, including positive, negative, and zero preferences |
| **Activity log** (`.csv`) | Activity log (often `/u/0/log`) -> **Download** | Assignment, first-submission, and discussion timestamps |
| **Reviews with paper info** (`.txt`) | Paper search / all submissions -> select papers -> **Download > Reviews with paper info** | Review and comment contents and word counts |

**Important input details**

- All four exports should come from the **same HotCRP venue and compatible snapshot**. Mixing old and new exports can produce unmatched reviews or email/roster errors.
- PC Info needs reviewer emails and `topic:` columns. The app uses its original topic columns to count explicitly recorded preferences, not merely a previously calculated `topics?` Boolean.
- A topic counts as **set** if its cell has an explicit nonempty value other than `None`. **Zero is a valid explicit preference**; an empty cell does not count. Thus, the default *all-topics* criterion means a preference recorded for **every topic**, not a positive preference for every topic.
- A paper bid counts when the HotCRP `preference` field is explicitly populated (including `0` or a negative preference). Repeated bids on the same paper are counted once. Blank/`None`/`null` values do not count.
- Accounts with the `chair` role are **excluded by default**. You can change this on the file-selection screen. PC members with zero reviews remain in the overall PC roster and may appear in rankings.

## 3. Optional supplementary files

These inputs are selected individually on the **Choose files** screen. They are **optional**, but awards or criteria that depend on missing information cannot be evaluated until you supply the source or disable the dependent rule. The app **does not infer emergency/extra reviews from assignment timestamps**.

### Extra reviews / FIREFIGHTER assignments

A CSV identifying **specific reviewer–paper pairs** externally certified by the chairs:

```csv
email,paper,notes
reviewer@example.edu,118,Emergency review
reviewer2@example.edu,231,Tiebreaker
```

- Required columns: `email`, `paper`; `notes` is optional.
- `paper` is a **positive paper ID**. A reviewer may appear on multiple rows.
- Every listed reviewer–paper pair must correspond to an actual **submitted review** in the processed data; otherwise, processing stops with an error.
- These pairs define `extra_reviews`; submitted reviews not in the list are counted as `ordinary_reviews`. Without the file, the app treats that distinction as **unknown**, not as zero extra reviews.
- Both **FIREFIGHTER** and the **early-ordinary-review** Distinguished criterion depend on this file.

### Received upvotes

A CSV of each PC member's **total signed upvotes**:

```csv
email,#upvotes
reviewer@example.edu,3
reviewer2@example.edu,-1
reviewer3@example.edu,0
reviewer4@example.edu,
```

- Required columns: `email`, `#upvotes` (with the `#`).
- Scores are integers and **may be negative**. A blank score means zero.
- Include at most **one entry per PC email**. With a complete upvotes file supplied, reviewers omitted from it receive a default value of zero.
- Rows belonging to known excluded chair accounts are ignored for the PC rankings.

### Spotlight-selection voters

A plain-text file containing one email address per line; a `.txt` extension is **not** required:

```text
reviewer@example.edu
reviewer2@example.edu
```

Also supported: a single-column CSV with an `email` header or a literal Python-style list such as `voted = ["reviewer@example.edu"]`. Repeated email addresses do not earn additional credit: the criterion is simply **voted / did not vote**.

### Chair-approved deadline exceptions

One email address per line:

```text
reviewer@example.edu
reviewer2@example.edu
```

Use this **only for a chair-certified extension**, such as a reviewer who proactively warned the chairs of a delay and subsequently met the revised, self-imposed deadline.

- `delay_override` becomes true for the listed PC members.
- Their **award-effective** `on_time` status is true if they submitted at least one review, even if formal delays are recorded.
- Their **original late-review count and timestamps remain intact** for auditing.
- This does **not** grant the separate Distinguished point for submitting ordinary reviews at least 48 hours early.
- A stated intention to finish late, without meeting the revised deadline, should **not** be entered here.

### Manually confirmed discussion initiation

A CSV recording the **minimum confirmed number of distinct papers** for which a reviewer initiated discussion:

```csv
email,minimum_distinct_papers
reviewer@example.edu,1
reviewer2@example.edu,2
```

- The minimum must be an integer **at least 1**.
- The final count is `max(automatically_detected_count, chair_confirmed_minimum)`: corrections **never add a second copy** of an already detected initiation.
- One confirmed initiation may satisfy the Distinguished initiative criterion; ALACRITOUS requires **at least two distinct papers** by default.

### Corrected upvote totals

A second CSV, useful when the original HotCRP upvotes extraction contains a known error:

```csv
email,#upvotes
reviewer@example.edu,3
```

This **replaces**, rather than adds to, that reviewer's original total. Supply the **main upvotes CSV as well**: a correction-only file is not considered a complete upvote dataset.

### Email aliases

If a supplementary file uses an address other than the one in PC Info, enter a mapping in the **Email aliases** field:

```text
alternate@example.edu,canonical@example.edu
```

Separate several mappings with **semicolons** in the text field. The right-hand address must exist in the original PC roster. Emails are matched case-insensitively, with surrounding spaces removed. Unknown addresses are rejected rather than silently assigned to another PC member.

## 4. Reviewing rounds and parsing settings

The app runs the original five-stage analysis before it computes awards. These inputs are **venue-specific** and are configured on the **Reviewing rounds & parsing settings** screen.

| Input | Default | Meaning / constraint |
|---|---|---|
| **Round start** | `2026-07-31T00:00:00-12:00` | ISO 8601 date/time **with timezone offset**; must precede the deadline |
| **Round deadline** | `2026-08-25T23:59:59-12:00` | ISO 8601 with offset; must be before the next round's start |
| **Minimum reviews for discussion** | `2` | Positive integer threshold that starts a discussion-initiation window; thresholds must be **strictly increasing** across configured rounds |
| **Grace window (days)** | `2` | Finite, nonnegative number affecting *timeliness of late-assigned reviews*; **not an extra-review classifier** |
| **Minimum review words** | `50` | Nonnegative integer; a qualifying reviewer must have submitted a sufficiently substantive review to receive autonomous discussion-initiation credit |
| **Minimum comment words** | `15` | Nonnegative integer; a qualifying initiation must have sufficient comment length |

**AoE example:** `2026-08-25T23:59:59-12:00` means August 25 at 23:59:59 **Anywhere on Earth**, i.e., UTC-12. `2026-08-25` by itself is **invalid**. Your real venue dates should replace the illustrative defaults.

**Multiple rounds:** Click **+ Add reviewing round** and enter a separate threshold, start, and deadline for each. The three lists must have equal nonzero lengths. Round starts must increase and rounds may not overlap. For example:

| Round | Discussion threshold | Start (AoE) | Deadline (AoE) |
|---|---:|---|---|
| 1 | 2 | `2026-07-31T00:00:00-12:00` | `2026-08-25T23:59:59-12:00` |
| 2 | 3 | `2026-08-26T00:00:00-12:00` | `2026-09-07T23:59:59-12:00` |

**Important modeling limitation:** The current pipeline uses the configured round thresholds for **separate discussion-initiation windows**, not just time-based deadlines. Adding a second round can change who receives discussion-initiation credit **independently of whether someone was late**. Compare results before and after any schedule change.

**How late assignments are treated:** A review assigned at or after `(its round deadline - grace days)` follows the pipeline's late-assignment exception logic. If another reviewing round exists, its **start** supplies the next cutoff for eligible post-deadline submissions. If there is no next round, eligible late-assigned final-round reviews have **no additional hard cutoff** specified by the schedule, so post-deadline submissions are treated as excused. Use chair-certified exception inputs when you need a documented human decision. None of these rules labels a review as *extra*; only the optional extra-review CSV does that.

## 5. The eight recognition categories

These are the **out-of-the-box defaults**. They can all be changed in the app. The default **top 7** reflects the earlier 5%-of-142 AISec PC example; it is a **fixed seven-person nominal cutoff**, not automatically recalculated as 5% for another venue. You can instead use **top percent** for size-independent selection.

| Recognition | What it rewards | Default rule | Supplementary input required? |
|---|---|---|---|
| **SPEEDY** | Quick submission of initial reviews | At least 2 submitted reviews; no unexcused lateness; **top 7 shortest elapsed hours to the second submitted review** (relative to the relevant round start) | No |
| **COMMENTER** | Active and substantive participation in discussion | **Top 7 by total comment words OR top 7 by number of comments**; union of the two lists, including boundary ties | No |
| **ELABORATE** | Reviewing thoroughness | At least 1 review, no unexcused lateness; **top 7 highest cumulative review wordcount** | No |
| **COMMITTED** | Sustained engagement with PC responsibilities | At least **10 bids**, **explicit preferences for ALL exported topics**, at least **2 submitted reviews**, and no unexcused lateness | No |
| **ALACRITOUS** | Initiative in starting paper discussions | Initiated qualifying discussions on at least **2 distinct papers** | Only if applying a manual correction |
| **FIREFIGHTER** | Willingness to take on verified emergency/tiebreaker load | At least **2 ordinary** submitted reviews **plus 1 extra** submitted review | **Yes:** extra-review CSV |
| **APPRECIATED** | Positive feedback from peer reviewers | At least 1 review; **top 7 highest total signed upvotes**, including ties | **Yes:** upvotes CSV |
| **DISTINGUISHED** | Broad excellence across multiple PC contributions | Satisfy **at least 8 of 12** equal-weight criteria listed below | **Yes:** upvotes, extra-review, and spotlight-voter files, unless dependent criteria are disabled |

### What are the 12 default Distinguished criteria?

Each satisfied criterion awards **exactly one point**. There is no mandatory criterion beyond the **minimum score**, unless you add one in the editor. The initial score threshold is **8/12**.

| # | Criterion | Default condition |
|---:|---|---|
| 1 | Bidding | At least **10** explicit paper bids |
| 2 | Topic preferences | Explicit preference for **every topic** in the PC Info export (for example, 6/6 for AISec '26, 3/3 in the synthetic sample) |
| 3 | Timeliness | **No unexcused late reviews**; chair-certified extensions can satisfy this |
| 4 | Discussion participation | **Top 30%** of PC by **number** of comments posted; positive values required |
| 5 | Reviewing contribution | At least **3** submitted reviews |
| 6 | Review elaborateness | **Top 30%** by **average** review length in words; positive values required |
| 7 | Peer appreciation | **Top 30%** by total signed upvotes, among reviewers with at least one submitted review |
| 8 | Discussion initiative | Initiated a qualifying discussion for at least **1** distinct paper |
| 9 | Proactive reviewing | **Every ordinary review** was submitted at least **48 hours before its round deadline**, with at least one ordinary review present |
| 10 | Extra reviewing | Completed at least **1** chair-identified emergency/tiebreaker review |
| 11 | Spotlight participation | Cast a vote in the spotlight-paper selection |
| 12 | Discussion sophistication | **Top 30%** by **cumulative comment wordcount**; positive values required |

The sequence in the **editor** may differ from the ordering used in particular workshop documents, but the above twelve rules are those actually implemented as defaults. Review the saved rule settings before publicly citing specific criterion numbers.

## 6. Customizing thresholds and rules

### Q: What can I change?

For each selected recognition, the editor allows you to:

- Change a rule's **metric**, comparison operator, and numeric threshold.
- Uncheck **Use** to disable a criterion without deleting it, or click **Remove** to delete it.
- Click **+ Add condition** to add another criterion (a default *posted comments >=1* rule appears and is editable).
- Set **Ranking population** to `all` PC members or `reviewed` (only those with at least one submitted review).
- Enable **Require a positive metric value** for a ranking rule to prevent a zero or negative value from qualifying merely because of rank.
- For ranked awards, combine multiple ranking rules with **any** (OR) or **all** (AND), and choose whether to **include boundary ties**.
- For Distinguished, change **Required points** and add/remove/disable scored criteria. All enabled scored criteria have equal weight.

The app's comparisons include **at least (`>=`)**, **at most (`<=`)**, **equals (`=`)**, **is true**, **is false**, **top N highest/lowest**, and **top percent highest/lowest**.

**Combining rule types:**

- **Mandatory conditions** (filters) are combined with **AND**.
- **Ranked awards** require all mandatory filters and then evaluate their ranking conditions as **any** or **all**, as configured.
- **Distinguished** grants one point for every enabled satisfied scored rule; optionally added mandatory conditions must also pass.
- A plain criteria-based award such as COMMITTED or FIREFIGHTER needs **every enabled mandatory rule** satisfied.

### Examples

**COMMITTED, with more discussion and no bidding requirement:** Uncheck **Use** for `Paper bids >= 10`, then add `Posted comments >= 5` as a mandatory condition. The other enabled conditions still apply.

**Require at least 5 of 6 topics instead of all 6:** Disable `Preferences for ALL topics is true` and add `Topic preferences explicitly set >= 5`. This is venue-specific. The default remains **all topics**; changing it changes the meaning of the recognition.

**Use a percentage instead of a hard-coded top 7:** In SPEEDY, change `top N (lowest), Value: 7` to `top percent (lowest), Value: 5`. The rank then adjusts to your venue's reviewer count.

**Make Distinguished more selective:** Change **Required points** from `8` to `9` without changing other rules. The allowed range is from **1** to the number of enabled scored criteria.

### Q: Which numeric inputs are valid?

- **Top N:** a positive whole number (1, 2, 3, ...).
- **Top percent:** greater than 0 and at most 100 (for example, 5 or 30).
- **Reviewing-round thresholds:** positive whole numbers, strictly increasing from one round to the next.
- **Grace days:** any finite nonnegative number, including fractional days.
- **Minimum review/comment words:** nonnegative whole numbers.
- **Distinguished required points:** a whole number between 1 and the number of enabled scoring rules.
- **Boolean metrics:** use **is true** or **is false**, not numeric comparisons.
- **Numeric metrics:** use numeric comparisons or ranking operators, not `is true` / `is false`.

A ranked award must have **at least one enabled ranking criterion**. A criteria-based award must have **at least one enabled mandatory criterion**. Changing a metric to one needing missing optional data will block the preview until that data is supplied or the rule is disabled.

## 7. How rankings, ties, and missing information work

### Q: What does "top N" mean?

The application sorts the eligible population by that metric and chooses the **Nth** score as a cutoff. If **Include ties at cutoff** is selected (the default), everyone equal to the boundary score qualifies, so the final count **can exceed N**. For instance, a top-seven APPRECIATED selection can include eight people if the seventh and eighth scores are equal.

### Q: What does "top 30%" mean?

It ranks the members in the configured population, takes `ceil(0.30 * eligible_count)` as the nominal number, and uses that position's metric as the cutoff. Ties at the boundary are included in the default configuration. It does **not** mean "value greater than 30% of the maximum score." Some metrics also require a **positive** value to qualify.

For **ranked** awards such as SPEEDY, the ranking is based on members who first pass its mandatory filters. For **Distinguished** percentile scoring, ranks are computed across the applicable PC population, **not** just members who already scored enough other points.

### Q: Is an unsubmitted review treated as a late review?

No. Timeliness is based on submitted review records and available event timestamps. Review **count** is evaluated separately, so the default COMMITTED rule still requires at least two submitted reviews. Missing or ambiguous timing information can produce **POSSIBLE** rather than a confirmed selection; the app warns about such cases before final export.

### Q: What if optional data were not supplied?

The application **does not invent missing firefighter assignments, upvotes, or spotlight votes**. Categories with missing required sources start unchecked. If you later select one, its evaluation will explain which source is missing. You may supply the file or **disable/remove the dependent rule**. Once the complete upvotes file is supplied, a reviewer with no listed vote total is treated as having **zero** upvotes.

### Q: Can manual corrections override calculations?

Only through the **explicit supplementary inputs** described above. They are separated conceptually from HotCRP-derived metrics: upvote corrections replace specific totals; discussion corrections define a verified minimum; deadline exceptions affect **award-effective** timeliness; and firefighter assignments define which **specific** submitted reviews are extra. Nothing is silently inferred from these corrections.

## 8. Reviewing results and exporting

### Q: Can I inspect someone who did *not* receive an award?

Yes. Each recognition has **Preview all reviewers**, which displays everyone in the PC roster, along with relevant metrics, each configured condition, and a status:

- **SELECTED:** meets the award definition;
- **NOT SELECTED:** does not meet it;
- **POSSIBLE:** insufficient or ambiguous information prevents a definitive conclusion.

Green table rows highlight selected members. The sidebar shows which chosen categories have been previewed (`CHECKED`) and those still needing attention (`NEXT`). Every selected category must be previewed before finalizing.

### Q: What statistics are shown?

The summary shows **total awards/badges**, **unique PC members with at least one recognition**, **total PC members evaluated**, **percentage recognized**, and a per-category count chart.

**Total badges and unique recipients are different:** a PC member can receive several awards, but counts as **one** distinct recognized member.

### Q: What is exported?

The main **CSV** includes **all evaluated PC members** (even those with zero awards), sorted by family name, given name, then email. It includes:

```text
given_name, family_name, name, email, affiliation,
[one 0/1 column per selected award], recognitions, award_count
```

The `recognitions` field is a pipe-separated list; `award_count` is the number of selected awards for that person. The CSV uses UTF-8 with BOM for spreadsheet compatibility and protects fields that might otherwise be interpreted as spreadsheet formulas.

The optional **Export rule settings** button saves a **JSON methodology snapshot**: round settings, parsing thresholds, selected awards, and their exact rules. It deliberately omits private source-file paths, reviewer emails, and review contents. This JSON is for **auditing/documentation**, not a currently supported "import and restore" function.

## 9. Privacy, fairness, and reproducibility

- **Local processing:** input files are read from your computer; the app does not upload them or access external services. No LLM is used.
- **Temporary intermediates:** the extraction pipeline's intermediate files reside in a temporary working folder. They are normally deleted when the app exits or you restart an analysis. The final CSV/JSON go only to the paths you choose.
- **Confidential data:** HotCRP exports can contain reviewer identities, confidential reviews, assignments, and discussions. Treat them accordingly. Do not publish raw inputs or private analysis output in the public repository.
- **Transparent discretion:** chair-approved corrections should be applied consistently and backed by verifiable records. Keep a private explanation of who was exempted and why, especially if awards are publicly announced.
- **Same inputs and rules, same results:** evaluation is deterministic. Differences in exports, round schedules, enabled conditions, chair supplements, or cutoff/tie policies **can legitimately change the result**.
- **Not an endorsement of one award policy:** the bundled default criteria reflect an AISec-style example. Conferences and workshops should publish the precise rules **actually used** when presenting awards.

## 10. Troubleshooting

| Symptom | Likely reason / what to do |
|---|---|
| The app cannot find a file | Select an existing local file in every required input field; the app does not download HotCRP data for you. |
| `No topic: columns` | Export **PC Info** from HotCRP, not a manually reduced contact list. Topic-related rules need those columns. |
| Unknown PC email in an optional file | Correct the spelling or enter `alias,canonical-email` in **Email aliases**. Unknown emails are deliberately not ignored. |
| Extra-review email/paper pair not present | Recheck the paper ID, reviewer email, and whether the review was submitted and included in the exports. |
| Upvote format error | Use exactly `email,#upvotes`; scores must be signed integers; do not duplicate reviewer rows. |
| APPRECIATED or DISTINGUISHED will not preview | Supply the missing optional source(s), or turn off the specific dependent criteria. |
| A chair appears in my rankings | Check the **Exclude accounts with the HotCRP 'chair' role** setting and the `roles` field in the PC Info export. |
| The award count exceeds a configured top N | Boundary ties are included by default. Disable **Include ties at cutoff** for a hard top-N limit. |
| The two-round configuration changed discussion initiations | Discussion thresholds define nonoverlapping initiation windows. Verify those thresholds rather than assuming dates affect only lateness. |
| A reviewer says they completed all topics but did not qualify | An empty or `None` topic cell is *not* explicit. Inspect the original six (or venue-specific number of) `topic:` columns. |
| Someone with a valid chair extension still appears late | Supply their canonical email in **Chair-certified deadline exemptions** (with an alias if necessary), then reprocess. Original raw delays remain visible in the audit metrics. |
| Export is unavailable | Preview **every** selected recognition and resolve any missing-source or rule-validation errors; then use **Summary > Export recognition CSV**. |

### Where can I report a problem?

Open an issue in the repository with the operating system, application version/release, the relevant error message, and **synthetic or redacted** example files. **Never attach private HotCRP logs, reviews, or PC rosters to a public issue.**

---

**Code reference:** Defaults and rule semantics are implemented in [`desktop_app/engine.py`](desktop_app/engine.py); the interface and input validation are in [`desktop_app/app.py`](desktop_app/app.py); the underlying extraction and reviewing-round logic are in [`code/main.py`](code/main.py) and [`code/pc_metrics.py`](code/pc_metrics.py). These files are the authoritative definition of application behavior.
