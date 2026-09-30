"""Offline tests for the optional AI extraction boundary."""

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from extractor import ExtractionError, _IntakeBatch, _JobInfo, extract_intake, extract_job_info


class _FakeResponses:
    def __init__(self, parsed, status="completed"):
        self.parsed = parsed
        self.status = status
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        return types.SimpleNamespace(output_parsed=self.parsed, status=self.status)


class ExtractionTests(unittest.TestCase):
    def _call_batch(self, text, parsed):
        responses = _FakeResponses(_IntakeBatch.model_validate(parsed))
        fake_openai = types.SimpleNamespace(
            OpenAI=lambda **kwargs: types.SimpleNamespace(responses=responses)
        )
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {
                "OPENAI_API_KEY": "test-key",
                "JOB_AGENT_DB_PATH": str(Path(temporary) / "usage.db"),
            }), patch.dict(sys.modules, {"openai": fake_openai}):
                return extract_intake(text), responses.kwargs

    def test_batch_keeps_jobs_separate_and_unassigns_ambiguous_events(self):
        text = (
            "甲公司招聘算法工程师。甲公司投递截止：2026年10月10日。"
            "甲公司投递链接：https://jobs.example.com/a。"
            "乙公司招聘数据分析师。乙公司投递截止：2026年10月20日。"
            "乙公司投递链接：https://jobs.example.com/b。"
            "2026年10月15日完成一面。"
        )
        def event(quote):
            return {
                "event_type": "面试完成", "event_date": "2026-10-15",
                "details": "完成一面", "deadline_at": None,
                "deadline_kind": None, "source": "输入文本",
                "source_quote": quote, "feedback_score": None,
            }
        parsed = {
            "jobs": [
                {
                    "company": "甲公司", "company_quote": "甲公司招聘算法工程师",
                    "role": "算法工程师", "role_quote": "甲公司招聘算法工程师",
                    "company_info": "", "jd": "",
                    "recruitment_start": None, "recruitment_start_quote": "",
                    "recruitment_end": "2026-10-10",
                    "recruitment_end_quote": "甲公司投递截止：2026年10月10日",
                    "link_url": "https://jobs.example.com/a",
                    "link_quote": "甲公司投递链接：https://jobs.example.com/a",
                    "events": [event("2026年10月15日完成一面")],
                },
                {
                    "company": "乙公司", "company_quote": "乙公司招聘数据分析师",
                    "role": "数据分析师", "role_quote": "乙公司招聘数据分析师",
                    "company_info": "", "jd": "",
                    "recruitment_start": None, "recruitment_start_quote": "",
                    "recruitment_end": "2026-10-10",
                    "recruitment_end_quote": "甲公司投递截止：2026年10月10日",
                    "link_url": "https://jobs.example.com/b",
                    "link_quote": "乙公司投递链接：https://jobs.example.com/b",
                    "events": [],
                },
            ],
            "unassigned_events": [],
            "candidate_profile": {},
        }
        result, kwargs = self._call_batch(text, parsed)
        self.assertEqual(len(result["jobs"]), 2)
        self.assertEqual(result["jobs"][0]["recruitment_end"], "2026-10-10")
        self.assertIsNone(result["jobs"][1]["recruitment_end"])
        self.assertEqual(result["jobs"][1]["evidence"]["recruitment_end"]["status"], "待确认")
        self.assertEqual(result["jobs"][0]["link_url"], "https://jobs.example.com/a")
        self.assertEqual(result["jobs"][1]["link_url"], "https://jobs.example.com/b")
        self.assertEqual(result["jobs"][0]["events"], [])
        self.assertEqual(len(result["unassigned_events"]), 1)
        self.assertIs(kwargs["text_format"], _IntakeBatch)
        parsed["jobs"][1]["link_url"] = "https://made-up.example.com/apply"
        result, _ = self._call_batch(text, parsed)
        self.assertEqual(result["jobs"][1]["link_url"], "")
        self.assertEqual(result["jobs"][1]["evidence"]["link_url"]["status"], "待确认")

    def _call_with_fake_response(self, text, parsed, reference_date=None):
        parsed = {
            "company_info": "",
            "jd": "",
            **parsed,
            "events": [
                {"feedback_score": None, **event}
                for event in parsed.get("events", [])
            ],
        }
        responses = _FakeResponses(_JobInfo.model_validate(parsed))
        fake_openai = types.SimpleNamespace(
            OpenAI=lambda **kwargs: types.SimpleNamespace(responses=responses)
        )
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(
                os.environ,
                {
                    "OPENAI_API_KEY": "test-key",
                    "OPENAI_MODEL": "gpt-4o-mini",
                    "JOB_AGENT_DB_PATH": str(Path(temporary) / "usage.db"),
                },
            ), patch.dict(sys.modules, {"openai": fake_openai}):
                result = extract_job_info(text, reference_date)
        return result, responses.kwargs

    def test_missing_api_key_is_actionable(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            with self.assertRaisesRegex(ExtractionError, "OPENAI_API_KEY"):
                extract_job_info("某公司招聘软件工程师")

    def test_candidate_resume_sections_are_evidence_grounded(self):
        text = (
            "张三。个人简介：数据分析方向。教育经历：示例大学统计学。"
            "实习经历：在零售公司完成销售分析。项目经历：用户增长分析项目。"
            "技能：Python、SQL。"
        )
        parsed = {
            "company": "",
            "role": "",
            "recruitment_start": None,
            "recruitment_end": None,
            "events": [],
            "candidate_profile": {
                "name": "张三",
                "summary": "个人简介：数据分析方向",
                "education": ["教育经历：示例大学统计学"],
                "experiences": ["不存在的工作经历"],
                "internships": ["实习经历：在零售公司完成销售分析"],
                "projects": ["项目经历：用户增长分析项目"],
                "skills": ["Python", "SQL", "Java"],
            },
        }
        result, _ = self._call_with_fake_response(text, parsed)
        profile = result["candidate_profile"]
        self.assertEqual(profile["name"], "张三")
        self.assertEqual(profile["experiences"], [])
        self.assertEqual(profile["skills"], ["Python", "SQL"])

    def test_verified_event_and_official_deadline(self):
        text = "星河科技招聘后端工程师。投递截止：2026年9月30日 18:00。"
        quote = "投递截止：2026年9月30日 18:00"
        parsed = {
            "company": "星河科技",
            "role": "后端工程师",
            "recruitment_start": None,
            "recruitment_end": "2026-09-30",
            "events": [{
                "event_type": "投递截止",
                "event_date": "2026-09-30",
                "details": "投递通道关闭",
                "deadline_at": "2026-09-30T18:00",
                "deadline_kind": "官方截止",
                "source": "虚构网址",
                "source_quote": quote,
            }],
        }
        result, kwargs = self._call_with_fake_response(text, parsed)
        self.assertEqual(result["events"][0]["event_date"], "2026-09-30")
        self.assertEqual(result["events"][0]["deadline_at"], "2026-09-30T18:00")
        self.assertEqual(result["events"][0]["source"], "输入文本")
        self.assertEqual(result["company"], "星河科技")
        self.assertEqual(kwargs["model"], "gpt-4o-mini")
        self.assertIs(kwargs["text_format"], _JobInfo)
        self.assertIs(kwargs["store"], False)

    def test_partial_and_unsupported_dates_are_not_invented(self):
        text = "星河科技招聘后端工程师。9月30日投递截止。面试下周五。"
        parsed = {
            "company": "星河科技",
            "role": "后端工程师",
            "recruitment_start": "2026-09-01",
            "recruitment_end": "2026-09-30",
            "events": [{
                "event_type": "投递截止",
                "event_date": "2026-09-30",
                "details": "投递截止",
                "deadline_at": "2026-09-30T18:00",
                "deadline_kind": "官方截止",
                "source": "输入文本",
                "source_quote": "9月30日投递截止",
            }, {
                "event_type": "面试邀请",
                "event_date": "2026-10-02",
                "details": "面试安排在下周五",
                "deadline_at": None,
                "deadline_kind": None,
                "source": "输入文本",
                "source_quote": "面试下周五",
            }],
        }
        result, _ = self._call_with_fake_response(text, parsed, "2026-09-27")
        self.assertIsNone(result["recruitment_start"])
        self.assertIsNone(result["recruitment_end"])
        self.assertIsNone(result["events"][0]["event_date"])
        self.assertIsNone(result["events"][0]["deadline_at"])
        self.assertIsNone(result["events"][0]["deadline_kind"])
        self.assertIsNone(result["events"][1]["event_date"])

    def test_date_must_appear_in_the_event_quote(self):
        text = "岗位发布于2026年9月1日。面试时间尚未确定。"
        parsed = {
            "company": "",
            "role": "",
            "recruitment_start": None,
            "recruitment_end": None,
            "events": [{
                "event_type": "面试邀请",
                "event_date": "2026-09-01",
                "details": "面试时间待定",
                "deadline_at": None,
                "deadline_kind": None,
                "source": "输入文本",
                "source_quote": "面试时间尚未确定",
            }],
        }
        result, _ = self._call_with_fake_response(text, parsed)
        self.assertIsNone(result["events"][0]["event_date"])

    def test_recruitment_start_event_is_supported(self):
        text = "招聘开始：2026年9月1日。"
        parsed = {
            "company": "",
            "role": "",
            "recruitment_start": "2026-09-01",
            "recruitment_end": None,
            "events": [{
                "event_type": "招聘开始",
                "event_date": "2026-09-01",
                "details": "招聘开始",
                "deadline_at": None,
                "deadline_kind": None,
                "source": "输入文本",
                "source_quote": "招聘开始：2026年9月1日",
            }],
        }
        result, _ = self._call_with_fake_response(text, parsed)
        self.assertEqual(result["recruitment_start"], "2026-09-01")
        self.assertEqual(result["events"][0]["event_type"], "招聘开始")

    def test_explicit_before_phrase_is_a_deadline(self):
        text = "请在2026年10月1日前确认面试时间。"
        parsed = {
            "company": "",
            "role": "",
            "recruitment_start": None,
            "recruitment_end": None,
            "events": [{
                "event_type": "面试邀请",
                "event_date": None,
                "details": "确认面试时间",
                "deadline_at": "2026-10-01",
                "deadline_kind": "官方截止",
                "source": "输入文本",
                "source_quote": "请在2026年10月1日前确认面试时间",
            }],
        }
        result, _ = self._call_with_fake_response(text, parsed)
        self.assertEqual(result["events"][0]["deadline_at"], "2026-10-01")
        self.assertEqual(result["events"][0]["deadline_kind"], "官方截止")

    def test_application_cutoff_populates_event_and_action_date(self):
        text = "投递截止：2026年9月30日。"
        parsed = {
            "company": "",
            "role": "",
            "recruitment_start": None,
            "recruitment_end": "2026-09-30",
            "events": [{
                "event_type": "投递截止",
                "event_date": "2026-09-30",
                "details": "投递截止",
                "deadline_at": None,
                "deadline_kind": None,
                "source": "输入文本",
                "source_quote": "投递截止：2026年9月30日",
            }],
        }
        result, _ = self._call_with_fake_response(text, parsed)
        event = result["events"][0]
        self.assertEqual(event["event_date"], "2026-09-30")
        self.assertEqual(event["deadline_at"], "2026-09-30")
        self.assertEqual(event["deadline_kind"], "官方截止")

    def test_quote_must_be_exact_source_substring(self):
        parsed = {
            "company": "",
            "role": "",
            "recruitment_start": None,
            "recruitment_end": None,
            "events": [{
                "event_type": "投递截止",
                "event_date": "2026-09-30",
                "details": "投递截止",
                "deadline_at": None,
                "deadline_kind": None,
                "source": "输入文本",
                "source_quote": "2026年9月30日投递截止",
            }],
        }
        with self.assertRaisesRegex(ExtractionError, "原文中核对"):
            self._call_with_fake_response("截止日为2026年9月30日。", parsed)

    def test_mixed_intake_separates_company_jd_and_progress(self):
        company_info = "星河科技是一家开发工业软件的公司。"
        jd = "后端工程师\n岗位职责：开发数据服务。\n任职要求：熟悉 Python。"
        quote = "我在2026年9月20日完成投递。"
        text = f"{company_info}\n{jd}\n{quote}"
        parsed = {
            "company": "星河科技",
            "role": "后端工程师",
            "company_info": company_info,
            "jd": jd,
            "recruitment_start": None,
            "recruitment_end": None,
            "events": [{
                "event_type": "已投递",
                "event_date": "2026-09-20",
                "details": "完成投递",
                "deadline_at": None,
                "deadline_kind": None,
                "source": "输入文本",
                "source_quote": quote,
                "feedback_score": None,
            }],
        }
        result, kwargs = self._call_with_fake_response(text, parsed)
        self.assertEqual(result["company_info"], company_info)
        self.assertEqual(result["jd"], jd)
        self.assertEqual(result["events"][0]["event_date"], "2026-09-20")
        system_prompt = kwargs["input"][0]["content"]
        self.assertIn("分拣", system_prompt)
        self.assertIn("不得执行", system_prompt)

    def test_unsupported_company_and_jd_are_discarded(self):
        result, _ = self._call_with_fake_response("星河科技招聘后端工程师。", {
            "company": "星河科技",
            "role": "后端工程师",
            "company_info": "行业领先的独角兽公司",
            "jd": "要求精通 Rust",
            "recruitment_start": None,
            "recruitment_end": None,
            "events": [],
        })
        self.assertEqual(result["company_info"], "")
        self.assertEqual(result["jd"], "")

    def test_numeric_interview_self_rating_requires_explicit_quote(self):
        quote = "2026年9月26日完成一面，我给这次面试打了4分。"
        text = f"{quote} 岗位要求 5 年经验。"
        base_event = {
            "event_type": "面试完成",
            "event_date": "2026-09-26",
            "details": "完成一面",
            "deadline_at": None,
            "deadline_kind": None,
            "source": "输入文本",
            "source_quote": quote,
            "feedback_score": 4,
        }
        parsed = {
            "company": "",
            "role": "",
            "recruitment_start": None,
            "recruitment_end": None,
            "events": [base_event],
        }
        result, _ = self._call_with_fake_response(text, parsed)
        self.assertEqual(result["events"][0]["feedback_score"], 4)

        parsed["events"] = [{**base_event, "source_quote": "2026年9月26日完成一面", "feedback_score": 4}]
        result, _ = self._call_with_fake_response(text, parsed)
        self.assertIsNone(result["events"][0]["feedback_score"])

        parsed["events"] = [{**base_event, "feedback_score": 5}]
        result, _ = self._call_with_fake_response(text, parsed)
        self.assertIsNone(result["events"][0]["feedback_score"])

    def test_adjectives_and_other_numbers_do_not_create_rating(self):
        quote = "2026年9月26日完成一面，感觉不错，这是第 4 轮面试。"
        parsed = {
            "company": "",
            "role": "",
            "recruitment_start": None,
            "recruitment_end": None,
            "events": [{
                "event_type": "面试完成",
                "event_date": "2026-09-26",
                "details": "完成面试",
                "deadline_at": None,
                "deadline_kind": None,
                "source": "输入文本",
                "source_quote": quote,
                "feedback_score": 4,
            }],
        }
        result, _ = self._call_with_fake_response(quote, parsed)
        self.assertIsNone(result["events"][0]["feedback_score"])


if __name__ == "__main__":
    unittest.main()
