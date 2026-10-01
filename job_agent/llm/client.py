"""Single gateway for structured OpenAI calls."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import TypeVar
from urllib.parse import urlparse

from pydantic import BaseModel

import database
from job_agent.config import Settings


SchemaT = TypeVar("SchemaT", bound=BaseModel)


class LLMConfigurationError(RuntimeError):
    pass


class LLMRequestError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMRuntimeConfig:
    api_key: str | None = None
    model: str | None = None
    api_style: str = "responses"
    base_url: str | None = None


def _validated_base_url(value: str | None) -> str:
    url = (value or "").strip().rstrip("/")
    parsed = urlparse(url)
    local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        not (parsed.scheme == "https" or local_http)
        or not parsed.netloc or parsed.username or parsed.password
        or parsed.query or parsed.fragment
    ):
        raise LLMConfigurationError(
            "兼容接口地址须为 HTTPS URL；本机 localhost 可使用 HTTP，且地址不能含账号、查询参数或片段"
        )
    return url


def parse_structured(
    *,
    messages: list[dict[str, str]],
    schema: type[SchemaT],
    operation: str,
    max_output_tokens: int,
    timeout: float = 30.0,
    api_key: str | None = None,
    llm_config: LLMRuntimeConfig | None = None,
) -> SchemaT:
    """Call the configured model and return a validated structured response."""
    settings = Settings.from_env()
    config = llm_config or LLMRuntimeConfig(api_key=api_key)
    if config.api_style not in {"responses", "chat_completions"}:
        raise LLMConfigurationError("不支持的模型接口类型")
    fallback_key = os.environ.get("OPENAI_API_KEY", "") if config.api_style == "responses" else ""
    resolved_api_key = (config.api_key or fallback_key).strip()
    if not resolved_api_key:
        message = (
            "未配置 OPENAI_API_KEY" if config.api_style == "responses"
            else "请在 AI 服务配置中填写兼容接口的 API Key"
        )
        raise LLMConfigurationError(message)
    model = (settings.openai_model if config.model is None else config.model).strip()
    if not model:
        raise LLMConfigurationError("请填写模型 ID")
    base_url = _validated_base_url(config.base_url) if config.api_style == "chat_completions" else None
    request_messages = messages
    if config.api_style == "chat_completions":
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        request_messages = [
            {"role": "system", "content": "只返回符合以下 JSON Schema 的 JSON 对象，不要添加 Markdown 或说明。\n" + schema_json},
            *messages,
        ]
    prompt_chars = sum(len(message.get("content") or "") for message in request_messages)
    if prompt_chars > settings.llm_max_input_chars:
        raise LLMConfigurationError(
            f"本次输入共 {prompt_chars} 个字符，超过单次上限 {settings.llm_max_input_chars}"
        )
    if max_output_tokens <= 0:
        raise LLMConfigurationError("max_output_tokens 必须是正整数")

    local_midnight = datetime.combine(settings.today(), time.min, tzinfo=settings.timezone)
    since_utc = local_midnight.astimezone(timezone.utc)
    request_hash = hashlib.sha256(
        json.dumps(
            {
                "operation": operation, "model": model, "api_style": config.api_style,
                "base_url": base_url, "messages": request_messages,
            },
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
        client_kwargs = {"api_key": resolved_api_key, "timeout": timeout, "max_retries": 0}
        if base_url:
            client_kwargs["base_url"] = base_url
        client = OpenAI(**client_kwargs)
        if config.api_style == "responses":
            response = client.responses.parse(
                model=model, input=request_messages, text_format=schema,
                max_output_tokens=max_output_tokens, store=False,
            )
            if getattr(response, "status", "completed") != "completed":
                raise LLMRequestError("模型请求未完成")
            if response.output_parsed is None:
                raise LLMRequestError("模型没有返回结构化结果")
            parsed = schema.model_validate(response.output_parsed)
        else:
            response = client.chat.completions.create(
                model=model, messages=request_messages,
                response_format={"type": "json_object"}, max_tokens=max_output_tokens,
            )
            if not response.choices or not response.choices[0].message.content:
                raise LLMRequestError("模型没有返回 JSON 结果")
            if getattr(response.choices[0], "finish_reason", None) == "length":
                raise LLMRequestError("模型输出达到上限，结果不完整")
            parsed = schema.model_validate(json.loads(response.choices[0].message.content))
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

        input_tokens = usage_value("input_tokens") or usage_value("prompt_tokens")
        output_tokens = usage_value("output_tokens") or usage_value("completion_tokens")
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
