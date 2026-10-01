"""Exercise the primary Streamlit path with an isolated database."""

from __future__ import annotations

import os
import io
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from docx import Document

import database
from extractor import ExtractionError


class AppSmokeTests(unittest.TestCase):
    def test_selected_compatible_model_reaches_intake(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            db_path = str(Path(temporary) / "applications.db")
            with patch.dict(os.environ, {"JOB_AGENT_DB_PATH": db_path}), patch(
                "extractor.extract_intake", side_effect=ExtractionError("test-only")
            ) as mocked:
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=30)
                app.get_by_key("llm_provider").set_value("兼容接口").run()
                app.get_by_key("llm_compatible_model").set_value("other-model").run()
                app.get_by_key("llm_compatible_base_url").set_value("https://provider.example/v1").run()
                app.get_by_key("兼容接口_api_key_input_0").set_value("other-key")
                next(button for button in app.button if button.label == "应用密钥").click().run()
                next(widget for widget in app.text_area if widget.label == "总输入框").set_value("示例职位")
                next(button for button in app.button if button.label == "AI 分拣信息").click().run()
                self.assertFalse(app.exception)
                config = mocked.call_args.kwargs["llm_config"]
                self.assertEqual((config.api_style, config.model, config.base_url, config.api_key), (
                    "chat_completions", "other-model", "https://provider.example/v1", "other-key",
                ))

    def test_multi_job_draft_saves_links_without_unassigned_event(self) -> None:
        source = (
            "甲公司招聘算法工程师，链接 https://jobs.example.com/a。"
            "乙公司招聘数据分析师，链接 https://jobs.example.com/b。"
            "2026年10月15日完成一面，尚不清楚属于哪个岗位。"
        )
        def job(company, role, link):
            return {
                "company": company, "role": role,
                "company_info": "", "jd": "",
                "recruitment_start": None, "recruitment_end": None,
                "link_url": link, "events": [],
                "evidence": {},
            }
        extracted = {
            "jobs": [
                job("甲公司", "算法工程师", "https://jobs.example.com/a"),
                job("乙公司", "数据分析师", "https://jobs.example.com/b"),
            ],
            "unassigned_events": [{
                "event_type": "面试完成", "event_date": "2026-10-15",
                "details": "完成一面", "deadline_at": None,
                "deadline_kind": None, "feedback_score": None,
                "source_quote": "2026年10月15日完成一面",
            }],
            "candidate_profile": {},
        }
        with tempfile.TemporaryDirectory() as temporary:
            db_path = str(Path(temporary) / "applications.db")
            with patch.dict(os.environ, {"JOB_AGENT_DB_PATH": db_path}), patch(
                "extractor.extract_intake", return_value=extracted
            ):
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=30)
                next(widget for widget in app.text_area if widget.label == "总输入框").set_value(source)
                next(button for button in app.button if button.label == "AI 分拣信息").click().run()
                next(button for button in app.button if button.label == "确认保存当前岗位与时间线").click().run()
                self.assertFalse(app.exception)
                next(button for button in app.button if button.label == "确认保存当前岗位与时间线").click().run()
                self.assertFalse(app.exception)
                app.get_by_key("draft_job_choice_1").set_value(1).run()
                next(button for button in app.button if button.label == "确认保存当前岗位与时间线").click().run()
                self.assertFalse(app.exception)
            jobs = database.list_applications(db_path)
            self.assertEqual(len(jobs), 2)
            self.assertEqual({job["link_url"] for job in jobs}, {
                "https://jobs.example.com/a", "https://jobs.example.com/b",
            })
            self.assertEqual(
                sum(len(database.list_events(job["id"], db_path)) for job in jobs), 0
            )

    def test_docx_upload_populates_unified_input_before_ai_call(self) -> None:
        document = Document()
        document.add_paragraph("张三，统计学专业，掌握 Python 和 SQL。")
        output = io.BytesIO()
        document.save(output)
        with tempfile.TemporaryDirectory() as temporary:
            db_path = str(Path(temporary) / "applications.db")
            with patch.dict(os.environ, {"JOB_AGENT_DB_PATH": db_path}):
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=30)
                uploader = next(
                    widget for widget in app.file_uploader
                    if widget.label == "导入 Word / PDF 文本"
                )
                uploader.upload("resume.docx", output.getvalue()).run()
                next(
                    button for button in app.button
                    if button.label == "将文件文字加入总输入框"
                ).click().run()
                self.assertFalse(app.exception)
                value = next(
                    widget.value for widget in app.text_area
                    if widget.label == "总输入框"
                )
                self.assertIn("张三，统计学专业", value)

    def test_resume_only_input_saves_candidate_profile(self) -> None:
        source = "张三，示例大学统计学。曾在零售公司实习。技能：Python、SQL。"
        extracted = {
            "jobs": [],
            "unassigned_events": [],
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
                "extractor.extract_intake", return_value=extracted
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
        job = {
            "company": "星辰科技",
            "role": "数据分析师",
            "company_info": "公司专注零售数据。",
            "jd": "JD：负责业务指标分析。",
            "recruitment_start": "2026-09-01",
            "recruitment_end": "2026-10-15",
            "link_url": "https://jobs.example.com/analyst",
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
        extracted = {"jobs": [job], "unassigned_events": [], "candidate_profile": {}}
        with tempfile.TemporaryDirectory() as temporary:
            db_path = str(Path(temporary) / "applications.db")
            with patch.dict(os.environ, {"JOB_AGENT_DB_PATH": db_path}), patch(
                "extractor.extract_intake", return_value=extracted
            ):
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=30)
                next(widget for widget in app.text_area if widget.label == "总输入框").set_value(source)
                next(button for button in app.button if button.label == "AI 分拣信息").click().run()
                self.assertFalse(app.exception)
                self.assertEqual(app.get_by_key("draft_company_1").value, "星辰科技")
                self.assertEqual(app.get_by_key("draft_role_1").value, "数据分析师")
                next(button for button in app.button if button.label == "确认保存当前岗位与时间线").click().run()
                self.assertFalse(app.exception)

                # Reprocessing the same source should update the matched job and
                # keep the factual events idempotent.
                next(widget for widget in app.text_area if widget.label == "总输入框").set_value(source)
                next(button for button in app.button if button.label == "AI 分拣信息").click().run()
                self.assertEqual(app.get_by_key("draft_target_2").value, 1)
                next(button for button in app.button if button.label == "确认保存当前岗位与时间线").click().run()
                self.assertFalse(app.exception)

            jobs = database.list_applications(db_path=db_path)
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]["company_info"], "公司专注零售数据。")
            self.assertEqual(jobs[0]["jd"], "JD：负责业务指标分析。")
            self.assertEqual(jobs[0]["link_url"], "https://jobs.example.com/analyst")
            self.assertEqual(len(database.list_intake_entries(jobs[0]["id"], db_path=db_path)), 2)
            self.assertEqual(
                {event["event_type"] for event in database.list_events(jobs[0]["id"], db_path=db_path)},
                {"招聘开始", "投递截止", "已投递"},
            )


if __name__ == "__main__":
    unittest.main()
