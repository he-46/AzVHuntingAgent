"""Extract reviewable recruitment facts from pasted text with OpenAI.

This module has no side effects at import time. The API is called only from
``extract_job_info`` and only when ``OPENAI_API_KEY`` is configured.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from job_agent.llm.client import (
    LLMConfigurationError,
    LLMRequestError,
    parse_structured,
)
from job_agent.skills.intake import SYSTEM_PROMPT as INTAKE_SYSTEM_PROMPT
from job_agent.skills.registry import get_skill


EventType = Literal[
    "岗位发布",
    "招聘开始",
    "投递截止",
    "已投递",
    "测评邀请",
    "测评完成",
    "面试邀请",
    "面试完成",
    "HR反馈",
    "录取通知",
    "拒绝",
    "其他",
]

INTAKE_SKILL = get_skill("intake_extraction")


class ExtractionError(RuntimeError):
    """A user-facing extraction or verification failure."""


class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: EventType
    event_date: str | None
    details: str
    deadline_at: str | None
    deadline_kind: Literal["官方截止"] | None
    source: str
    source_quote: str
    feedback_score: int | None


class _CandidateProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    summary: str = ""
    education: list[str] = Field(default_factory=list)
    experiences: list[str] = Field(default_factory=list)
    internships: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


class _JobInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: str
    role: str
    company_info: str
    jd: str
    recruitment_start: str | None
    recruitment_end: str | None
    events: list[_Event]
    candidate_profile: _CandidateProfile = Field(default_factory=_CandidateProfile)


_FULL_DATE = re.compile(
    r"(?<!\d)(?P<year>19\d{2}|20\d{2})\s*(?:年|[-/.])\s*"
    r"(?P<month>0?[1-9]|1[0-2])\s*(?:月|[-/.])\s*"
    r"(?P<day>0?[1-9]|[12]\d|3[01])\s*(?:日|号)?(?!\d)"
)
_CLOCK_TIME = re.compile(r"(?<!\d)([01]?\d|2[0-3])[:：]([0-5]\d)(?:[:：]([0-5]\d))?(?!\d)")
_CHINESE_TIME = re.compile(r"(?<!\d)([01]?\d|2[0-3])\s*(?:点|时)(?:\s*([0-5]?\d)\s*分?)?(?!\d)")
_OFFICIAL_DEADLINE = re.compile(
    r"截止|最迟|结束报名|报名结束|提交前|投递前|"
    r"(?:前|之前|以前)(?:提交|确认|投递|报名|完成|发送|回复)|"
    r"(?:^|\W)deadline(?:\W|$)|(?:^|\W)due(?:\W|$)",
    re.IGNORECASE,
)
_SELF_RATING = re.compile(
    r"(?:自评|自我评分|自我打分|给自己(?:的面试表现)?打分|"
    r"我给(?:自己|这次面试(?:表现)?)(?:打了?|评了?)?分?|"
    r"我(?:觉得|认为|感觉)(?:这次)?面试表现|"
    r"(?:I\s+rate|I\s+rated|I\s+would\s+rate)\s+(?:my\s+)?(?:interview\s+)?performance)"
    r"\s*(?:[:：=]|为|是|打了?|评分为)?\s*"
    r"(?P<score>[1-5])(?:\s*(?:分(?:\s*[/／]\s*5\s*分?)?|[/／]\s*5\s*分?))?"
    r"(?![\d年月日轮次.．%％/／]|分\s*[/／])",
    re.IGNORECASE,
)


def _explicit_dates(text: str) -> set[str]:
    """Return valid, fully specified dates literally represented in a quote."""
    dates: set[str] = set()
    for match in _FULL_DATE.finditer(text):
        try:
            dates.add(
                date(
                    int(match.group("year")),
                    int(match.group("month")),
                    int(match.group("day")),
                ).isoformat()
            )
        except ValueError:
            continue
    return dates


def _verified_date(value: str | None, quote: str) -> str | None:
    if value is None:
        return None
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    # Reject non-canonical formats, including compact dates accepted by Python.
    if value != parsed.isoformat():
        return None
    return value if value in _explicit_dates(quote) else None


def _explicit_times(quote: str) -> set[tuple[int, int, int]]:
    times = {
        (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))
        for match in _CLOCK_TIME.finditer(quote)
    }
    times.update(
        (int(match.group(1)), int(match.group(2) or 0), 0)
        for match in _CHINESE_TIME.finditer(quote)
    )
    return times


def _verified_deadline(value: str | None, quote: str) -> str | None:
    if value is None or not _OFFICIAL_DEADLINE.search(quote):
        return None
    verified_day = _verified_date(value, quote)
    if verified_day is not None:
        return verified_day
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is not None or parsed.date().isoformat() not in _explicit_dates(quote):
        return None
    if (parsed.hour, parsed.minute, parsed.second) not in _explicit_times(quote):
        return None
    return parsed.isoformat(timespec="seconds" if parsed.second else "minutes")


def _verified_feedback_score(value: int | None, quote: str, event_type: str) -> int | None:
    if event_type != "面试完成" or not isinstance(value, int) or isinstance(value, bool):
        return None
    if not 1 <= value <= 5:
        return None
    explicit_scores = {int(match.group("score")) for match in _SELF_RATING.finditer(quote)}
    return value if value in explicit_scores else None


def _verify_result(parsed: _JobInfo, text: str) -> dict:
    result = parsed.model_dump()
    for field in ("company", "role"):
        value = result[field].strip()
        result[field] = value if value and value in text else ""

    for field in ("company_info", "jd"):
        value = result[field].strip()
        # These fields are source excerpts, so unsupported paraphrases or
        # hallucinated content must never be saved as extracted facts.
        result[field] = value if value and value in text else ""

    for field in ("recruitment_start", "recruitment_end"):
        result[field] = _verified_date(result[field], text)

    profile = result["candidate_profile"]
    for field in ("name", "summary"):
        value = profile[field].strip()
        profile[field] = value if value and value in text else ""
    for field in ("education", "experiences", "internships", "projects", "skills"):
        verified: list[str] = []
        for item in profile[field]:
            value = item.strip()
            if value and value in text and value not in verified:
                verified.append(value)
        profile[field] = verified

    for event in result["events"]:
        quote = event["source_quote"].strip()
        if not quote or quote not in text:
            raise ExtractionError("AI 返回的事件引用无法在原文中核对，请重试或手动录入。")
        event["source_quote"] = quote
        event["source"] = "输入文本"
        event["details"] = event["details"].strip()
        event["event_date"] = _verified_date(event["event_date"], quote)
        event["deadline_at"] = _verified_deadline(event["deadline_at"], quote)
        if event["event_type"] == "投递截止" and _OFFICIAL_DEADLINE.search(quote):
            # A posted application cutoff is both the timeline event and the
            # actionable deadline, even if the model omitted one field.
            if event["deadline_at"] and not event["event_date"]:
                event["event_date"] = event["deadline_at"][:10]
            elif event["event_date"] and not event["deadline_at"]:
                event["deadline_at"] = event["event_date"]
        event["deadline_kind"] = "官方截止" if event["deadline_at"] else None
        event["feedback_score"] = _verified_feedback_score(
            event["feedback_score"], quote, event["event_type"]
        )
    return result


def extract_job_info(
    text: str,
    reference_date: str | None = None,
    *,
    api_key: str | None = None,
) -> dict:
    """Extract job information and evidence-backed timeline events.

    ``reference_date`` is context only and never supplies a missing year or day.
    All returned dates are confirmed against literal, full dates in the pasted
    text. A call requires ``OPENAI_API_KEY``; ``OPENAI_MODEL`` may override the
    default ``gpt-4o-mini`` model.
    """
    if not isinstance(text, str) or not text.strip():
        raise ExtractionError("请先粘贴 JD、招聘通知或进度记录。")
    source_limit = INTAKE_SKILL.input_limit("source_text")
    if len(text) > source_limit.max_chars:
        raise ExtractionError(
            f"{source_limit.label}共 {len(text)} 个字符，"
            f"信息分拣单次最多支持 {source_limit.max_chars} 个字符。"
        )
    if reference_date is not None:
        try:
            if reference_date != date.fromisoformat(reference_date).isoformat():
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise ExtractionError("参考日期请使用 YYYY-MM-DD 格式。") from exc

    try:
        parsed = parse_structured(
            messages=[
                {"role": "system", "content": INTAKE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"reference_date（仅供上下文理解，不用来补全日期）：{reference_date or '未提供'}\n"
                        f"以下是待提取的原文：\n{text}"
                    ),
                },
            ],
            schema=_JobInfo,
            operation=INTAKE_SKILL.operation,
            max_output_tokens=INTAKE_SKILL.max_output_tokens,
            timeout=INTAKE_SKILL.timeout_seconds,
            api_key=api_key,
        )
    except LLMConfigurationError as exc:
        raise ExtractionError(f"{exc}；设置后可使用 AI 提取，或先手动录入时间线。") from exc
    except LLMRequestError as exc:
        raise ExtractionError("AI 提取请求失败，请检查 API Key、网络及模型权限后重试。") from exc
    try:
        return _verify_result(parsed, text)
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError("AI 返回的数据格式不正确，请重试或手动录入。") from exc
