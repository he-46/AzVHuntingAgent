"""Generate evidence-grounded, job-specific resume drafts with the selected model."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

from job_agent.llm.client import (
    LLMRuntimeConfig,
    LLMConfigurationError,
    LLMRequestError,
    parse_structured,
)
from job_agent.skills.registry import get_skill
from job_agent.skills.resume import SYSTEM_PROMPT as RESUME_SYSTEM_PROMPT


class ResumeGenerationError(RuntimeError):
    """A user-facing resume generation failure."""


class _ResumeEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str
    source_quote: str


class _ResumeDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tailored_resume_markdown: str
    match_analysis: list[str]
    missing_evidence: list[str]
    interview_focus: list[str]
    evidence_map: list[_ResumeEvidence]


RESUME_SKILL = get_skill("resume_generation")


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?%?", text))


def generate_resume_draft(
    *,
    source_resume: str,
    company: str,
    role: str,
    jd: str,
    company_info: str = "",
    api_key: str | None = None,
    llm_config: LLMRuntimeConfig | None = None,
) -> dict:
    """Return a reviewable resume draft without inventing unsupported numbers."""
    if not isinstance(source_resume, str) or not source_resume.strip():
        raise ResumeGenerationError("请先粘贴基础简历。")
    resume_limit = RESUME_SKILL.input_limit("source_resume")
    if len(source_resume) > resume_limit.max_chars:
        raise ResumeGenerationError(
            f"{resume_limit.label}共 {len(source_resume)} 个字符，"
            f"单次最多支持 {resume_limit.max_chars} 个字符。"
        )
    if not jd.strip():
        raise ResumeGenerationError("当前岗位没有 JD，请先补充 JD 后再制作定制简历。")
    jd_limit = RESUME_SKILL.input_limit("jd")
    if len(jd) > jd_limit.max_chars:
        raise ResumeGenerationError(
            f"{jd_limit.label}共 {len(jd)} 个字符，"
            f"单次最多支持 {jd_limit.max_chars} 个字符。"
        )
    try:
        parsed = parse_structured(
            messages=[
                {"role": "system", "content": RESUME_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"目标公司：{company}\n目标岗位：{role}\n公司信息：{company_info or '未提供'}\n"
                        f"目标 JD：\n{jd}\n\n候选人基础简历：\n{source_resume}"
                    ),
                },
            ],
            schema=_ResumeDraft,
            operation=RESUME_SKILL.operation,
            max_output_tokens=RESUME_SKILL.max_output_tokens,
            timeout=RESUME_SKILL.timeout_seconds,
            api_key=api_key,
            llm_config=llm_config,
        )
    except LLMConfigurationError as exc:
        raise ResumeGenerationError(f"{exc}；设置后才能生成定制简历。") from exc
    except LLMRequestError as exc:
        raise ResumeGenerationError("简历生成失败，请检查 API Key、模型 ID、接口地址及 JSON mode 支持后重试。") from exc
    result = parsed.model_dump()
    draft = result["tailored_resume_markdown"].strip()
    if not draft:
        raise ResumeGenerationError("模型返回的简历草稿为空，请重试。")
    unsupported = sorted(_numbers(draft) - _numbers(source_resume))
    if unsupported:
        raise ResumeGenerationError(
            "草稿出现基础简历未提供的数字，已阻止导入：" + "、".join(unsupported)
        )
    claims = {
        item["claim"].strip(): item["source_quote"].strip()
        for item in result["evidence_map"]
    }
    factual_lines = [
        line.strip().lstrip("-* ").strip()
        for line in draft.splitlines()
        if line.strip() and not line.lstrip().startswith("#") and line.strip() != "---"
    ]
    unverified = [line for line in factual_lines if not claims.get(line)]
    bad_quotes = [
        claim for claim, quote in claims.items()
        if quote not in source_resume or claim not in factual_lines
    ]
    if unverified or bad_quotes:
        raise ResumeGenerationError(
            "简历中存在未对应到基础简历原文的内容，已阻止导入；请重试并逐项核对。"
        )
    result["tailored_resume_markdown"] = draft
    return result
