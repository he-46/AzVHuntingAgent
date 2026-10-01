"""One persistent candidate profile, edited independently of applications."""

from __future__ import annotations

import json
from difflib import SequenceMatcher

import streamlit as st

import database
from extractor import ExtractionError, extract_candidate_profile
from job_agent.documents import DocumentError, extract_document_text
from job_agent.llm.client import LLMRuntimeConfig


_LIST_LABELS = {
    "education": "教育经历", "experiences": "工作经历", "internships": "实习经历",
    "projects": "项目经历", "skills": "技能",
}


def _has_profile_facts(profile: dict) -> bool:
    return any(
        profile.get(field)
        for field in ("name", "contact", "summary", *_LIST_LABELS)
    )


def _item_key(field: str, item_id: int) -> str:
    return f"profile_item_{field}_{item_id}"


def _new_item(field: str, value: str = "", status: str = "手动添加") -> None:
    item_id = st.session_state.get("profile_next_item_id", 0) + 1
    st.session_state["profile_next_item_id"] = item_id
    st.session_state.setdefault(f"profile_items_{field}", []).append((item_id, status))
    st.session_state[_item_key(field, item_id)] = value


def _remove_item(field: str, item_id: int) -> None:
    key = f"profile_items_{field}"
    st.session_state[key] = [row for row in st.session_state.get(key, []) if row[0] != item_id]
    st.session_state.pop(_item_key(field, item_id), None)


def _current_items(field: str) -> list[str]:
    return list(dict.fromkeys(
        value for item_id, _ in st.session_state.get(f"profile_items_{field}", [])
        if (value := st.session_state.get(_item_key(field, item_id), "").strip())
    ))


def _load_editor(profile: dict | None) -> None:
    """Load saved facts, then mark AI additions for review without losing edits."""
    signature = json.dumps(profile or {}, sort_keys=True, ensure_ascii=False)
    loaded = st.session_state.get("profile_editor_loaded_signature") == signature
    incoming = st.session_state.pop("pending_profile_candidate", None)
    if loaded and incoming is None:
        return
    existing = profile or {}
    current = {
        field: _current_items(field) if loaded else list(existing.get(field) or [])
        for field in _LIST_LABELS
    }
    if incoming is not None:
        details, source_text = incoming
        st.session_state["profile_source_input"] = source_text or st.session_state.get("profile_source_input", "")
        conflicts = []
        for field in ("name", "contact", "summary"):
            key = f"profile_editor_{field}"
            old = st.session_state.get(key, "") if loaded else existing.get(field) or ""
            new = details.get(field) or ""
            st.session_state[key] = old or new
            if old and new and old != new:
                conflicts.append(f"{field}：AI 提取「{new}」，当前草稿保留「{old}」")
        st.session_state["profile_conflicts"] = conflicts
    else:
        st.session_state["profile_source_input"] = existing.get("source_text") or ""
        for field in ("name", "contact", "summary"):
            st.session_state[f"profile_editor_{field}"] = existing.get(field) or ""
        details = {}
    for field in _LIST_LABELS:
        for item_id, _ in st.session_state.get(f"profile_items_{field}", []):
            st.session_state.pop(_item_key(field, item_id), None)
        st.session_state[f"profile_items_{field}"] = []
        for value in current[field]:
            _new_item(field, value, "已保存" if not loaded else "当前草稿")
        for value in details.get(field) or []:
            value = value.strip()
            if not value or value in current[field]:
                continue
            similar = any(SequenceMatcher(None, value, old).ratio() >= 0.72 for old in current[field])
            _new_item(field, value, "疑似重复 · 待核对" if similar else "AI 新增 · 待核对")
            current[field].append(value)
    st.session_state["profile_editor_loaded_signature"] = signature
    if incoming is not None:
        st.session_state["profile_import_notice"] = "AI 新增条目已加入草稿；请逐条核对后保存。"


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
) -> None:
    """Render the independent profile editor and optional AI import."""
    _load_editor(profile)
    st.markdown('<div class="section-kicker">CANDIDATE PROFILE</div>', unsafe_allow_html=True)
    st.markdown("### 求职者资料")
    st.caption("只保存一份长期档案。每条经历、技能和项目都可以单独修改或删除。")
    with st.expander("导入简历并由 AI 分析", expanded=profile is None):
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
                        candidate = extract_candidate_profile(source_text, llm_config=llm_config)
                    if not _has_profile_facts(candidate):
                        st.warning("未识别到可核对的求职者资料，请手动填写。")
                    else:
                        st.session_state["pending_profile_candidate"] = (candidate, source_text)
                        st.rerun()
                except ExtractionError as exc:
                    st.error(str(exc))

    for conflict in st.session_state.get("profile_conflicts", []):
        st.warning(conflict)
    st.caption("简历原文仅作存档与分析依据；定制简历主要使用下面已保存的结构化资料。")
    first, second = st.columns(2)
    first.text_input("姓名", key="profile_editor_name")
    second.text_input("联系方式（可选）", key="profile_editor_contact", placeholder="邮箱 / 电话 / 个人主页")
    st.text_area("个人简介", key="profile_editor_summary", height=90)

    for field, label in _LIST_LABELS.items():
        st.markdown(f"#### {label}")
        rows = st.session_state.get(f"profile_items_{field}", [])
        if not rows:
            st.caption("暂无条目。")
        for position, (item_id, status) in enumerate(rows, start=1):
            editor, action = st.columns([5, 1], gap="small")
            with editor:
                widget = st.text_input if field == "skills" else st.text_area
                widget(
                    f"{label} {position} · {status}", key=_item_key(field, item_id),
                    **({} if field == "skills" else {"height": 80}),
                )
            with action:
                st.button(
                    "删除", key=f"delete_profile_{field}_{item_id}",
                    on_click=_remove_item, args=(field, item_id), use_container_width=True,
                )
        st.button(f"添加{label}", key=f"add_profile_{field}", on_click=_new_item, args=(field,))

    if st.button("保存求职者资料", type="primary", use_container_width=True):
        details = {
            "name": st.session_state.get("profile_editor_name", "").strip(),
            "contact": st.session_state.get("profile_editor_contact", "").strip(),
            "summary": st.session_state.get("profile_editor_summary", "").strip(),
            **{field: _current_items(field) for field in _LIST_LABELS},
        }
        try:
            database.save_candidate_profile(
                details, source_text=st.session_state.get("profile_source_input", ""), db_path=db_path,
            )
            st.session_state["profile_conflicts"] = []
            st.session_state["flash_success"] = "求职者资料已更新，所有岗位的简历制作都会读取这份档案。"
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
