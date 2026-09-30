"""Tests for centralized LLM token and call budgets."""

import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import BaseModel

import database
from job_agent.llm.client import (
    LLMConfigurationError,
    LLMRequestError,
    daily_usage_snapshot,
    parse_structured,
)


class _Answer(BaseModel):
    value: str


def _try_reserve(callback, index: int) -> int | str:
    try:
        return callback(index)
    except ValueError as exc:
        return str(exc)


class LLMBudgetTests(unittest.TestCase):
    def test_failed_request_keeps_conservative_reservation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            db_path = str(Path(temporary) / "usage.db")
            def fail(**kwargs):
                raise RuntimeError("network timeout")
            fake_openai = types.SimpleNamespace(
                OpenAI=lambda **kwargs: types.SimpleNamespace(
                    responses=types.SimpleNamespace(parse=fail)
                )
            )
            with patch.dict(os.environ, {
                "JOB_AGENT_DB_PATH": db_path,
                "OPENAI_API_KEY": "test-key",
                "LLM_DAILY_TOKEN_BUDGET": "200",
            }), patch.dict(sys.modules, {"openai": fake_openai}):
                with self.assertRaises(LLMRequestError):
                    parse_structured(
                        messages=[{"role": "user", "content": "hello"}],
                        schema=_Answer, operation="failed-test", max_output_tokens=100,
                    )
                usage = daily_usage_snapshot()
                self.assertEqual(usage["calls"], 1)
                self.assertEqual(usage["reserved_calls"], 1)
                self.assertEqual(usage["reserved_tokens"], 110)

    def test_concurrent_reservations_cannot_exceed_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            db_path = str(Path(temporary) / "usage.db")
            def reserve(index: int) -> int:
                return database.reserve_llm_call(
                    operation="test", model="test", request_hash=f"hash-{index}",
                    reserved_tokens=80, since=datetime.now(timezone.utc).replace(
                        hour=0, minute=0, second=0, microsecond=0
                    ),
                    token_budget=100, call_budget=2,
                    duplicate_window_seconds=60, db_path=db_path,
                )
            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(executor.map(lambda index: _try_reserve(reserve, index), range(2)))
            self.assertEqual(sum(isinstance(value, int) for value in outcomes), 1)
            self.assertEqual(database.llm_usage_summary(
                since=datetime.now(timezone.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0
                ), db_path=db_path,
            )["total_tokens"], 80)

    def test_explicit_api_key_works_without_environment_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            db_path = str(Path(temporary) / "usage.db")
            captured: dict = {}
            response = types.SimpleNamespace(
                status="completed",
                output_parsed={"value": "ok"},
                usage=None,
            )

            def openai_factory(**kwargs):
                captured.update(kwargs)
                return types.SimpleNamespace(
                    responses=types.SimpleNamespace(parse=lambda **kwargs: response)
                )

            fake_openai = types.SimpleNamespace(OpenAI=openai_factory)
            with patch.dict(
                os.environ,
                {"JOB_AGENT_DB_PATH": db_path, "OPENAI_API_KEY": ""},
                clear=True,
            ), patch.dict(sys.modules, {"openai": fake_openai}):
                result = parse_structured(
                    messages=[{"role": "user", "content": "hello"}],
                    schema=_Answer,
                    operation="session-key-test",
                    max_output_tokens=100,
                    api_key="web-session-key",
                )

            self.assertEqual(result.value, "ok")
            self.assertEqual(captured["api_key"], "web-session-key")

    def test_records_usage_and_blocks_immediate_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            db_path = str(Path(temporary) / "usage.db")
            response = types.SimpleNamespace(
                status="completed",
                output_parsed={"value": "ok"},
                usage=types.SimpleNamespace(input_tokens=20, output_tokens=10, total_tokens=30),
            )
            fake_openai = types.SimpleNamespace(
                OpenAI=lambda **kwargs: types.SimpleNamespace(
                    responses=types.SimpleNamespace(parse=lambda **kwargs: response)
                )
            )
            environment = {
                "OPENAI_API_KEY": "test-key",
                "JOB_AGENT_DB_PATH": db_path,
                "LLM_DAILY_TOKEN_BUDGET": "1000",
                "LLM_MAX_CALLS_PER_DAY": "3",
                "LLM_DUPLICATE_WINDOW_SECONDS": "60",
            }
            with patch.dict(os.environ, environment), patch.dict(sys.modules, {"openai": fake_openai}):
                result = parse_structured(
                    messages=[{"role": "user", "content": "hello"}],
                    schema=_Answer,
                    operation="test",
                    max_output_tokens=100,
                )
                self.assertEqual(result.value, "ok")
                self.assertEqual(daily_usage_snapshot()["total_tokens"], 30)
                with self.assertRaisesRegex(LLMConfigurationError, "相同请求"):
                    parse_structured(
                        messages=[{"role": "user", "content": "hello"}],
                        schema=_Answer,
                        operation="test",
                        max_output_tokens=100,
                    )

    def test_rejects_request_that_exceeds_daily_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            db_path = str(Path(temporary) / "usage.db")
            database.record_llm_usage(
                operation="prior",
                model="test",
                input_tokens=400,
                output_tokens=450,
                total_tokens=850,
                request_hash="prior",
                db_path=db_path,
            )
            with patch.dict(
                os.environ,
                {
                    "OPENAI_API_KEY": "test-key",
                    "JOB_AGENT_DB_PATH": db_path,
                    "LLM_DAILY_TOKEN_BUDGET": "900",
                },
            ):
                with self.assertRaisesRegex(LLMConfigurationError, "今日预算"):
                    parse_structured(
                        messages=[{"role": "user", "content": "new request"}],
                        schema=_Answer,
                        operation="test",
                        max_output_tokens=100,
                    )


if __name__ == "__main__":
    unittest.main()
