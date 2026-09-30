"""Reviewable specifications for local document capabilities."""

from __future__ import annotations

from dataclasses import dataclass

from job_agent.documents import MAX_FILE_BYTES


@dataclass(frozen=True, slots=True)
class LocalDocumentSkill:
    key: str
    title: str
    description: str
    formats: tuple[str, ...]
    max_file_bytes: int | None
    external_data: tuple[str, ...] = ()


DOCUMENT_IMPORT = LocalDocumentSkill(
    key="document_text_import",
    title="Word / PDF 文字导入",
    description="本地提取可编辑 Word 或文字型 PDF 的内容，先放入输入框由用户核对。",
    formats=("DOCX", "PDF"),
    max_file_bytes=MAX_FILE_BYTES,
)

RESUME_EXPORT = LocalDocumentSkill(
    key="resume_document_export",
    title="定制简历文件导出",
    description="将已核对的简历草稿在本地导出为可编辑 Word 或 PDF。",
    formats=("DOCX", "PDF"),
    max_file_bytes=None,
)


_LOCAL_SKILLS = (DOCUMENT_IMPORT, RESUME_EXPORT)


def list_local_document_skills() -> tuple[LocalDocumentSkill, ...]:
    return _LOCAL_SKILLS
