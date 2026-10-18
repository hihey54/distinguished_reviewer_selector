"""Tk desktop wizard: local HotCRP extraction, configurable awards, CSV export.

No internet, analytics, database, nonstandard runtime packages or LLMs.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
import queue
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from .engine import (
    AwardConfig, METRICS, OP_LABELS, BOOL_OPS, RANKING_OPS, ProgramData,
    ReviewResult, Rule, award_report, default_awards, evaluate_award,
    export_csv, load_program,
)


FILE_FIELDS = [
    ("pc", "PC roster / PC Info (.csv)", True),
    ("prefs", "Paper preferences / bids (.csv)", True),
    ("log", "HotCRP activity log (.csv)", True),
    ("reviews", "Reviews with paper info (.txt)", True),
    ("firefighters", "Extra review assignments (email,paper CSV)", False),
    ("upvotes", "Upvotes (email,#upvotes CSV)", False),
    ("spotlight", "Spotlight voters (one email per line)", False),
    ("delay_overrides", "Chair-certified deadline exemptions (emails)", False),
    ("discussion_corrections", "Chair-confirmed initiations (email,minimum_distinct_papers)", False),
    ("upvote_corrections", "Corrected vote totals (email,#upvotes)", False),
]

RULE_METRICS = {f"{v.label}  [{k}]": k for k, v in METRICS.items()}
METRIC_TO_LABEL = {key: label for label, key in RULE_METRICS.items()}
OP_TO_LABEL = {key: value for key, value in OP_LABELS.items()}
LABEL_TO_OP = {value: key for key, value in OP_TO_LABEL.items()}


class ScrollArea(ttk.Frame):
    def __init__(self, parent, *, height=370):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0, height=height,
                                background="#ffffff")
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.body = ttk.Frame(self.canvas)
        self.item = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bar.pack(side="right", fill="y")
        self.body.bind("<Configure>", self._bound)
        self.canvas.bind("<Configure>", self._resize)

    def _bound(self, event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _resize(self, event):
        self.canvas.itemconfigure(self.item, width=event.width)


class DesktopApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PC Recognition Selector | Offline HotCRP Analyzer")
        self.geometry("1160x830")
        self.minsize(910, 660)
        self.configure(bg="#f2f5f9")
        self._theme()
        self.protocol("WM_DELETE_WINDOW", self._close)

        self.file_paths = {key: tk.StringVar(value="") for key, _label, _required in FILE_FIELDS}
        self.exclude_chairs = tk.BooleanVar(value=True)
        self.email_aliases = tk.StringVar(value="")
        self.grace_days = tk.StringVar(value="2")
        self.min_review_words = tk.StringVar(value="50")
        self.min_comment_words = tk.StringVar(value="15")
        self.rounds = [{"threshold": "2", "start": "2026-07-31T00:00:00-12:00",
                        "deadline": "2026-08-25T23:59:59-12:00"}]
        self.round_widgets = []
        self.configs = {c.key: c for c in default_awards()}
        self.selected = {key: tk.BooleanVar(value=True) for key in self.configs}
        self.reviewed: set[str] = set()
        self.preview: dict[str, ReviewResult] = {}
        self.program: ProgramData | None = None
        self.temp: tempfile.TemporaryDirectory | None = None
        self.current_category = ""
        self.rule_widgets: dict[str, list[tuple]] = {}
        self.busy = False
        self.events = queue.Queue()
        self.step = "welcome"
        self._header()
        self.page = ttk.Frame(self, style="Surface.TFrame", padding=22)
        self.page.pack(fill="both", expand=True, padx=22, pady=(0, 14))
        self.status = ttk.Label(self, text="Fully offline | Nothing is uploaded or transmitted",
                                style="Foot.TLabel")
        self.status.pack(side="bottom", anchor="w", padx=28, pady=(0, 10))
        self.show_welcome()

    def _theme(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Surface.TFrame", background="#ffffff")
        style.configure("TFrame", background="#ffffff")
        style.configure("TLabel", background="#ffffff", foreground="#192a43", font=("Arial", 10))
        style.configure("Title.TLabel", background="#ffffff", foreground="#152840", font=("Arial", 19, "bold"))
        style.configure("Sub.TLabel", background="#ffffff", foreground="#65748a", font=("Arial", 10))
        style.configure("Group.TLabel", background="#ffffff", foreground="#173552", font=("Arial", 11, "bold"))
        style.configure("Hero.TLabel", background="#f2f5f9", foreground="#18365c", font=("Arial", 17, "bold"))
        style.configure("HeroSub.TLabel", background="#f2f5f9", foreground="#5d6c7d", font=("Arial", 10))
        style.configure("Foot.TLabel", background="#f2f5f9", foreground="#68778a", font=("Arial", 9))
        style.configure("TButton", font=("Arial", 10), padding=(12, 7))
        style.configure("Primary.TButton", foreground="#ffffff", background="#2563b5", padding=(17, 9))
        style.map("Primary.TButton", background=[("active", "#1d518f"), ("disabled", "#aac3df")])
        style.configure("TCheckbutton", background="#ffffff", foreground="#1b3655", font=("Arial", 10))
        style.configure("TEntry", padding=5)
        style.configure("Treeview", rowheight=25, font=("Arial", 9))
        style.configure("Treeview.Heading", font=("Arial", 9, "bold"))

    def _header(self):
        top = ttk.Frame(self, style="Surface.TFrame", padding=(26, 16))
        top.pack(fill="x", padx=22, pady=(16, 10))
        ttk.Label(top, text="PC Recognition Selector", style="Title.TLabel").pack(side="left")
        ttk.Label(top, text="LOCAL DESKTOP APPLICATION", style="Sub.TLabel").pack(side="right")

    def _clear(self):
        for child in self.page.winfo_children():
            child.destroy()

    def _h1(self, title: str, subtitle=""):
        ttk.Label(self.page, text=title, style="Title.TLabel").pack(anchor="w", pady=(1, 4))
        if subtitle:
            ttk.Label(self.page, text=subtitle, style="Sub.TLabel", wraplength=1000).pack(anchor="w", pady=(0, 14))

    def _buttons(self, *, previous: Callable | None = None, next_text="Continue",
                 next_command: Callable | None = None, extra: list[tuple[str, Callable]] | None = None):
        panel = ttk.Frame(self.page)
        panel.pack(side="bottom", fill="x", pady=(13, 0))
        if previous:
            ttk.Button(panel, text="< Back", command=previous).pack(side="left")
        for label, command in (extra or []):
            ttk.Button(panel, text=label, command=command).pack(side="left", padx=7)
        if next_command:
            ttk.Button(panel, text=next_text, command=next_command,
                       style="Primary.TButton").pack(side="right")

    def _text(self, parent, text, *, maxwidth=900, color_style=None, pady=6):
        lab = ttk.Label(parent, text=text, wraplength=maxwidth,
                        style=color_style or "TLabel", justify="left")
        lab.pack(anchor="w", pady=pady)
        return lab

    def show_welcome(self):
        self.step = "welcome"
        self._clear()
        self._h1("Create transparent PC recognitions",
                 "A guided, deterministic, fully local workflow: collect HotCRP exports, configure reviewing rounds, select awards, inspect results, and export CSV.")
        sc = ScrollArea(self.page, height=520)
        sc.pack(fill="both", expand=True)
        body = sc.body
        guide = [
            ("1  |  PC roster / PC Info CSV",
             "In your HotCRP venue, go to Users > Program committee (typically /u/0/users/pc), select PC members, then Download > PC info. Topic preferences and affiliations come from this file."),
            ("2  |  Review preferences / paper bids CSV",
             "In the paper search page (typically /u/0/q=&t=all), select the submissions, then Download > PC review preferences. Include all paper preferences; this is often named *allprefs*.csv."),
            ("3  |  HotCRP activity log CSV",
             "Open the HotCRP activity log (typically /u/0/log) and click Download. The timestamps in this export are used for review timeliness and discussion analysis."),
            ("4  |  Reviews with paper info TXT",
             "On the all-submissions search page, select all papers and download Reviews with paper info. This export supplies review text and comments."),
            ("5  |  Supplementary chair data (optional)",
             "You can provide CSVs for explicitly identified emergency/tiebreaker reviews, signed upvotes, corrected upvotes, chair-confirmed discussion initiations, and email lists for spotlight votes or approved deadline extensions. The app never guesses an extra review from its assignment date."),
        ]
        for head, text in guide:
            ttk.Label(body, text=head, style="Group.TLabel").pack(anchor="w", pady=(13, 3))
            self._text(body, text, pady=3)
        ttk.Separator(body).pack(fill="x", pady=12)
        self._text(body, "Privacy: downloaded HotCRP files may contain confidential peer reviews, reviewer identities, and submission content. Processing happens entirely on this computer, in a temporary folder automatically cleaned up on exit. Share exported awards CSVs only as appropriate.")
        self._text(body, "This application does not log in to HotCRP or download your exports automatically. A HotCRP chair/admin must obtain those files manually using the venue's interface.")
        self._buttons(next_text="Choose input files  >", next_command=self.show_files)

    def _browse(self, key: str):
        fmt = [("CSV files", "*.csv"), ("Text files", "*.txt"), ("All files", "*")]
        if key in ("spotlight", "delay_overrides", "reviews"):
            fmt = [("All files", "*"), ("Text files", "*.txt"), ("CSV files", "*.csv")]
        result = filedialog.askopenfilename(parent=self, title="Select " + key, filetypes=fmt)
        if result:
            self.file_paths[key].set(result)

    def show_files(self):
        self.step = "files"
        self._clear()
        self._h1("1 / 4  |  Choose files", "Four exports are required; supplementary chair-provided files are optional.")
        sc = ScrollArea(self.page, height=530)
        sc.pack(fill="both", expand=True)
        for i, (key, label, required) in enumerate(FILE_FIELDS):
            if i in (0, 4):
                heading = "HotCRP exports (required)" if i == 0 else "Optional supplementary data"
                ttk.Label(sc.body, text=heading, style="Group.TLabel").pack(anchor="w", pady=(13, 5))
            row = ttk.Frame(sc.body)
            row.pack(fill="x", pady=5)
            ttk.Label(row, text=label, width=53, anchor="w").pack(side="left")
            ttk.Entry(row, textvariable=self.file_paths[key], width=65).pack(side="left", expand=True, fill="x", padx=(4, 8))
            ttk.Button(row, text="Browse", command=lambda x=key: self._browse(x)).pack(side="right")
        ttk.Separator(sc.body).pack(fill="x", pady=(16, 6))
        ttk.Checkbutton(sc.body, text="Exclude accounts with the HotCRP 'chair' role from award rankings (recommended)",
                        variable=self.exclude_chairs).pack(anchor="w", pady=8)
        ttk.Label(sc.body, text="Email aliases (optional): one alias,canonical-email pair per line", style="Group.TLabel").pack(anchor="w", pady=(7, 4))
        ttk.Entry(sc.body, textvariable=self.email_aliases, width=85).pack(fill="x", pady=(0, 4))
        self._text(sc.body, "If an exception file uses a different email address from the PC roster, type its mapping here: alt@example.org,person@example.org. Multiple pairs can be separated with semicolons.", pady=2)
        self._buttons(previous=self.show_welcome, next_text="Configure reviewing rounds  >", next_command=self._files_next)

    def _files_next(self):
        missing = [label for key, label, required in FILE_FIELDS if required and not self.file_paths[key].get().strip()]
        if missing:
            messagebox.showerror("Missing HotCRP export(s)", "Select all required files:\n" + "\n".join(missing))
            return
        for key, label, required in FILE_FIELDS:
            path = self.file_paths[key].get().strip()
            if path and not Path(path).expanduser().is_file():
                messagebox.showerror("File not found", f"{label}\n{path}")
                return
        self.show_rounds()

    def _persist_rounds(self):
        if not self.round_widgets:
            return
        self.rounds = [{name: entry.get().strip() for name, entry in each.items()}
                       for each in self.round_widgets]

    def _add_round(self):
        self._persist_rounds()
        n = len(self.rounds)
        self.rounds.append({"threshold": str(n + 2), "start": "2026-08-26T00:00:00-12:00",
                            "deadline": "2026-09-07T23:59:59-12:00"})
        self.show_rounds()

    def _remove_round(self):
        self._persist_rounds()
        if len(self.rounds) > 1:
            self.rounds.pop()
            self.show_rounds()

    def show_rounds(self):
        self.step = "rounds"
        self._clear()
        self._h1("2 / 4  |  Reviewing rounds & parsing settings",
                 "Replace the example dates with your venue's actual dates. Every timestamp MUST contain a timezone offset (e.g., -12:00 for AoE).")
        ttk.Label(self.page, text="Round schedule", style="Group.TLabel").pack(anchor="w", pady=6)
        round_frame = ttk.Frame(self.page)
        round_frame.pack(fill="x", pady=6)
        titles = ["Round", "Minimum reviews for discussion", "Start (ISO 8601 + timezone)", "Deadline (ISO 8601 + timezone)"]
        for col, name in enumerate(titles):
            ttk.Label(round_frame, text=name, style="Sub.TLabel").grid(row=0, column=col, sticky="w", padx=6)
        self.round_widgets = []
        for i, saved in enumerate(self.rounds):
            ttk.Label(round_frame, text=f"Round {i+1}").grid(row=i+1, column=0, sticky="w", padx=6, pady=7)
            vars = {}
            for j, field_name in enumerate(("threshold", "start", "deadline"), start=1):
                ent = ttk.Entry(round_frame, width=(10 if j == 1 else 34))
                ent.insert(0, saved[field_name])
                ent.grid(row=i+1, column=j, sticky="ew", padx=6, pady=7)
                vars[field_name] = ent
            self.round_widgets.append(vars)
        ttk.Button(self.page, text="+ Add reviewing round", command=self._add_round).pack(anchor="w", pady=(8, 0))
        ttk.Button(self.page, text="Remove last round", command=self._remove_round).pack(anchor="w", pady=5)
        ttk.Separator(self.page).pack(fill="x", pady=14)
        other = ttk.Frame(self.page)
        other.pack(anchor="w", pady=8)
        for j, (name, var, note) in enumerate([
            ("Grace window (days)", self.grace_days, "Assignment grace; not a firefighter classifier"),
            ("Minimum review words", self.min_review_words, "Discussion initiator eligibility"),
            ("Minimum comment words", self.min_comment_words, "Discussion initiator eligibility"),
        ]):
            ttk.Label(other, text=name, width=27).grid(row=j, column=0, sticky="w", pady=9)
            ttk.Entry(other, width=12, textvariable=var).grid(row=j, column=1, padx=8, sticky="w")
            ttk.Label(other, text=note, style="Sub.TLabel").grid(row=j, column=2, padx=10)
        self._text(self.page, "The review threshold list also defines the pipeline's distinct discussion-initiation windows. Changing the number of rounds may change discussion credits, independently of timeliness.", pady=15)
        self.progress_label = ttk.Label(self.page, text="", style="Sub.TLabel")
        self.progress_label.pack(anchor="w")
        self._buttons(previous=self._back_to_files, next_text="Process locally  >", next_command=self._start_processing)

    def _back_to_files(self):
        self._persist_rounds()
        self.show_files()

    def _validate_round_settings(self):
        self._persist_rounds()
        thresholds = [int(r["threshold"]) for r in self.rounds]
        starts = [r["start"] for r in self.rounds]
        ends = [r["deadline"] for r in self.rounds]
        grace = float(self.grace_days.get())
        min_review = int(self.min_review_words.get())
        min_comment = int(self.min_comment_words.get())
        if min_review < 0 or min_comment < 0:
            raise ValueError("Review/comment minimum wordcounts must be nonnegative")
        from .pipeline import verify_schedule
        verify_schedule(thresholds, starts, ends, grace)
        return thresholds, starts, ends, grace, min_review, min_comment

    def _start_processing(self):
        if self.busy:
            return
        try:
            args = self._validate_round_settings()
            from .engine import approved_aliases
            # Accept one pair or multiple semicolon-separated pairs in a single field.
            alias_text = self.email_aliases.get().replace(";", "\n")
            approved_aliases(alias_text)
        except (ValueError, TypeError) as exc:
            messagebox.showerror("Invalid configuration", str(exc))
            return
        self.busy = True
        self.progress_label.configure(text="Running the five-stage HotCRP pipeline... please wait.")
        self.status.configure(text="Processing offline - your files remain on this computer")
        if self.temp:
            self.temp.cleanup()
        self.temp = tempfile.TemporaryDirectory(prefix="hotcrp_pc_awards_")
        input_paths = {name: Path(var.get()).expanduser().resolve() if var.get().strip() else None
                       for name, var in self.file_paths.items()}
        exclude_chairs_setting = bool(self.exclude_chairs.get())
        temporary_output = Path(self.temp.name) / "output"
        tmp_input = Path(self.temp.name) / "input"
        tmp_input.mkdir(exist_ok=True)
        def work():
            try:
                from .pipeline import run_hotcrp
                outputs = run_hotcrp(
                    input_dir=tmp_input, output_dir=temporary_output,
                    pc=input_paths["pc"], prefs=input_paths["prefs"], log=input_paths["log"],
                    reviews=input_paths["reviews"], thresholds=args[0], starts=args[1], deadlines=args[2],
                    grace=args[3], min_review_words=args[4], min_comment_words=args[5],
                )
                opts = {key: input_paths[key] for key in (
                    "firefighters", "upvotes", "spotlight", "delay_overrides",
                    "discussion_corrections", "upvote_corrections")}
                data = load_program(outputs["pc_metrics"], input_paths["pc"],
                                    alias_text=alias_text, exclude_chairs=exclude_chairs_setting, **opts)
                self.events.put(("ok", data))
            except Exception as exc:
                self.events.put(("error", f"{type(exc).__name__}: {exc}"))
        threading.Thread(target=work, daemon=True).start()
        self.after(110, self._poll)

    def _poll(self):
        try:
            event, data = self.events.get_nowait()
        except queue.Empty:
            self.after(110, self._poll)
            return
        self.busy = False
        if event == "error":
            self.progress_label.configure(text="Processing did not complete.")
            messagebox.showerror("Pipeline failed", data)
            self.status.configure(text="Pipeline error - please check the input file types and configuration")
            return
        first_analysis = self.program is None
        self.program = data
        if first_analysis:
            # Supplementary datasets are optional; do not preselect awards
            # that cannot be evaluated without currently missing inputs.
            for key, cfg in self.configs.items():
                needed = {data.need(rule.metric)
                          for rule in cfg.filters + cfg.rankings + cfg.points if rule.enabled}
                self.selected[key].set(needed <= {None})
        self.reviewed.clear()
        self.preview.clear()
        self.status.configure(text=f"Loaded {len(data.reviewers)} reviewers and {data.raw_review_count} submitted reviews | Fully offline")
        self.show_choose()

    def show_choose(self):
        self.step = "choose"
        self._clear()
        self._h1("3 / 4  |  Select recognitions", "Choose the distinctions you want to evaluate. Categories needing absent optional data start unchecked. Every rule remains editable.")
        if self.program is None:
            self.show_rounds()
            return
        self._text(self.page, f"Loaded {len(self.program.reviewers)} eligible PC members. {len(self.program.excluded)} chair-role accounts excluded.")
        if self.program.warnings:
            for warn in self.program.warnings:
                self._text(self.page, "Audit: " + warn, color_style="Sub.TLabel", pady=2)
        area = ScrollArea(self.page, height=470)
        area.pack(fill="both", expand=True)
        for c in self.configs.values():
            row = ttk.Frame(area.body)
            row.pack(fill="x", pady=11)
            ttk.Checkbutton(row, text=c.key, variable=self.selected[c.key]).pack(side="left", padx=(0, 12))
            ttk.Label(row, text=c.description, style="Sub.TLabel", wraplength=790).pack(side="left", fill="x", expand=True)
        self._buttons(previous=self.show_rounds, next_text="Customize selected recognitions  >", next_command=self._choose_next,
                      extra=[("Select all", lambda: self._select_all(True)),
                             ("Select none", lambda: self._select_all(False))])

    def _select_all(self, yes: bool):
        for var in self.selected.values():
            var.set(yes)

    def _chosen(self) -> list[str]:
        return [k for k in self.configs if self.selected[k].get()]

    def _choose_next(self):
        chosen = self._chosen()
        if not chosen:
            messagebox.showerror("No recognitions chosen", "Select at least one recognition to evaluate.")
            return
        self.current_category = chosen[0]
        self.show_editor()

    def _rule_editor(self, frame, rules: list[Rule], group: str):
        rows = []
        if not rules:
            self._text(frame, "No conditions yet. Use the button below to add one.", color_style="Sub.TLabel")
        for i, rule in enumerate(rules):
            card = ttk.Frame(frame, padding=(7, 6))
            card.pack(fill="x", pady=3)
            enabled = tk.BooleanVar(value=rule.enabled)
            metric = tk.StringVar(value=METRIC_TO_LABEL[rule.metric])
            op = tk.StringVar(value=OP_TO_LABEL[rule.op])
            val = tk.StringVar(value=str(int(rule.value) if rule.value == int(rule.value) else rule.value))
            pop = tk.StringVar(value=rule.population)
            positive = tk.BooleanVar(value=rule.require_positive)
            header = ttk.Frame(card)
            header.pack(fill="x")
            ttk.Checkbutton(header, text="Use", variable=enabled).pack(side="left")
            ttk.Label(header, text="Metric:", style="Sub.TLabel").pack(side="left", padx=(10, 4))
            ttk.Combobox(header, textvariable=metric, values=list(RULE_METRICS),
                         state="readonly", width=46).pack(side="left", fill="x", expand=True, padx=(0, 9))
            ttk.Button(header, text="Remove", command=lambda ix=i, kind=group: self._remove_rule(kind, ix)).pack(side="right")
            controls = ttk.Frame(card)
            controls.pack(fill="x", pady=(5, 0))
            ttk.Label(controls, text="Comparison:", style="Sub.TLabel").pack(side="left", padx=(0, 4))
            ttk.Combobox(controls, textvariable=op, values=list(LABEL_TO_OP),
                         state="readonly", width=21).pack(side="left", padx=(0, 12))
            ttk.Label(controls, text="Value:", style="Sub.TLabel").pack(side="left", padx=(0, 4))
            ttk.Entry(controls, textvariable=val, width=7).pack(side="left", padx=(0, 12))
            advanced = ttk.Frame(card)
            advanced.pack(fill="x", pady=(4, 0))
            ttk.Label(advanced, text="Ranking population:", style="Sub.TLabel").pack(side="left", padx=(0, 4))
            ttk.Combobox(advanced, textvariable=pop, values=["all", "reviewed"],
                         state="readonly", width=10).pack(side="left", padx=(0, 18))
            ttk.Checkbutton(advanced, text="Require a positive metric value", variable=positive).pack(side="left")
            rows.append((rule, enabled, metric, op, val, pop, positive))
            ttk.Separator(card).pack(fill="x", pady=(8, 0))
        self.rule_widgets[group] = rows
        ttk.Button(frame, text="+ Add condition", command=lambda kind=group: self._add_rule(kind)).pack(anchor="w", pady=(5, 12))

    def _read_rule_widgets(self):
        if not self.current_category or not self.rule_widgets:
            return
        c = self.configs[self.current_category]
        for group, elements in self.rule_widgets.items():
            refreshed = []
            for original, enabled, metric, op, val, pop, positive in elements:
                metric_key = RULE_METRICS.get(metric.get())
                operator = LABEL_TO_OP.get(op.get())
                if not metric_key or not operator:
                    raise ValueError("Choose a metric and comparison operator from the provided lists")
                number = 0.0 if operator in BOOL_OPS else float(val.get().strip())
                record = Rule(metric_key, operator, number, enabled.get(), pop.get(), positive.get())
                record.validate()
                refreshed.append(record)
            setattr(c, group, refreshed)
        if hasattr(self, "points_min_var") and c.mode == "points":
            c.min_points = int(self.points_min_var.get())
        if hasattr(self, "match_rank_var") and c.mode == "ranked":
            c.match_rankings = self.match_rank_var.get()
            c.include_ties = self.ties_var.get()
        c.validate()

    def _save_editor(self) -> bool:
        try:
            before = repr(self.configs[self.current_category])
            self._read_rule_widgets()
            after = repr(self.configs[self.current_category])
            if before != after:
                self.reviewed.discard(self.current_category)
                self.preview.pop(self.current_category, None)
            return True
        except (ValueError, TypeError) as exc:
            messagebox.showerror("Invalid award criteria", str(exc))
            return False

    def _refresh_editor(self):
        self.show_editor()

    def _add_rule(self, group: str):
        if not self._save_editor():
            return
        self.configs[self.current_category].__dict__[group].append(Rule("comments", "ge", 1))
        self.reviewed.discard(self.current_category)
        self.preview.pop(self.current_category, None)
        self.show_editor()

    def _remove_rule(self, group: str, index: int):
        if not self._save_editor():
            return
        self.configs[self.current_category].__dict__[group].pop(index)
        self.reviewed.discard(self.current_category)
        self.preview.pop(self.current_category, None)
        self.show_editor()

    def show_editor(self):
        if not self._chosen():
            self.show_choose()
            return
        if self.current_category not in self._chosen():
            self.current_category = self._chosen()[0]
        self.step = "edit"
        self._clear()
        chosen = self._chosen()
        cfg = self.configs[self.current_category]
        self._h1("4 / 4  |  Configure recognitions", "Move between categories, edit conditions, and preview the complete eligible/ineligible reviewer ranking. Your edits persist.")
        main = ttk.Frame(self.page)
        main.pack(fill="both", expand=True)
        sidebar = ttk.Frame(main, width=205)
        sidebar.pack(side="left", fill="y", padx=(0, 14))
        ttk.Label(sidebar, text="Recognition progress", style="Group.TLabel").pack(anchor="w", pady=(0, 8))
        for key in chosen:
            state = "CHECKED" if key in self.reviewed else "NEXT"
            mark = ">" if key == self.current_category else " "
            label = f"{mark} {key}  [{state}]"
            ttk.Button(sidebar, text=label, width=29,
                       command=lambda k=key: self._jump(k)).pack(fill="x", pady=4)
        ttk.Separator(sidebar).pack(fill="x", pady=13)
        ttk.Label(sidebar, text=f"{len(self.reviewed & set(chosen))}/{len(chosen)} previewed", style="Sub.TLabel").pack(anchor="w")
        center = ttk.Frame(main)
        center.pack(side="left", fill="both", expand=True)
        ttk.Label(center, text=cfg.key, style="Group.TLabel").pack(anchor="w", pady=(0, 4))
        self._text(center, cfg.description, pady=(0, 10))
        area = ScrollArea(center, height=380)
        area.pack(fill="both", expand=True)
        self.rule_widgets = {}
        if cfg.mode == "points":
            mins = ttk.Frame(area.body)
            mins.pack(fill="x", pady=9)
            ttk.Label(mins, text="Required points", style="Group.TLabel").pack(side="left")
            self.points_min_var = tk.StringVar(value=str(cfg.min_points))
            ttk.Entry(mins, width=6, textvariable=self.points_min_var).pack(side="left", padx=13)
            self._text(area.body, "Each enabled scored criterion contributes one point; criteria can be added, removed, or changed.", color_style="Sub.TLabel")
            ttk.Label(area.body, text="Scored criteria", style="Group.TLabel").pack(anchor="w", pady=(12, 4))
            self._rule_editor(area.body, cfg.points, "points")
        if cfg.mode == "ranked":
            rank_head = ttk.Frame(area.body)
            rank_head.pack(fill="x", pady=9)
            ttk.Label(rank_head, text="When multiple rankings are enabled:", style="Sub.TLabel").pack(side="left")
            self.match_rank_var = tk.StringVar(value=cfg.match_rankings)
            ttk.Combobox(rank_head, textvariable=self.match_rank_var, state="readonly", width=7,
                         values=["any", "all"]).pack(side="left", padx=8)
            self.ties_var = tk.BooleanVar(value=cfg.include_ties)
            ttk.Checkbutton(rank_head, text="Include ties at cutoff", variable=self.ties_var).pack(side="left", padx=15)
            ttk.Label(area.body, text="Ranking conditions", style="Group.TLabel").pack(anchor="w", pady=(12, 4))
            self._rule_editor(area.body, cfg.rankings, "rankings")
        ttk.Label(area.body, text="Mandatory conditions (all must pass)", style="Group.TLabel").pack(anchor="w", pady=(12, 4))
        self._rule_editor(area.body, cfg.filters, "filters")
        self._text(area.body, "Operators: top N uses the Nth reviewer in the eligible population; top percent uses ceil(percent x population), with ties when enabled. 'reviewed' population excludes members with zero submitted reviews. A manually confirmed discussion count is a verified minimum, not an increment.", color_style="Sub.TLabel", pady=9)
        self.inline = ttk.Frame(center)
        self.inline.pack(fill="x", pady=(6, 0))
        saved = self.preview.get(cfg.key)
        if saved:
            self._inline_result(saved)
        self._buttons(previous=self.show_choose, next_text="Preview and next  >", next_command=self._next_recognition,
                      extra=[("Preview all reviewers", self._preview_current), ("Summary", self._finish)])

    def _jump(self, key: str):
        if key == self.current_category:
            return
        if not self._save_editor():
            return
        self.current_category = key
        self.show_editor()

    def _compute_current(self) -> ReviewResult | None:
        if not self._save_editor() or self.program is None:
            return None
        try:
            result = evaluate_award(self.program, self.configs[self.current_category])
        except (ValueError, KeyError) as exc:
            messagebox.showerror("Recognition calculation blocked", str(exc))
            return None
        self.preview[self.current_category] = result
        self.reviewed.add(self.current_category)
        self._inline_result(result)
        return result

    def _inline_result(self, result: ReviewResult):
        for child in self.inline.winfo_children():
            child.destroy()
        matching = [row["name"] for row in result.rows if row["status"] == "SELECTED"]
        ttk.Label(self.inline, text=f"Preview: {len(matching)} recipients / {len(result.rows)} PC members",
                  style="Group.TLabel").pack(anchor="w")
        if matching:
            ttk.Label(self.inline, text=", ".join(matching[:12]) + (" ..." if len(matching) > 12 else ""),
                      wraplength=820, justify="left").pack(anchor="w", pady=3)
        else:
            ttk.Label(self.inline, text="No members qualify at these thresholds.", style="Sub.TLabel").pack(anchor="w")

    def _preview_current(self):
        result = self._compute_current()
        if result:
            self._table_window(result)

    def _next_recognition(self):
        if not self._compute_current():
            return
        selected = self._chosen()
        idx = selected.index(self.current_category)
        if idx + 1 < len(selected):
            self.current_category = selected[idx + 1]
            self.show_editor()
        else:
            self._finish()

    def _table_window(self, result: ReviewResult):
        win = tk.Toplevel(self)
        win.title(f"{result.config.key} - All PC members")
        win.geometry("1320x650")
        ttk.Label(win, text=f"{result.config.key} | {len(result.recipients)} selected | {len(result.rows)} total reviewers",
                  style="Group.TLabel").pack(anchor="w", padx=12, pady=(10, 7))
        base = ["name", "email", "affiliation", "status", "score"]
        actual_metrics = []
        for rule in result.config.filters + result.config.rankings + result.config.points:
            if rule.enabled and rule.metric not in actual_metrics:
                actual_metrics.append(rule.metric)
        details = [key for key in result.rows[0] if key.startswith("filter_") or key.startswith("rule_")] if result.rows else []
        columns = base + actual_metrics + details
        wrap = ttk.Frame(win)
        wrap.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        tree = ttk.Treeview(wrap, columns=columns, show="headings", selectmode="browse")
        y = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        x = ttk.Scrollbar(wrap, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=y.set, xscrollcommand=x.set)
        tree.grid(row=0, column=0, sticky="nsew")
        y.grid(row=0, column=1, sticky="ns")
        x.grid(row=1, column=0, sticky="ew")
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)
        for column in columns:
            label = METRICS[column].label if column in METRICS else column
            width = (195 if column in ("name", "affiliation") else
                     235 if column == "email" else 132)
            tree.heading(column, text=label)
            tree.column(column, width=width, minwidth=80, stretch=False, anchor="w")
        for row in result.rows:
            tree.insert("", "end", values=[str(row.get(c) if row.get(c) is not None else "N/A") for c in columns],
                        tags=("selected",) if row["status"] == "SELECTED" else ())
        tree.tag_configure("selected", background="#e4f2e8")
        ttk.Label(win, text="Green rows = selected. All reviewers remain visible, including those who do not qualify.",
                  style="Sub.TLabel").pack(anchor="w", padx=12, pady=(0, 9))

    def _finish(self):
        if self.step == "edit" and not self._save_editor():
            return
        chosen = self._chosen()
        outstanding = [n for n in chosen if n not in self.reviewed]
        if outstanding:
            messagebox.showwarning("Preview pending", "Preview each chosen recognition before finalizing:\n" + ", ".join(outstanding))
            return
        try:
            all_results, rows, totals = award_report(self.program, [self.configs[x] for x in chosen])
        except (ValueError, KeyError) as exc:
            messagebox.showerror("Cannot complete selection", str(exc))
            return
        if any(any(r["status"] == "POSSIBLE" for r in result.rows) for result in all_results):
            if not messagebox.askyesno("Incomplete timestamps", "Some reviewers have ambiguous or missing timestamps and therefore appear as POSSIBLE in the result tables. Only confirmed recipients will be exported. Continue?"):
                return
        self.final_rows = rows
        self.final_totals = totals
        self.final_results = all_results
        self.show_summary()

    def show_summary(self):
        self.step = "summary"
        self._clear()
        self._h1("Review and export", "Your selected recognitions are computed from the exact saved rules and unchanged HotCRP metrics.")
        pc_total = len(self.final_rows)
        unique = sum(r["award_count"] > 0 for r in self.final_rows)
        badges = sum(self.final_totals.values())
        counts = ttk.Frame(self.page)
        counts.pack(fill="x", pady=10)
        for title, value in (("TOTAL AWARDS", badges), ("RECOGNIZED PC MEMBERS", unique),
                             ("PC MEMBERS EVALUATED", pc_total)):
            frame = ttk.LabelFrame(counts, text=title, padding=14)
            frame.pack(side="left", fill="x", expand=True, padx=7)
            ttk.Label(frame, text=str(value), font=("Arial", 23, "bold")).pack(anchor="center")
        percent = (100.0 * unique / pc_total) if pc_total else 0
        self._text(self.page, f"{percent:.1f}% of the reviewing PC receives at least one recognition.")
        chart = tk.Canvas(self.page, height=270, bg="#ffffff", highlightthickness=0)
        chart.pack(fill="x", pady=13)
        max_awards = max([1] + list(self.final_totals.values()))
        def redraw(event):
            chart.delete("all")
            left, top = min(205, max(140, event.width // 4)), 12
            available_width = max(60, event.width - left - 65)
            for index, (key, number) in enumerate(self.final_totals.items()):
                y = top + index * 28
                chart.create_text(left - 13, y + 9, text=key, anchor="e", fill="#25374c", font=("Arial", 10))
                length = available_width * number / max_awards
                chart.create_rectangle(left, y, left + length, y + 18, fill="#3e74bb", outline="")
                chart.create_text(left + length + 8, y + 9, text=str(number), anchor="w", fill="#25374c", font=("Arial", 10, "bold"))
        chart.bind("<Configure>", redraw)
        self._text(self.page, "CSV columns: given name, family name, full name, email, affiliation, one 1/0 column per chosen recognition, a readable list of awards, and award count. All eligible PC members are included.")
        self._buttons(previous=self._summary_back, next_text="Export recognition CSV", next_command=self._export,
                      extra=[("Inspect award table", self._show_all_results),
                             ("Export rule settings", self._export_rules)])

    def _summary_back(self):
        self.current_category = self._chosen()[-1]
        self.show_editor()

    def _show_all_results(self):
        win = tk.Toplevel(self)
        win.title("Recognition export preview (all PC members)")
        win.geometry("1180x660")
        columns = ["name", "email", "affiliation"] + self._chosen() + ["award_count"]
        main = ttk.Frame(win, padding=12)
        main.pack(fill="both", expand=True)
        tree = ttk.Treeview(main, columns=columns, show="headings")
        y = ttk.Scrollbar(main, orient="vertical", command=tree.yview)
        x = ttk.Scrollbar(main, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=y.set, xscrollcommand=x.set)
        tree.grid(row=0, column=0, sticky="nsew")
        y.grid(row=0, column=1, sticky="ns")
        x.grid(row=1, column=0, sticky="ew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(0, weight=1)
        for field in columns:
            tree.heading(field, text=field)
            tree.column(field, width=(180 if field in ("name", "affiliation") else 240 if field == "email" else 115),
                        stretch=False)
        for row in self.final_rows:
            tree.insert("", "end", values=[row.get(c, "") for c in columns])

    def _export_rules(self):
        # Reproducibility record contains criteria and process settings, but
        # deliberately omits sensitive source paths, HotCRP content, and emails.
        filename = filedialog.asksaveasfilename(
            parent=self, title="Export award rule settings", defaultextension=".json",
            initialfile="pc_recognition_rules.json", filetypes=[("JSON files", "*.json")])
        if not filename:
            return
        record = {
            "format": "PC Recognition Selector settings v1",
            "reviewing_rounds": list(self.rounds),
            "grace_days": float(self.grace_days.get()),
            "minimum_review_words": int(self.min_review_words.get()),
            "minimum_comment_words": int(self.min_comment_words.get()),
            "exclude_chair_roles": bool(self.exclude_chairs.get()),
            "supplementary_sources_supplied": {
                key: bool(self.file_paths[key].get().strip()) for key in (
                    "firefighters", "upvotes", "spotlight", "delay_overrides",
                    "discussion_corrections", "upvote_corrections")
            },
            "recognitions": [asdict(self.configs[k]) for k in self._chosen()],
        }
        try:
            Path(filename).write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n",
                                      encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Could not export settings", str(exc))
            return
        messagebox.showinfo("Rule settings saved", f"Saved recognition thresholds and process settings to:\n{filename}")

    def _export(self):
        filename = filedialog.asksaveasfilename(
            parent=self, title="Export PC recognitions", defaultextension=".csv",
            initialfile="pc_recognitions.csv", filetypes=[("CSV files", "*.csv")])
        if not filename:
            return
        try:
            export_csv(filename, self.final_rows, [self.configs[k] for k in self._chosen()])
        except (OSError, ValueError) as exc:
            messagebox.showerror("Export failed", str(exc))
            return
        messagebox.showinfo("Export completed", f"Saved {len(self.final_rows)} PC members to:\n{filename}\n\nNo data was transmitted.")
        self.status.configure(text=f"Exported results to {filename}")

    def _close(self):
        if self.busy and not messagebox.askyesno("Close while processing?", "The local pipeline is still running. Close anyway?"):
            return
        if self.temp:
            self.temp.cleanup()
        self.destroy()


def main():
    DesktopApp().mainloop()


if __name__ == "__main__":
    main()
