"""Tests for the interactive portfolio timeline and its demo fixtures."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import app
import database
from sample_data import SAMPLE_PREFIX, load_sample_data


class TimelineChartTests(unittest.TestCase):
    def test_sample_data_is_idempotent_and_covers_date_ranges(self) -> None:
        today = date(2026, 9, 27)
        with tempfile.TemporaryDirectory() as temporary:
            db_path = str(Path(temporary) / "applications.db")
            first_ids = load_sample_data(db_path, today=today)
            second_ids = load_sample_data(db_path, today=today)

            jobs = database.list_applications(db_path=db_path)
            self.assertEqual(first_ids, second_ids)
            self.assertEqual(len(jobs), 4)
            self.assertTrue(all(job["company"].startswith(SAMPLE_PREFIX) for job in jobs))
            self.assertLess(min(date.fromisoformat(job["recruitment_start"]) for job in jobs), today - timedelta(days=28))
            self.assertGreater(max(date.fromisoformat(job["recruitment_start"]) for job in jobs), today)

    def test_chart_uses_box_segments_and_today_is_at_forty_percent(self) -> None:
        today = app.TODAY
        snapshots = [
            {
                "job": {
                    "id": 1,
                    "company": "测试公司",
                    "role": "测试岗位",
                    "recruitment_start": (today - timedelta(days=50)).isoformat(),
                    "recruitment_end": (today + timedelta(days=10)).isoformat(),
                },
                "timeline": [
                    {
                        "date": (today - timedelta(days=50)).isoformat(),
                        "event_type": "招聘开始",
                        "stage": "待投递",
                        "details": "职位开放",
                        "deadline_at": "",
                        "status": "已发生",
                    },
                    {
                        "date": (today + timedelta(days=10)).isoformat(),
                        "event_type": "投递截止",
                        "stage": "待投递",
                        "details": "截止",
                        "deadline_at": (today + timedelta(days=10)).isoformat(),
                        "status": "待办",
                    },
                ],
                "stage": "待投递",
                "focus_time": (today + timedelta(days=10)).isoformat(),
                "row_label": "测试公司 · 测试岗位 〔1〕",
            }
        ]

        spec = app._portfolio_chart(snapshots).to_dict()
        bar = spec["layer"][0]
        domain = [date.fromisoformat(value) for value in bar["encoding"]["x"]["scale"]["domain"]]
        today_position = (today - domain[0]).days / (domain[1] - domain[0]).days

        self.assertEqual(bar["mark"]["type"], "bar")
        self.assertTrue(bar["mark"]["clip"])
        self.assertGreaterEqual(bar["mark"]["size"], 36)
        self.assertEqual(spec["layer"][1]["mark"]["type"], "text")
        label_layer = spec["layer"][1]
        label_calculations = " ".join(
            transform.get("calculate", "") for transform in label_layer["transform"]
        )
        self.assertIn("timeline_window['开始']", label_calculations)
        self.assertIn(domain[0].isoformat(), label_calculations)
        self.assertIn(domain[1].isoformat(), label_calculations)
        self.assertEqual(label_layer["encoding"]["x"]["field"], "_dynamic_label_at")
        self.assertIn("filter", label_layer["transform"][-1])
        self.assertAlmostEqual(today_position, 0.4)
        self.assertEqual(spec["params"][0]["bind"], "scales")


if __name__ == "__main__":
    unittest.main()
