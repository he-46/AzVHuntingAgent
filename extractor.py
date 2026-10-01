"""Extract reviewable recruitment facts from pasted text with the selected model.

This module has no side effects at import time. The API is called only from
``extract_job_info`` or ``extract_intake`` and only when credentials are configured.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from job_agent.links import validate_link_url

from job_agent.llm.client import (
    LLMRuntimeConfig,
    LLMConfigurationError,
    LLMRequestError,
    parse_structured,
)
from job_agent.skills.intake import (
    BATCH_SYSTEM_PROMPT,
    SYSTEM_PROMPT as INTAKE_SYSTEM_PROMPT,
)
from job_agent.skills.candidate import SYSTEM_PROMPT as CANDIDATE_SYSTEM_PROMPT
from job_agent.skills.job import SYSTEM_PROMPT as JOB_SYSTEM_PROMPT
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
CANDIDATE_SKILL = get_skill("candidate_extraction")
JOB_SKILL = get_skill("job_extraction")


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
    contact: str = ""
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


class _JobDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: str
    company_quote: str
    role: str
    role_quote: str
    company_info: str
    jd: str
    recruitment_start: str | None
    recruitment_start_quote: str
    recruitment_end: str | None
    recruitment_end_quote: str
    link_url: str
    link_quote: str
    events: list[_Event]


class _IntakeBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobs: list[_JobDraft]
    unassigned_events: list[_Event]
    candidate_profile: _CandidateProfile


class _JobIntakeBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobs: list[_JobDraft]
    unassigned_events: list[_Event]


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

    profile = _verify_candidate_fields(result["candidate_profile"], text)
    result["candidate_profile"] = profile

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


def _verify_candidate_fields(profile: dict, text: str, *, exclude_job_segments: bool = False) -> dict:
    """Keep only literal resume excerpts, including individual skill names."""
    if exclude_job_segments:
        # A pasted JD must not turn its required skills into candidate facts.
        text = "\n".join(re.split(
            r"(?:JD|岗位要求|任职要求|招聘要求|岗位职责)\s*[：:]?", line,
            maxsplit=1, flags=re.IGNORECASE,
        )[0] for line in text.splitlines())
    for field in ("name", "contact", "summary"):
        value = profile[field].strip()
        profile[field] = value if value and value in text else ""
    for field in ("education", "experiences", "internships", "projects", "skills"):
        verified: list[str] = []
        for item in profile[field]:
            value = item.strip()
            if value and value in text and value not in verified:
                verified.append(value)
        profile[field] = verified
    return profile


def extract_candidate_profile(
    text: str,
    *,
    api_key: str | None = None,
    llm_config: LLMRuntimeConfig | None = None,
) -> dict:
    """Analyze one resume without paying for the multi-job output schema."""
    if not isinstance(text, str) or not text.strip():
        raise ExtractionError("请先粘贴简历或导入文件。")
    maximum = CANDIDATE_SKILL.input_limit("source_text").max_chars
    if len(text) > maximum:
        raise ExtractionError(f"简历原文单次最多支持 {maximum} 个字符。")
    try:
        parsed = parse_structured(
            messages=[
                {"role": "system", "content": CANDIDATE_SYSTEM_PROMPT},
                {"role": "user", "content": f"简历原文：\n{text}"},
            ],
            schema=_CandidateProfile,
            operation=CANDIDATE_SKILL.operation,
            max_output_tokens=CANDIDATE_SKILL.max_output_tokens,
            timeout=CANDIDATE_SKILL.timeout_seconds,
            api_key=api_key,
            llm_config=llm_config,
        )
    except LLMConfigurationError as exc:
        raise ExtractionError(f"{exc}；也可手动填写求职者资料。") from exc
    except LLMRequestError as exc:
        raise ExtractionError("AI 分析简历失败，请检查模型配置及 JSON mode 支持后重试。") from exc
    return _verify_candidate_fields(parsed.model_dump(), text, exclude_job_segments=True)


def extract_job_info(
    text: str,
    reference_date: str | None = None,
    *,
    api_key: str | None = None,
    llm_config: LLMRuntimeConfig | None = None,
) -> dict:
    """Extract job information and evidence-backed timeline events.

    ``reference_date`` is context only and never supplies a missing year or day.
    All returned dates are confirmed against literal, full dates in the pasted
    text. The selected service needs its own API key.
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
            llm_config=llm_config,
        )
    except LLMConfigurationError as exc:
        raise ExtractionError(f"{exc}；设置后可使用 AI 提取，或先手动录入时间线。") from exc
    except LLMRequestError as exc:
        raise ExtractionError("AI 提取请求失败，请检查 API Key、模型 ID、接口地址及 JSON mode 支持后重试。") from exc
    try:
        return _verify_result(parsed, text)
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError("AI 返回的数据格式不正确，请重试或手动录入。") from exc


def _verified_quote(value: str, quote: str, text: str) -> tuple[str, str, str]:
    """Return a claim only when its quoted source contains it verbatim."""
    value, quote = value.strip(), quote.strip()
    if value and quote and quote in text and value in quote:
        return value, quote, "原文匹配"
    return "", quote if quote in text else "", "待确认"


def _quote_owner(quote: str, jobs: list[dict]) -> int | None:
    """Recognize only a unique company/role mention in the same source quote."""
    if len(jobs) == 1:
        return 0
    matches: set[int] = set()
    for index, job in enumerate(jobs):
        for field in ("company", "role"):
            marker = job[field]
            if marker and sum(other[field] == marker for other in jobs) == 1 and marker in quote:
                matches.add(index)
    return next(iter(matches)) if len(matches) == 1 else None


def _verify_batch(parsed: _IntakeBatch | _JobIntakeBatch, text: str) -> dict:
    if len(parsed.jobs) > 6:
        raise ExtractionError("本次识别超过 6 个岗位，请分批粘贴后重试。")
    jobs = [job.model_dump() for job in parsed.jobs]
    seen_jobs: set[tuple[str, str]] = set()
    for job in jobs:
        evidence: dict[str, dict[str, str]] = {}
        for field in ("company", "role"):
            value, quote, status = _verified_quote(job[field], job[f"{field}_quote"], text)
            job[field], job[f"{field}_quote"] = value, quote
            evidence[field] = {"quote": quote, "status": status}
        identity = (job["company"], job["role"])
        if all(identity):
            if identity in seen_jobs:
                raise ExtractionError("AI 返回了重复的公司和岗位，请分批输入或重试。")
            seen_jobs.add(identity)
        for field in ("company_info", "jd"):
            value = job[field].strip()
            job[field] = value if value and value in text else ""
            evidence[field] = {
                "quote": job[field],
                "status": "原文匹配" if job[field] else "待确认",
            }
        job["evidence"] = evidence

    all_unassigned = _verify_result(
        _JobInfo(
            company="", role="", company_info="", jd="",
            recruitment_start=None, recruitment_end=None,
            events=parsed.unassigned_events,
            candidate_profile=getattr(parsed, "candidate_profile", _CandidateProfile()),
        ),
        text,
    )
    unassigned = all_unassigned["events"]
    for index, (job, original) in enumerate(zip(jobs, parsed.jobs)):
        evidence = job["evidence"]
        for field in ("recruitment_start", "recruitment_end"):
            quote = job[f"{field}_quote"].strip()
            quoted = quote in text if quote else False
            date_value = _verified_date(job[field], quote) if quoted else None
            owner = _quote_owner(quote, jobs) if quoted else None
            if len(jobs) > 1 and owner != index:
                date_value = None
            if field == "recruitment_end" and date_value and not _OFFICIAL_DEADLINE.search(quote):
                date_value = None
            job[field] = date_value
            job[f"{field}_quote"] = quote if quoted else ""
            evidence[field] = {
                "quote": job[f"{field}_quote"],
                "status": "原文匹配" if date_value else "待确认",
            }
        link = job["link_url"].strip()
        quote = job["link_quote"].strip()
        try:
            link = validate_link_url(link)
        except ValueError:
            link = ""
        if not (link and quote and quote in text and link in quote):
            link = ""
        if len(jobs) > 1 and _quote_owner(quote, jobs) != index:
            link = ""
        job["link_url"] = link
        job["link_quote"] = quote if quote in text else ""
        evidence["link_url"] = {
            "quote": job["link_quote"],
            "status": "原文匹配" if link else "待确认",
        }
        verified = _verify_result(
            _JobInfo(
                company=job["company"], role=job["role"],
                company_info=job["company_info"], jd=job["jd"],
                recruitment_start=None, recruitment_end=None,
                events=original.events,
            ),
            text,
        )
        job["events"] = []
        for event in verified["events"]:
            if _quote_owner(event["source_quote"], jobs) == index:
                job["events"].append(event)
            else:
                unassigned.append(event)
    return {
        "jobs": jobs,
        "unassigned_events": unassigned,
        "candidate_profile": all_unassigned["candidate_profile"],
    }


def extract_intake(
    text: str,
    reference_date: str | None = None,
    *,
    api_key: str | None = None,
    llm_config: LLMRuntimeConfig | None = None,
) -> dict:
    """Extract multiple reviewable job drafts and one candidate profile."""
    if not isinstance(text, str) or not text.strip():
        raise ExtractionError("请先粘贴简历、JD、招聘通知或进度记录。")
    source_limit = INTAKE_SKILL.input_limit("source_text")
    if len(text) > source_limit.max_chars:
        raise ExtractionError(f"信息分拣单次最多支持 {source_limit.max_chars} 个字符。")
    if reference_date is not None:
        try:
            if reference_date != date.fromisoformat(reference_date).isoformat():
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise ExtractionError("参考日期请使用 YYYY-MM-DD 格式。") from exc
    try:
        parsed = parse_structured(
            messages=[
                {"role": "system", "content": BATCH_SYSTEM_PROMPT},
                {"role": "user", "content": (
                    f"reference_date（仅供上下文理解，不用来补全日期）：{reference_date or '未提供'}\n"
                    f"以下是待提取的原文：\n{text}"
                )},
            ],
            schema=_IntakeBatch,
            operation=INTAKE_SKILL.operation,
            max_output_tokens=INTAKE_SKILL.max_output_tokens,
            timeout=INTAKE_SKILL.timeout_seconds,
            api_key=api_key,
            llm_config=llm_config,
        )
    except LLMConfigurationError as exc:
        raise ExtractionError(f"{exc}；设置后可使用 AI 提取，或先手动录入时间线。") from exc
    except LLMRequestError as exc:
        raise ExtractionError("AI 提取请求失败，请检查 API Key、模型 ID、接口地址及 JSON mode 支持后重试。") from exc
    try:
        return _verify_batch(parsed, text)
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError("AI 返回的数据格式不正确，请重试或手动录入。") from exc


def extract_job_intake(
    text: str,
    reference_date: str | None = None,
    *,
    api_key: str | None = None,
    llm_config: LLMRuntimeConfig | None = None,
) -> dict:
    """Analyze job facts without sending a candidate-profile output schema."""
    if not isinstance(text, str) or not text.strip():
        raise ExtractionError("请先粘贴 JD、招聘通知或进度消息。")
    maximum = JOB_SKILL.input_limit("source_text").max_chars
    if len(text) > maximum:
        raise ExtractionError(f"岗位信息单次最多支持 {maximum} 个字符。")
    if reference_date is not None:
        try:
            if reference_date != date.fromisoformat(reference_date).isoformat():
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise ExtractionError("参考日期请使用 YYYY-MM-DD 格式。") from exc
    try:
        parsed = parse_structured(
            messages=[
                {"role": "system", "content": JOB_SYSTEM_PROMPT},
                {"role": "user", "content": (
                    f"reference_date（仅供上下文理解，不用来补全日期）：{reference_date or '未提供'}\n"
                    f"以下是待提取的岗位原文：\n{text}"
                )},
            ],
            schema=_JobIntakeBatch,
            operation=JOB_SKILL.operation,
            max_output_tokens=JOB_SKILL.max_output_tokens,
            timeout=JOB_SKILL.timeout_seconds,
            api_key=api_key,
            llm_config=llm_config,
        )
    except LLMConfigurationError as exc:
        raise ExtractionError(f"{exc}；也可手动录入岗位。") from exc
    except LLMRequestError as exc:
        raise ExtractionError("AI 分拣岗位失败，请检查模型配置及 JSON mode 支持后重试。") from exc
    try:
        result = _verify_batch(parsed, text)
        result.pop("candidate_profile", None)
        return result
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError("AI 返回的数据格式不正确，请重试或手动录入。") from exc
