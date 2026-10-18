"""Standard-library-only regression checks for the native recognition app."""
from __future__ import annotations

import copy
import csv
import tempfile
import unittest
from pathlib import Path

from desktop_app.engine import (
    Rule, award_report, default_awards, email_lines, evaluate_award,
    export_csv, load_program, approved_aliases,
)
from desktop_app.pipeline import run_hotcrp, verify_schedule

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "sample_data"


class PipelineIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="pc_app_test_")
        dest = Path(cls.temp.name)
        cls.outputs = run_hotcrp(
            input_dir=dest / "input",
            output_dir=dest / "output",
            pc=SAMPLE / "sampleVenue-pcinfo.csv",
            prefs=SAMPLE / "sampleVenue-allprefs.csv",
            log=SAMPLE / "sampleVenue-log.csv",
            reviews=SAMPLE / "sampleVenue-reviews.txt",
            thresholds=[2], starts=["2026-07-31T00:00:00-12:00"],
            deadlines=["2026-08-25T23:59:59-12:00"],
            grace=2, min_review_words=50, min_comment_words=15)
        cls.data = load_program(
            cls.outputs["pc_metrics"], SAMPLE / "sampleVenue-pcinfo.csv",
            firefighters=SAMPLE / "sampleVenue-firefighters.csv",
            upvotes=SAMPLE / "sampleVenue-upvotes.csv")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_complete_real_pipeline_with_synthetic_data(self):
        self.assertEqual(len(self.data.reviewers), 8)
        self.assertEqual(self.data.topics_total, 3)
        self.assertGreater(self.data.raw_review_count, 10)
        self.assertTrue(Path(self.outputs["pc_metrics"]).is_file())

    def test_all_awards_except_spotlight_dependency(self):
        for config in default_awards():
            if config.key == "DISTINGUISHED":
                # The synthetic repository does not ship a spotlight voter file.
                with self.assertRaisesRegex(ValueError, "spotlight"):
                    evaluate_award(self.data, config)
                continue
            result = evaluate_award(self.data, config)
            self.assertEqual(len(result.rows), 8)
            self.assertTrue(result.recipients <= {p.email for p in self.data.reviewers})

    def test_custom_committed_rule_and_export(self):
        committed = next(x for x in default_awards() if x.key == "COMMITTED")
        baseline = evaluate_award(self.data, committed)
        committed.filters[0].enabled = False  # drop bidding condition
        committed.filters.append(Rule("comments", "ge", 1))  # add an engagement rule
        modified = evaluate_award(self.data, committed)
        self.assertTrue(isinstance(baseline.recipients, set))
        self.assertTrue(isinstance(modified.recipients, set))
        self.assertEqual(len(modified.rows), 8)
        _, rows, totals = award_report(self.data, [committed])
        path = Path(self.temp.name) / "awards.csv"
        export_csv(path, rows, [committed])
        with path.open(encoding="utf-8-sig", newline="") as f:
            parsed = list(csv.DictReader(f))
        self.assertEqual(len(parsed), 8)
        self.assertEqual(set(parsed[0]), {"given_name", "family_name", "name", "email",
                                          "affiliation", "COMMITTED", "recognitions", "award_count"})
        self.assertEqual(totals["COMMITTED"], len(modified.recipients))

    def test_extras_only_when_chair_file_given(self):
        with_file = self.data
        no_file = load_program(self.outputs["pc_metrics"], SAMPLE / "sampleVenue-pcinfo.csv")
        self.assertNotIn("firefighters", no_file.available)
        self.assertTrue(all(p.metrics["extra_reviews"] is None for p in no_file.reviewers))
        firefighter = next(c for c in default_awards() if c.key == "FIREFIGHTER")
        with self.assertRaisesRegex(ValueError, "firefighters"):
            evaluate_award(no_file, firefighter)
        self.assertEqual(len(evaluate_award(with_file, firefighter).rows), 8)

    def test_topic_rule_all_vs_minimum(self):
        config = next(c for c in default_awards() if c.key == "COMMITTED")
        all_topics = evaluate_award(self.data, config)
        config.filters[1] = Rule("topics_explicit_count", "ge", 2)
        relaxed = evaluate_award(self.data, config)
        self.assertGreaterEqual(len(relaxed.recipients), len(all_topics.recipients))

    def test_round_validation(self):
        verify_schedule([2], ["2026-07-31T00:00:00-12:00"], ["2026-08-25T23:59:59-12:00"], 2)
        with self.assertRaises(ValueError):
            verify_schedule([2, 3], ["2026-07-31T00:00:00-12:00"],
                            ["2026-08-25T23:59:59-12:00"], 2)
        with self.assertRaises(ValueError):
            verify_schedule([2], ["2026-07-31"], ["2026-08-25"], 2)

    def test_unknown_email_and_invalid_extras_are_rejected(self):
        path = Path(self.temp.name) / "wrong.csv"
        path.write_text("email,paper\nunknown@example.invalid,123\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unknown email"):
            load_program(self.outputs["pc_metrics"], SAMPLE / "sampleVenue-pcinfo.csv", firefighters=path)
        email = self.data.reviewers[0].email
        path.write_text(f"email,paper\n{email},99999\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not present"):
            load_program(self.outputs["pc_metrics"], SAMPLE / "sampleVenue-pcinfo.csv", firefighters=path)

    def test_manual_override_retains_raw_delays(self):
        person = self.data.reviewers[0]
        path = Path(self.temp.name) / "extensions.txt"
        path.write_text(person.email + "\n", encoding="utf-8")
        changed = load_program(self.outputs["pc_metrics"], SAMPLE / "sampleVenue-pcinfo.csv", delay_overrides=path)
        a = next(p for p in changed.reviewers if p.email == person.email)
        self.assertTrue(a.metrics["delay_override"])
        self.assertEqual(a.audit["original_late_reviews"], person.audit["original_late_reviews"])

    def test_rule_configuration_validation(self):
        c = next(x for x in default_awards() if x.key == "DISTINGUISHED")
        self.assertEqual(len(c.points), 12)
        c.points.append(Rule("comments", "ge", 5))
        c.min_points = 9
        c.validate()
        with self.assertRaises(ValueError):
            Rule("on_time", "ge", 1).validate()
        committed = next(x for x in default_awards() if x.key == "COMMITTED")
        for rule in committed.filters:
            rule.enabled = False
        with self.assertRaisesRegex(ValueError, "enable at least one"):
            committed.validate()

    def test_csv_export_defuses_spreadsheet_formulas(self):
        from desktop_app.engine import export_csv
        config = next(x for x in default_awards() if x.key == "COMMITTED")
        row = {"given_name": "=1+2", "family_name": "Reviewer", "name": "=1+2 Reviewer",
               "email": "tester@example.invalid", "affiliation": "+HYPERLINK('x')",
               "COMMITTED": 0, "recognitions": "", "award_count": 0}
        path = Path(self.temp.name) / "formula.csv"
        export_csv(path, [row], [config])
        with path.open(encoding="utf-8-sig", newline="") as handle:
            value = next(csv.DictReader(handle))
        self.assertEqual(value["given_name"], "'=1+2")
        self.assertEqual(value["affiliation"], "'+HYPERLINK('x')")

    def test_email_parsing_aliases(self):
        f = Path(self.temp.name) / "spotlight_votes"
        f.write_text("email\na@example.org\nb@example.org\n", encoding="utf-8")
        self.assertEqual(email_lines(f), ["a@example.org", "b@example.org"])
        self.assertEqual(approved_aliases("Alias@E.com,Canonical@E.com"),
                         {"alias@e.com": "canonical@e.com"})


if __name__ == "__main__":
    unittest.main()
