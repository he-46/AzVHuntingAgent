"""Generate evidence-grounded, job-specific resume drafts with OpenAI."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

from job_agent.llm.client import (
    LLMConfigurationError,
    LLMRequestError,
    parse_structured,
)


class ResumeGenerationError(RuntimeError):
    """A user-facing resume generation failure."""


class _ResumeDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tailored_resume_markdown: str
    match_analysis: list[str]
    missing_evidence: list[str]
    interview_focus: list[str]


_INSTRUCTIONS = """你是求职简历编辑器。根据候选人的基础简历和目标岗位资料，生成中文 Markdown 简历草稿。
只能重组、压缩和改写基础简历中明确存在的事实。不得新增公司、项目、职责、技能、学历、证书、数字、
业绩、时间或个人信息。JD 中出现而基础简历未证明的能力，只能列入 missing_evidence，不能写进简历。
优先把与 JD 最相关的真实经历放在前面，使用简洁、具体、以行动开头的表达。保留基础简历中的联系方式，
不要猜测或补全。tailored_resume_markdown 应包含适合直接编辑的完整简历结构。
match_analysis 简明说明已有经历与 JD 的匹配点；missing_evidence 列出 JD 要求但简历没有证据的内容；
interview_focus 列出建议准备的面试主题。不要输出录取概率。"""


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?%?", text))


def generate_resume_draft(
    *,
    source_resume: str,
    company: str,
    role: str,
    jd: str,
    company_info: str = "",
) -> dict:
    """Return a reviewable resume draft without inventing unsupported numbers."""
    if not isinstance(source_resume, str) or not source_resume.strip():
        raise ResumeGenerationError("请先粘贴基础简历。")
    if len(source_resume) > 20_000:
        raise ResumeGenerationError(
            f"基础简历共 {len(source_resume)} 个字符，单次最多支持 20000 个字符。"
        )
    if not jd.strip():
        raise ResumeGenerationError("当前岗位没有 JD，请先补充 JD 后再制作定制简历。")
    if len(jd) > 15_000:
        raise ResumeGenerationError(f"当前 JD 共 {len(jd)} 个字符，单次最多支持 15000 个字符。")
    try:
        parsed = parse_structured(
            messages=[
                {"role": "system", "content": _INSTRUCTIONS},
                {
                    "role": "user",
                    "content": (
                        f"目标公司：{company}\n目标岗位：{role}\n公司信息：{company_info or '未提供'}\n"
                        f"目标 JD：\n{jd}\n\n候选人基础简历：\n{source_resume}"
                    ),
                },
            ],
            schema=_ResumeDraft,
            operation="resume_generation",
            max_output_tokens=4_000,
            timeout=45.0,
        )
    except LLMConfigurationError as exc:
        raise ResumeGenerationError(f"{exc}；设置后才能生成定制简历。") from exc
    except LLMRequestError as exc:
        raise ResumeGenerationError("简历生成失败，请检查 API Key、网络及模型权限后重试。") from exc
    result = parsed.model_dump()
    draft = result["tailored_resume_markdown"].strip()
    if not draft:
        raise ResumeGenerationError("模型返回的简历草稿为空，请重试。")
    unsupported = sorted(_numbers(draft) - _numbers(source_resume))
    if unsupported:
        raise ResumeGenerationError(
            "草稿出现基础简历未提供的数字，已阻止导入：" + "、".join(unsupported)
        )
    result["tailored_resume_markdown"] = draft
    return result
