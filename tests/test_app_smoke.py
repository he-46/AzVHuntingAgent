"""Exercise the primary Streamlit path with an isolated database."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import database


class AppSmokeTests(unittest.TestCase):
    def test_resume_only_input_saves_candidate_profile(self) -> None:
        source = "张三，示例大学统计学。曾在零售公司实习。技能：Python、SQL。"
        extracted = {
            "company": "",
            "role": "",
            "company_info": "",
            "jd": "",
            "recruitment_start": None,
            "recruitment_end": None,
            "events": [],
            "candidate_profile": {
                "name": "张三",
                "summary": "",
                "education": ["示例大学统计学"],
                "experiences": [],
                "internships": ["曾在零售公司实习"],
                "projects": [],
                "skills": ["Python", "SQL"],
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            db_path = str(Path(temporary) / "applications.db")
            with patch.dict(os.environ, {"JOB_AGENT_DB_PATH": db_path}), patch(
                "extractor.extract_job_info", return_value=extracted
            ):
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=30)
                next(widget for widget in app.text_area if widget.label == "总输入框").set_value(source)
                next(button for button in app.button if button.label == "AI 分拣信息").click().run()
                self.assertEqual(app.get_by_key("draft_candidate_name_1").value, "张三")
                next(button for button in app.button if button.label == "仅保存求职者资料").click().run()
                self.assertFalse(app.exception)

            profile = database.get_candidate_profile(db_path)
            self.assertEqual(profile["skills"], ["Python", "SQL"])
            self.assertEqual(database.list_applications(db_path), [])

    def test_create_job_and_record_application(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            db_path = str(Path(temporary) / "applications.db")
            with patch.dict(os.environ, {"JOB_AGENT_DB_PATH": db_path}):
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=30)
                self.assertFalse(app.exception)
                app.text_input[0].set_value("星辰科技")
                app.text_input[1].set_value("数据分析师")
                app.text_input[2].set_value((date.today() - timedelta(days=1)).isoformat())
                app.text_input[3].set_value((date.today() + timedelta(days=10)).isoformat())
                next(widget for widget in app.text_area if widget.label == "公司信息（可选）").set_value(
                    "公司采用两轮面试。"
                )
                next(button for button in app.button if button.label == "创建职位").click().run()
                self.assertFalse(app.exception)
                jobs = database.list_applications(db_path=db_path)
                self.assertEqual(len(jobs), 1)
                self.assertEqual(jobs[0]["company_info"], "公司采用两轮面试。")

                event_type = next(widget for widget in app.selectbox if widget.label == "事件类型")
                event_type.set_value("已投递")
                next(button for button in app.button if button.label == "添加事件").click().run()
                self.assertFalse(app.exception)
                events = database.list_events(jobs[0]["id"], db_path=db_path)
                self.assertEqual(
                    {event["event_type"] for event in events},
                    {"招聘开始", "投递截止", "已投递"},
                )
                self.assertEqual(app.metric[0].value, "简历筛选中")
                self.assertEqual(len(app.get("vega_lite_chart")), 1)
                overview_headers = [expander.label for expander in app.expander]
                self.assertTrue(
                    any(
                        all(value in label for value in ("星辰科技", "数据分析师", "简历筛选中"))
                        for label in overview_headers
                    )
                )

    def test_one_box_ai_review_creates_populated_job(self) -> None:
        source = (
            "星辰科技招聘数据分析师。公司专注零售数据。JD：负责业务指标分析。"
            "招聘开始：2026年9月1日，投递截止：2026年10月15日。"
            "我在2026年9月20日投递。"
        )
        extracted = {
            "company": "星辰科技",
            "role": "数据分析师",
            "company_info": "公司专注零售数据。",
            "jd": "JD：负责业务指标分析。",
            "recruitment_start": "2026-09-01",
            "recruitment_end": "2026-10-15",
            "events": [
                {
                    "event_type": "已投递",
                    "event_date": "2026-09-20",
                    "details": "完成投递",
                    "deadline_at": None,
                    "deadline_kind": None,
                    "feedback_score": None,
                    "source_quote": "我在2026年9月20日投递",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            db_path = str(Path(temporary) / "applications.db")
            with patch.dict(os.environ, {"JOB_AGENT_DB_PATH": db_path}), patch(
                "extractor.extract_job_info", return_value=extracted
            ):
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=30)
                next(widget for widget in app.text_area if widget.label == "总输入框").set_value(source)
                next(button for button in app.button if button.label == "AI 分拣信息").click().run()
                self.assertFalse(app.exception)
                self.assertEqual(app.get_by_key("draft_company_1").value, "星辰科技")
                self.assertEqual(app.get_by_key("draft_role_1").value, "数据分析师")
                next(button for button in app.button if button.label == "确认写入时间线").click().run()
                self.assertFalse(app.exception)

                # Reprocessing the same source should update the matched job and
                # keep the factual events idempotent.
                next(widget for widget in app.text_area if widget.label == "总输入框").set_value(source)
                next(button for button in app.button if button.label == "AI 分拣信息").click().run()
                self.assertEqual(app.get_by_key("draft_target_2").value, 1)
                next(button for button in app.button if button.label == "确认写入时间线").click().run()
                self.assertFalse(app.exception)

            jobs = database.list_applications(db_path=db_path)
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["company_info"], "公司专注零售数据。")
            self.assertEqual(jobs[0]["jd"], "JD：负责业务指标分析。")
            self.assertEqual(len(database.list_intake_entries(jobs[0]["id"], db_path=db_path)), 2)
            self.assertEqual(
                {event["event_type"] for event in database.list_events(jobs[0]["id"], db_path=db_path)},
                {"招聘开始", "投递截止", "已投递"},
            )


if __name__ == "__main__":
    unittest.main()
