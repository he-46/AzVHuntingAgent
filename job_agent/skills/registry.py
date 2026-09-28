"""Explicit registry for capabilities shipped with the application."""

from job_agent.skills.base import SkillSpec
from job_agent.skills.intake import SPEC as INTAKE_SPEC
from job_agent.skills.resume import SPEC as RESUME_SPEC


_SKILLS = (INTAKE_SPEC, RESUME_SPEC)
_BY_KEY = {skill.key: skill for skill in _SKILLS}

if len(_BY_KEY) != len(_SKILLS):
    raise RuntimeError("Skill keys must be unique.")


def list_skills() -> tuple[SkillSpec, ...]:
    """Return the immutable, allowlisted skill catalog."""
    return _SKILLS


def get_skill(key: str) -> SkillSpec:
    """Look up one registered skill."""
    try:
        return _BY_KEY[key]
    except KeyError as exc:
        raise KeyError(f"Unknown skill: {key}") from exc
