"""Resume studio UI."""

from __future__ import annotations

import streamlit as st

import database
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


def render_resume_workflow(job: dict, *, db_path: str) -> None:
    application_id = job["id"]
    versions = database.list_resume_versions(application_id, db_path=db_path)
    candidate_profile = database.get_candidate_profile(db_path=db_path)
    source_key = f"resume_source_{application_id}"
    draft_key = f"resume_draft_{application_id}"
    editor_key = f"resume_editor_{application_id}"
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
            file_name=f"{job['company']}_{job['role']}_定制简历.md",
            mime="text/markdown",
            use_container_width=True,
            key=f"download_resume_{application_id}",
        )

    if versions:
        with st.expander(f"已保存版本（{len(versions)}）"):
            for version in versions:
                st.markdown(f"**版本 #{version['id']} · {version['created_at']}**")
                st.caption(version["match_analysis"] or "未保存匹配说明")
                st.download_button(
                    "下载此版本",
                    data=version["tailored_resume"].encode("utf-8"),
                    file_name=f"{job['company']}_{job['role']}_简历版本{version['id']}.md",
                    mime="text/markdown",
                    key=f"download_saved_resume_{version['id']}",
                )
                st.divider()
