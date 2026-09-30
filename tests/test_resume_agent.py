"""Tests for evidence-grounded resume generation."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from resume_agent import ResumeGenerationError, generate_resume_draft


def _fake_openai(result: dict):
    response = SimpleNamespace(status="completed", output_parsed=result)
    responses = SimpleNamespace(parse=lambda **kwargs: response)
    client = SimpleNamespace(responses=responses)
    return SimpleNamespace(OpenAI=lambda **kwargs: client)


class ResumeAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        environment = patch.dict(
            os.environ, {"JOB_AGENT_DB_PATH": str(Path(temporary.name) / "usage.db")}
        )
        environment.start()
        self.addCleanup(environment.stop)

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
            "evidence_map": [{"claim": "Python 3 年", "source_quote": "Python 3 年经验"}],
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

    def test_rejects_claim_without_source_quote(self) -> None:
        result = {
            "tailored_resume_markdown": "# 张三\n- 熟悉 Python",
            "match_analysis": [],
            "missing_evidence": [],
            "interview_focus": [],
            "evidence_map": [{"claim": "熟悉 Python", "source_quote": "虚构的原文"}],
        }
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch.dict(
            sys.modules, {"openai": _fake_openai(result)}
        ):
            with self.assertRaisesRegex(ResumeGenerationError, "原文"):
                generate_resume_draft(
                    source_resume="张三，会 Python",
                    company="示例公司", role="开发", jd="需要 Python",
                )

    def test_blocks_numbers_not_found_in_source_resume(self) -> None:
        result = {
            "tailored_resume_markdown": "# 简历\n提升效率 50%",
            "match_analysis": [],
            "missing_evidence": [],
            "interview_focus": [],
            "evidence_map": [],
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
