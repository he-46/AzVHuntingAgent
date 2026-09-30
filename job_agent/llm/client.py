"""Single gateway for structured OpenAI calls."""

from __future__ import annotations

import hashlib
import json
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
    model = settings.openai_model
    request_hash = hashlib.sha256(
        json.dumps(
            {"operation": operation, "model": model, "messages": messages},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise LLMConfigurationError("缺少 openai 依赖") from exc
    # The character count is a conservative local estimate, not a provider token count.
    reserved_tokens = max(1, prompt_chars * 2) + max_output_tokens
    try:
        reservation_id = database.reserve_llm_call(
            operation=operation,
            model=model,
            request_hash=request_hash,
            reserved_tokens=reserved_tokens,
            since=since_utc,
            token_budget=settings.llm_daily_token_budget,
            call_budget=settings.llm_max_calls_per_day,
            duplicate_window_seconds=settings.llm_duplicate_window_seconds,
            db_path=settings.db_path,
        )
    except ValueError as exc:
        raise LLMConfigurationError(str(exc)) from exc
    try:
        response = OpenAI(api_key=resolved_api_key, timeout=timeout, max_retries=0).responses.parse(
            model=model,
            input=messages,
            text_format=schema,
            max_output_tokens=max_output_tokens,
            store=False,
        )
        if getattr(response, "status", "completed") != "completed":
            raise LLMRequestError("模型请求未完成")
        if response.output_parsed is None:
            raise LLMRequestError("模型没有返回结构化结果")
        parsed = schema.model_validate(response.output_parsed)
    except Exception as exc:
        database.mark_llm_call_failed(reservation_id, db_path=settings.db_path)
        if isinstance(exc, LLMRequestError):
            raise
        raise LLMRequestError("模型请求失败") from exc
    usage = getattr(response, "usage", None)
    if usage is not None:
        def usage_value(name: str) -> int:
            value = usage.get(name, 0) if isinstance(usage, dict) else getattr(usage, name, 0)
            return int(value or 0)

        input_tokens = usage_value("input_tokens")
        output_tokens = usage_value("output_tokens")
        total_tokens = usage_value("total_tokens") or input_tokens + output_tokens
        database.settle_llm_call(
            reservation_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            db_path=settings.db_path,
        )
    return parsed


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
