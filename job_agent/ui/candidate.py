"""One persistent candidate profile, edited independently of applications."""

from __future__ import annotations

import json

import streamlit as st

import database
from extractor import ExtractionError, extract_intake
from job_agent.documents import DocumentError, extract_document_text
from job_agent.llm.client import LLMRuntimeConfig


_LIST_FIELDS = ("education", "experiences", "internships", "projects")


def _has_profile_facts(profile: dict) -> bool:
    return any(
        profile.get(field)
        for field in ("name", "contact", "summary", *_LIST_FIELDS, "skills")
    )


def _load_editor(profile: dict | None) -> None:
    """Load persisted fields and merge AI findings into the review draft."""
    signature = json.dumps(profile or {}, sort_keys=True, ensure_ascii=False)
    incoming = st.session_state.pop("pending_profile_candidate", None)
    if incoming is not None:
        details, source_text = incoming
        existing = profile or {}
        st.session_state["profile_editor_loaded_signature"] = signature
        st.session_state["profile_source_input"] = source_text or existing.get("source_text") or ""
        for field in ("name", "contact", "summary"):
            st.session_state[f"profile_editor_{field}"] = (
                details.get(field) or existing.get(field) or ""
            )
        for field in _LIST_FIELDS:
            combined = list(dict.fromkeys([
                *(existing.get(field) or []), *(details.get(field) or []),
            ]))
            st.session_state[f"profile_editor_{field}"] = "\n\n".join(combined)
        skills = list(dict.fromkeys([
            *(existing.get("skills") or []), *(details.get("skills") or []),
        ]))
        st.session_state["profile_editor_skills"] = "，".join(skills)
        st.session_state["profile_force_expand"] = True
        st.session_state["profile_import_notice"] = (
            "AI 已将新资料合入草稿，旧条目会保留；请检查、删改后保存。"
        )
        return
    if st.session_state.get("profile_editor_loaded_signature") == signature:
        return
    details = profile or {}
    st.session_state["profile_source_input"] = details.get("source_text") or ""
    for field in ("name", "contact", "summary"):
        st.session_state[f"profile_editor_{field}"] = details.get(field) or ""
    for field in _LIST_FIELDS:
        st.session_state[f"profile_editor_{field}"] = "\n\n".join(details.get(field) or [])
    st.session_state["profile_editor_skills"] = "，".join(details.get("skills") or [])
    st.session_state["profile_editor_loaded_signature"] = signature


def _split_entries(value: str) -> list[str]:
    return [part.strip() for part in value.replace("\r\n", "\n").split("\n\n") if part.strip()]


def _import_resume_file() -> None:
    upload = st.session_state.get("profile_resume_upload")
    if upload is None:
        st.session_state["profile_import_error"] = "请先选择 Word 或 PDF 简历。"
        return
    try:
        st.session_state["profile_source_input"] = extract_document_text(
            upload.name, upload.getvalue(), max_chars=30_000
        )
        st.session_state["profile_import_notice"] = "已提取文件文字；核对后可用 AI 分析。"
    except DocumentError as exc:
        st.session_state["profile_import_error"] = str(exc)


def render_candidate_profile(
    profile: dict | None,
    *,
    db_path: str,
    llm_config: LLMRuntimeConfig,
    reference_date: str,
) -> None:
    """Render the independent profile editor and optional AI import."""
    _load_editor(profile)
    st.markdown('<div class="section-kicker">CANDIDATE PROFILE</div>', unsafe_allow_html=True)
    st.markdown("### 求职者资料")
    st.caption("只保存一份长期档案，随时更新；新增岗位不会改写这里的内容。")
    expanded = profile is None or bool(st.session_state.pop("profile_force_expand", False))
    with st.expander("填写或更新长期档案", expanded=expanded):
        st.text_area(
            "简历原文（本地存档，也可用于 AI 分析）", key="profile_source_input", height=130,
            placeholder="粘贴现有简历；也可以导入 Word / PDF。",
        )
        st.file_uploader(
            "导入求职者简历（Word / PDF）", type=["docx", "pdf"],
            key="profile_resume_upload",
        )
        st.button(
            "将文件文字放入简历原文", on_click=_import_resume_file,
            use_container_width=True,
        )
        if error := st.session_state.pop("profile_import_error", None):
            st.error(error)
        if notice := st.session_state.pop("profile_import_notice", None):
            st.success(notice)
        if st.button("AI 分析简历并填入资料草稿", use_container_width=True):
            source_text = st.session_state.get("profile_source_input", "").strip()
            if not source_text:
                st.warning("请先粘贴简历或导入文件。")
            else:
                try:
                    with st.spinner("正在分析简历…"):
                        result = extract_intake(
                            source_text, reference_date=reference_date,
                            llm_config=llm_config,
                        )
                    candidate = result.get("candidate_profile") or {}
                    if not _has_profile_facts(candidate):
                        st.warning("未识别到可核对的求职者资料，请手动填写。")
                    else:
                        st.session_state["pending_profile_candidate"] = (candidate, source_text)
                        st.rerun()
                except ExtractionError as exc:
                    st.error(str(exc))

        with st.form("candidate_profile_form"):
            name = st.text_input("姓名", key="profile_editor_name")
            contact = st.text_input(
                "联系方式（可选）", key="profile_editor_contact",
                placeholder="邮箱 / 电话 / 个人主页",
            )
            summary = st.text_area("个人简介", key="profile_editor_summary", height=90)
            first, second = st.columns(2)
            education = first.text_area(
                "教育经历（条目之间空一行）", key="profile_editor_education", height=135,
            )
            experiences = second.text_area(
                "工作经历（条目之间空一行）", key="profile_editor_experiences", height=135,
            )
            third, fourth = st.columns(2)
            internships = third.text_area(
                "实习经历（条目之间空一行）", key="profile_editor_internships", height=135,
            )
            projects = fourth.text_area(
                "项目经历（条目之间空一行）", key="profile_editor_projects", height=135,
            )
            skills = st.text_area(
                "技能（逗号或换行分隔）", key="profile_editor_skills", height=80,
            )
            saved = st.form_submit_button("保存求职者资料", use_container_width=True)
        if saved:
            details = {
                "name": name.strip(),
                "contact": contact.strip(),
                "summary": summary.strip(),
                "education": _split_entries(education),
                "experiences": _split_entries(experiences),
                "internships": _split_entries(internships),
                "projects": _split_entries(projects),
                "skills": [
                    item.strip()
                    for item in skills.replace("，", ",").replace("\n", ",").split(",")
                    if item.strip()
                ],
            }
            try:
                database.save_candidate_profile(
                    details,
                    source_text=st.session_state.get("profile_source_input", ""),
                    db_path=db_path,
                )
                st.session_state["flash_success"] = "求职者资料已更新，所有岗位的简历制作都会读取这份档案。"
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
