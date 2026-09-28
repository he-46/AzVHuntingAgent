"""Tests for evidence-grounded resume generation."""

from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from resume_agent import ResumeGenerationError, generate_resume_draft


def _fake_openai(result: dict):
    response = SimpleNamespace(status="completed", output_parsed=result)
    responses = SimpleNamespace(parse=lambda **kwargs: response)
    client = SimpleNamespace(responses=responses)
    return SimpleNamespace(OpenAI=lambda **kwargs: client)


class ResumeAgentTests(unittest.TestCase):
    def test_missing_key_is_actionable(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ResumeGenerationError, "OPENAI_API_KEY"):
                generate_resume_draft(
                    source_resume="会 Python",
                    company="示例公司",
                    role="开发",
                    jd="需要 Python",
                )

    def test_generates_structured_review_draft(self) -> None:
        result = {
            "tailored_resume_markdown": "# 张三\n\n## 技能\n- Python 3 年",
            "match_analysis": ["Python 与岗位匹配"],
            "missing_evidence": ["没有云平台经历"],
            "interview_focus": ["准备 Python 项目细节"],
        }
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch.dict(
            sys.modules, {"openai": _fake_openai(result)}
        ):
            draft = generate_resume_draft(
                source_resume="张三，Python 3 年经验",
                company="示例公司",
                role="开发",
                jd="需要 Python 和云平台经验",
            )
        self.assertIn("Python 3 年", draft["tailored_resume_markdown"])
        self.assertEqual(draft["missing_evidence"], ["没有云平台经历"])

    def test_blocks_numbers_not_found_in_source_resume(self) -> None:
        result = {
            "tailored_resume_markdown": "# 简历\n提升效率 50%",
            "match_analysis": [],
            "missing_evidence": [],
            "interview_focus": [],
        }
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch.dict(
            sys.modules, {"openai": _fake_openai(result)}
        ):
            with self.assertRaisesRegex(ResumeGenerationError, "50%"):
                generate_resume_draft(
                    source_resume="负责优化业务流程",
                    company="示例公司",
                    role="开发",
                    jd="提升系统效率",
                )


if __name__ == "__main__":
    unittest.main()
