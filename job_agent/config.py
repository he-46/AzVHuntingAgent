"""Central application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是正整数") from exc
    if value <= 0:
        raise ValueError(f"{name} 必须是正整数")
    return value


@dataclass(frozen=True)
class Settings:
    db_path: str
    timezone: ZoneInfo
    openai_model: str
    llm_max_input_chars: int
    llm_daily_token_budget: int
    llm_max_calls_per_day: int
    llm_duplicate_window_seconds: int

    @classmethod
    def from_env(cls) -> "Settings":
        project_root = Path(__file__).resolve().parents[1]
        return cls(
            db_path=os.environ.get("JOB_AGENT_DB_PATH")
            or str(project_root / "data" / "applications.db"),
            timezone=ZoneInfo(os.environ.get("JOB_AGENT_TIMEZONE", "Asia/Shanghai")),
            openai_model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip()
            or "gpt-4o-mini",
            llm_max_input_chars=_positive_int("LLM_MAX_INPUT_CHARS", 40_000),
            llm_daily_token_budget=_positive_int("LLM_DAILY_TOKEN_BUDGET", 100_000),
            llm_max_calls_per_day=_positive_int("LLM_MAX_CALLS_PER_DAY", 30),
            llm_duplicate_window_seconds=_positive_int("LLM_DUPLICATE_WINDOW_SECONDS", 60),
        )

    def now(self) -> datetime:
        return datetime.now(self.timezone)

    def today(self) -> date:
        return self.now().date()


settings = Settings.from_env()
