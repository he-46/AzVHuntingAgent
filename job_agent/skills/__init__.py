"""Allowlisted, model-backed capabilities for the job search agent."""

from job_agent.skills.base import InputLimit, SkillSpec
from job_agent.skills.documents import LocalDocumentSkill, list_local_document_skills
from job_agent.skills.registry import get_skill, list_skills

__all__ = [
    "InputLimit", "SkillSpec", "LocalDocumentSkill",
    "get_skill", "list_skills", "list_local_document_skills",
]
