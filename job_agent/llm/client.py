"""Single gateway for structured OpenAI calls."""

from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, time, timezone
from typing import TypeVar

from pydantic import BaseModel

import database
from job_agent.config import Settings


SchemaT = TypeVar("SchemaT", bound=BaseModel)


class LLMConfigurationError(RuntimeError):
    pass


class LLMRequestError(RuntimeError):
    pass


def parse_structured(
    *,
    messages: list[dict[str, str]],
    schema: type[SchemaT],
    operation: str,
    max_output_tokens: int,
    timeout: float = 30.0,
    api_key: str | None = None,
) -> SchemaT:
    """Call the configured model and return a validated structured response."""
    settings = Settings.from_env()
    resolved_api_key = (api_key or os.environ.get("OPENAI_API_KEY", "")).strip()
    if not resolved_api_key:
        raise LLMConfigurationError("未配置 OPENAI_API_KEY")
    prompt_chars = sum(len(message.get("content") or "") for message in messages)
    if prompt_chars > settings.llm_max_input_chars:
        raise LLMConfigurationError(
            f"本次输入共 {prompt_chars} 个字符，超过单次上限 {settings.llm_max_input_chars}"
        )
    if max_output_tokens <= 0:
        raise LLMConfigurationError("max_output_tokens 必须是正整数")

    local_midnight = datetime.combine(settings.today(), time.min, tzinfo=settings.timezone)
    since_utc = local_midnight.astimezone(timezone.utc)
    usage_today = database.llm_usage_summary(since=since_utc, db_path=settings.db_path)
    estimated_input_tokens = max(1, math.ceil(prompt_chars / 2))
    if usage_today["calls"] >= settings.llm_max_calls_per_day:
        raise LLMConfigurationError(
            f"今天已经调用 {usage_today['calls']} 次，达到每日上限 {settings.llm_max_calls_per_day} 次"
        )
    estimated_total = usage_today["total_tokens"] + estimated_input_tokens + max_output_tokens
    if estimated_total > settings.llm_daily_token_budget:
        remaining = max(0, settings.llm_daily_token_budget - usage_today["total_tokens"])
        raise LLMConfigurationError(
            f"本次请求预计需要最多 {estimated_input_tokens + max_output_tokens} tokens，"
            f"今日预算仅剩 {remaining} tokens"
        )

    model = settings.openai_model
    request_hash = hashlib.sha256(
        json.dumps(
            {"operation": operation, "model": model, "messages": messages},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    if database.has_recent_llm_request(
        request_hash,
        seconds=settings.llm_duplicate_window_seconds,
        db_path=settings.db_path,
    ):
        raise LLMConfigurationError(
            f"相同请求刚刚已经成功执行，请等待 {settings.llm_duplicate_window_seconds} 秒后再试"
        )
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise LLMConfigurationError("缺少 openai 依赖") from exc
    try:
        response = OpenAI(api_key=resolved_api_key, timeout=timeout).responses.parse(
            model=model,
            input=messages,
            text_format=schema,
            max_output_tokens=max_output_tokens,
            store=False,
        )
    except Exception as exc:
        raise LLMRequestError("模型请求失败") from exc
    if getattr(response, "status", "completed") != "completed":
        raise LLMRequestError("模型请求未完成")
    if response.output_parsed is None:
        raise LLMRequestError("模型没有返回结构化结果")
    usage = getattr(response, "usage", None)
    if usage is not None:
        def usage_value(name: str) -> int:
            value = usage.get(name, 0) if isinstance(usage, dict) else getattr(usage, name, 0)
            return int(value or 0)

        input_tokens = usage_value("input_tokens")
        output_tokens = usage_value("output_tokens")
        total_tokens = usage_value("total_tokens") or input_tokens + output_tokens
        database.record_llm_usage(
            operation=operation,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            request_hash=request_hash,
            db_path=settings.db_path,
        )
    return schema.model_validate(response.output_parsed)


def daily_usage_snapshot() -> dict[str, int]:
    """Return today's persisted usage together with configured limits."""
    settings = Settings.from_env()
    local_midnight = datetime.combine(settings.today(), time.min, tzinfo=settings.timezone)
    result = database.llm_usage_summary(
        since=local_midnight.astimezone(timezone.utc),
        db_path=settings.db_path,
    )
    result.update(
        {
            "token_budget": settings.llm_daily_token_budget,
            "call_budget": settings.llm_max_calls_per_day,
            "max_input_chars": settings.llm_max_input_chars,
        }
    )
    return result
