"""Resume studio UI."""

from __future__ import annotations

import re

import streamlit as st

import database
from job_agent.documents import (
    DocumentError,
    export_resume_docx,
    export_resume_pdf,
    extract_document_text,
)
from resume_agent import ResumeGenerationError, generate_resume_draft


def _candidate_profile_markdown(profile: dict | None) -> str:
    if not profile:
        return ""
    sections: list[str] = []
    if profile.get("name"):
        sections.append(f"# {profile['name']}")
    if profile.get("summary"):
        sections.append(f"## 个人简介\n{profile['summary']}")
    for field, title in (
        ("education", "教育经历"),
        ("experiences", "工作经历"),
        ("internships", "实习经历"),
        ("projects", "项目经历"),
    ):
        items = profile.get(field) or []
        if items:
            sections.append(f"## {title}\n" + "\n".join(f"- {item}" for item in items))
    if profile.get("skills"):
        sections.append("## 技能\n" + "、".join(profile["skills"]))
    return "\n\n".join(sections)


def _import_base_resume(source_key: str, upload_key: str, notice_key: str) -> None:
    upload = st.session_state.get(upload_key)
    if upload is None:
        st.session_state[notice_key] = ("error", "请先选择简历文件。")
        return
    try:
        st.session_state[source_key] = extract_document_text(
            upload.name, upload.getvalue(), max_chars=20_000
        )
    except DocumentError as exc:
        st.session_state[notice_key] = ("error", str(exc))
        return
    st.session_state[notice_key] = ("success", "简历文字已导入，请核对后再生成定制简历。")


def _safe_resume_filename(company: str, role: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", f"{company}_{role}_定制简历")


def render_resume_workflow(
    job: dict,
    *,
    db_path: str,
    api_key: str | None = None,
) -> None:
    application_id = job["id"]
    versions = database.list_resume_versions(application_id, db_path=db_path)
    candidate_profile = database.get_candidate_profile(db_path=db_path)
    source_key = f"resume_source_{application_id}"
    draft_key = f"resume_draft_{application_id}"
    editor_key = f"resume_editor_{application_id}"
    upload_key = f"resume_upload_{application_id}"
    notice_key = f"resume_import_notice_{application_id}"
    if source_key not in st.session_state:
        st.session_state[source_key] = (
            versions[0]["source_resume"]
            if versions
            else _candidate_profile_markdown(candidate_profile)
        )

    st.markdown('<div class="section-kicker">RESUME STUDIO</div>', unsafe_allow_html=True)
    st.markdown("### 岗位定制简历")
    st.markdown(
        """
        <div class="resume-steps">
          <div class="resume-step"><b>01 · 基础简历</b>粘贴真实经历与技能</div>
          <div class="resume-step"><b>02 · AI 定制</b>按当前 JD 调整重点与表达</div>
          <div class="resume-step"><b>03 · 核对导出</b>人工修改、保存版本并下载</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    with st.container(border=True):
        st.caption(f"当前目标：{job['company']} · {job['role']}")
        st.file_uploader(
            "导入基础简历（Word / PDF）", type=["docx", "pdf"], key=upload_key,
        )
        st.button(
            "将文件文字放入基础简历",
            key=f"import_resume_{application_id}",
            on_click=_import_base_resume,
            args=(source_key, upload_key, notice_key),
            use_container_width=True,
        )
        notice = st.session_state.pop(notice_key, None)
        if notice:
            getattr(st, notice[0])(notice[1])
        source_resume = st.text_area(
            "基础简历",
            height=260,
            placeholder="粘贴你的现有简历。请包含真实的教育、经历、项目和技能信息。",
            key=source_key,
        )
        st.caption("生成时，基础简历、当前 JD 和公司信息会发送至配置的模型服务。草稿不会自动投递。")
        if st.button(
            "根据当前 JD 生成定制简历",
            type="primary",
            use_container_width=True,
            key=f"generate_resume_{application_id}",
        ):
            try:
                with st.spinner("正在分析 JD 并重组简历重点…"):
                    draft = generate_resume_draft(
                        source_resume=source_resume,
                        company=job["company"],
                        role=job["role"],
                        jd=job.get("jd") or "",
                        company_info=job.get("company_info") or "",
                        api_key=api_key,
                    )
                st.session_state[draft_key] = draft
                st.session_state[editor_key] = draft["tailored_resume_markdown"]
                st.rerun()
            except ResumeGenerationError as exc:
                st.error(str(exc))

    draft = st.session_state.get(draft_key)
    if draft:
        st.markdown("#### 核对并修改草稿")
        tailored_resume = st.text_area("定制简历（Markdown）", height=460, key=editor_key)
        analysis_column, gap_column = st.columns(2)
        with analysis_column:
            st.write("**匹配点**")
            for item in draft.get("match_analysis") or []:
                st.write(f"• {item}")
        with gap_column:
            st.write("**缺少证据，请勿直接写入简历**")
            for item in draft.get("missing_evidence") or []:
                st.write(f"• {item}")
        with st.expander("查看逐条原文依据"):
            for item in draft.get("evidence_map") or []:
                st.write(f"**{item['claim']}**")
                st.caption(f"基础简历原文：{item['source_quote']}")
            st.caption("手动修改草稿后，请重新核对改动内容的事实依据。")
        if draft.get("interview_focus"):
            with st.expander("建议准备的面试主题"):
                for item in draft["interview_focus"]:
                    st.write(f"• {item}")

        save_column, download_column = st.columns(2)
        if save_column.button("保存这个版本", use_container_width=True, key=f"save_resume_{application_id}"):
            try:
                database.add_resume_version(
                    application_id,
                    source_resume,
                    tailored_resume,
                    "\n".join(draft.get("match_analysis") or []),
                    "\n".join(draft.get("missing_evidence") or []),
                    db_path=db_path,
                )
                st.success("简历版本已保存。")
            except ValueError as exc:
                st.error(str(exc))
        download_column.download_button(
            "下载 Markdown",
            data=tailored_resume.encode("utf-8"),
            file_name=f"{_safe_resume_filename(job['company'], job['role'])}.md",
            mime="text/markdown",
            use_container_width=True,
            key=f"download_resume_{application_id}",
        )
        docx_column, pdf_column = st.columns(2)
        filename = _safe_resume_filename(job["company"], job["role"])
        try:
            docx_data = export_resume_docx(tailored_resume)
            docx_column.download_button(
                "下载 Word",
                data=docx_data,
                file_name=f"{filename}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True,
                key=f"download_resume_docx_{application_id}",
            )
            pdf_data = export_resume_pdf(tailored_resume)
            pdf_column.download_button(
                "下载 PDF",
                data=pdf_data,
                file_name=f"{filename}.pdf",
                mime="application/pdf",
                use_container_width=True,
                key=f"download_resume_pdf_{application_id}",
            )
        except DocumentError as exc:
            st.error(str(exc))

    if versions:
        with st.expander(f"已保存版本（{len(versions)}）"):
            selected_version_id = st.selectbox(
                "选择已保存版本",
                [version["id"] for version in versions],
                format_func=lambda version_id: next(
                    f"版本 #{version['id']} · {version['created_at']}"
                    for version in versions if version["id"] == version_id
                ),
                key=f"saved_resume_selection_{application_id}",
            )
            version = next(item for item in versions if item["id"] == selected_version_id)
            st.caption(version["match_analysis"] or "未保存匹配说明")
            filename = f"{_safe_resume_filename(job['company'], job['role'])}_版本{version['id']}"
            markdown_column, word_column, pdf_column = st.columns(3)
            markdown_column.download_button(
                "下载 Markdown", data=version["tailored_resume"].encode("utf-8"),
                file_name=f"{filename}.md", mime="text/markdown",
                key=f"download_saved_resume_{version['id']}",
                use_container_width=True,
            )
            try:
                word_column.download_button(
                    "下载 Word", data=export_resume_docx(version["tailored_resume"]),
                    file_name=f"{filename}.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    key=f"download_saved_resume_docx_{version['id']}",
                    use_container_width=True,
                )
                pdf_column.download_button(
                    "下载 PDF", data=export_resume_pdf(version["tailored_resume"]),
                    file_name=f"{filename}.pdf", mime="application/pdf",
                    key=f"download_saved_resume_pdf_{version['id']}",
                    use_container_width=True,
                )
            except DocumentError as exc:
                st.error(str(exc))
