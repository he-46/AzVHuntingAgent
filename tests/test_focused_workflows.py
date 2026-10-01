"""Focused extraction and bounded resume matching boundaries."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from extractor import (
    ExtractionError, _CandidateProfile, _JobIntakeBatch,
    extract_candidate_profile, extract_job_intake,
)
from job_agent.services.resume_matching import (
    MatchingError, _Recommendations, recommend_profile_items,
)


class FocusedWorkflowTests(unittest.TestCase):
    def test_candidate_analysis_uses_small_schema_and_literal_facts(self) -> None:
        source = "张三掌握 Python；JD 要求 Java。"
        parsed = _CandidateProfile(name="张三", skills=["Python", "SQL", "Java"])
        with patch("extractor.parse_structured", return_value=parsed) as mocked:
            result = extract_candidate_profile(source)
        self.assertEqual(mocked.call_args.kwargs["schema"], _CandidateProfile)
        self.assertEqual(mocked.call_args.kwargs["operation"], "candidate_extraction")
        self.assertEqual(result["skills"], ["Python"])

    def test_job_analysis_does_not_request_or_return_candidate_profile(self) -> None:
        with patch("extractor.parse_structured", return_value=_JobIntakeBatch(
            jobs=[], unassigned_events=[],
        )) as mocked:
            result = extract_job_intake("甲公司招聘数据分析师")
        self.assertEqual(mocked.call_args.kwargs["schema"], _JobIntakeBatch)
        self.assertEqual(mocked.call_args.kwargs["operation"], "job_extraction")
        self.assertEqual(result, {"jobs": [], "unassigned_events": []})

    def test_ai_material_matching_rejects_unbacked_or_unknown_rows(self) -> None:
        parsed = _Recommendations.model_validate({
            "skills": [
                {"index": 0, "jd_quote": "Python"},
                {"index": 1, "jd_quote": "不存在的要求"},
                {"index": 99, "jd_quote": "Python"},
            ],
            "projects": [{"index": 0, "jd_quote": "用户增长"}],
        })
        with patch("job_agent.services.resume_matching.parse_structured", return_value=parsed) as mocked:
            result = recommend_profile_items(
                "需要 Python，负责用户增长。", ["Python", "SQL"], ["用户增长项目"],
            )
        self.assertEqual(mocked.call_args.kwargs["operation"], "resume_material_matching")
        self.assertEqual(result["skills"], [{"item": "Python", "jd_quote": "Python"}])
        self.assertEqual(result["projects"], [{"item": "用户增长项目", "jd_quote": "用户增长"}])

    def test_matching_needs_jd_before_any_model_call(self) -> None:
        with patch("job_agent.services.resume_matching.parse_structured") as mocked:
            with self.assertRaisesRegex(MatchingError, "JD"):
                recommend_profile_items("", ["Python"], [])
        mocked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
