"""Bounded, evidence-checked recommendations for existing profile items."""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field

from job_agent.llm.client import (
    LLMConfigurationError, LLMRequestError, LLMRuntimeConfig, parse_structured,
)
from job_agent.skills.matching import SYSTEM_PROMPT
from job_agent.skills.registry import get_skill


MATCHING_SKILL = get_skill("resume_material_matching")


class MatchingError(RuntimeError):
    pass


class _Recommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int
    jd_quote: str


class _Recommendations(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skills: list[_Recommendation] = Field(default_factory=list)
    projects: list[_Recommendation] = Field(default_factory=list)


def recommend_profile_items(
    jd: str,
    skills: list[str],
    projects: list[str],
    *,
    llm_config: LLMRuntimeConfig | None = None,
) -> dict[str, list[dict[str, str]]]:
    """Return only existing items backed by a literal JD excerpt."""
    if not jd.strip():
        raise MatchingError("当前岗位还没有 JD，请先补充后再使用 AI 推荐。")
    if not skills and not projects:
        raise MatchingError("档案中还没有技能或项目。")
    if len(jd) > MATCHING_SKILL.input_limit("jd").max_chars:
        raise MatchingError("JD 超出简历素材推荐的 15,000 字符上限。")
    items_json = json.dumps({"skills": skills, "projects": projects}, ensure_ascii=False)
    if len(items_json) > MATCHING_SKILL.input_limit("profile_items").max_chars:
        raise MatchingError("档案技能与项目内容过长，请先精简后再推荐。")
    try:
        parsed = parse_structured(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"JD:\n{jd}\n\n档案条目（编号从 0 开始）：\n{items_json}"},
            ],
            schema=_Recommendations,
            operation=MATCHING_SKILL.operation,
            max_output_tokens=MATCHING_SKILL.max_output_tokens,
            timeout=MATCHING_SKILL.timeout_seconds,
            llm_config=llm_config,
        )
    except LLMConfigurationError as exc:
        raise MatchingError(str(exc)) from exc
    except LLMRequestError as exc:
        raise MatchingError("AI 推荐失败，请检查模型配置及 JSON mode 支持后重试。") from exc

    result: dict[str, list[dict[str, str]]] = {"skills": [], "projects": []}
    for field, items, limit in (("skills", skills, 8), ("projects", projects, 4)):
        seen: set[int] = set()
        for row in getattr(parsed, field):
            quote = row.jd_quote.strip()
            if (
                isinstance(row.index, bool) or row.index < 0 or row.index >= len(items)
                or row.index in seen or not quote or quote not in jd
            ):
                continue
            seen.add(row.index)
            result[field].append({"item": items[row.index], "jd_quote": quote})
            if len(result[field]) >= limit:
                break
    return result
