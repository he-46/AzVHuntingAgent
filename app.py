"""Local job application timeline prototype."""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta

import altair as alt
import pandas as pd
import streamlit as st

import database
import domain
from extractor import ExtractionError, extract_job_intake
from job_agent.config import Settings
from job_agent.documents import DocumentError, extract_document_text
from job_agent.llm.client import daily_usage_snapshot
from job_agent.links import validate_link_url
from job_agent.skills import list_local_document_skills, list_skills
from job_agent.services.intake import save_reviewed_intake, sync_recruitment_event
from job_agent.ui.backup import render_backup_controls
from job_agent.ui.candidate import render_candidate_profile
from job_agent.ui.llm_settings import configured_llm, render_llm_settings
from job_agent.llm.client import LLMRuntimeConfig
from job_agent.ui.portfolio import (
    portfolio_chart as portfolio_chart_component,
    portfolio_snapshots as portfolio_snapshots_component,
    render_portfolio_overview,
    timeline_display_rows,
)
from job_agent.ui.resume import render_resume_workflow
from job_agent.ui.styles import inject_styles
from sample_data import load_sample_data


SETTINGS = Settings.from_env()
DB_PATH = SETTINGS.db_path
EVENT_TYPES = [
    "招聘开始",
    "岗位发布",
    "投递截止",
    "已投递",
    "测评邀请",
    "测评完成",
    "面试邀请",
    "面试完成",
    "HR反馈",
    "录取通知",
    "拒绝",
    "其他",
]
DEADLINE_KINDS = ["无", "官方截止", "自设截止", "建议跟进"]
NOW = SETTINGS.now()
TODAY = NOW.date()


def _text(value: object) -> str:
    if value is None or (not isinstance(value, (dict, list)) and pd.isna(value)):
        return ""
    return str(value).strip()


def _date_or_none(value: object) -> str | None:
    raw = _text(value)
    if not raw:
        return None
    try:
        if len(raw) != 10:
            raise ValueError(raw)
        return date.fromisoformat(raw).isoformat()
    except ValueError as exc:
        raise ValueError(f"日期应为 YYYY-MM-DD：{raw}") from exc


def _deadline_or_none(value: object) -> str | None:
    raw = _text(value)
    if not raw:
        return None
    try:
        if len(raw) == 10:
            return date.fromisoformat(raw).isoformat()
        return datetime.fromisoformat(raw.replace(" ", "T")).isoformat(timespec="minutes")
    except ValueError as exc:
        raise ValueError(f"截止时间应为 YYYY-MM-DD 或 YYYY-MM-DD HH:MM：{raw}") from exc


def _csv_bytes(rows: list[dict]) -> bytes:
    output = io.StringIO()
    columns = ["日期", "事件", "阶段", "说明", "截止时间", "截止性质", "来源", "原文依据", "状态"]
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                "日期": row.get("date", ""),
                "事件": row.get("event_type", ""),
                "阶段": row.get("stage", ""),
                "说明": row.get("details", ""),
                "截止时间": row.get("deadline_at", ""),
                "截止性质": row.get("deadline_kind", ""),
                "来源": row.get("source", ""),
                "原文依据": row.get("source_quote", ""),
                "状态": row.get("status", ""),
            }
        )
    return output.getvalue().encode("utf-8-sig")


def _timeline_display_rows(timeline: list[dict]) -> list[dict]:
    return timeline_display_rows(timeline)


# Compatibility wrappers keep tests and existing imports stable while the
# implementation lives in the UI package.
def _portfolio_snapshots(jobs: list[dict]) -> list[dict]:
    return portfolio_snapshots_component(jobs, db_path=DB_PATH, today=TODAY, now=NOW)


def _portfolio_chart(snapshots: list[dict]) -> alt.LayerChart | None:
    return portfolio_chart_component(snapshots, today=TODAY)


def _portfolio_overview(jobs: list[dict]) -> None:
    render_portfolio_overview(
        jobs,
        db_path=DB_PATH,
        today=TODAY,
        now=NOW,
        on_select=_rerun_after_change,
    )


def _rerun_after_change(application_id: int | None = None) -> None:
    st.session_state["event_table_version"] = st.session_state.get("event_table_version", 0) + 1
    if application_id is not None:
        st.session_state["pending_selection"] = application_id
    st.rerun()


def _import_documents_to_unified_input() -> None:
    uploads = st.session_state.get("unified_document_uploads") or []
    if not uploads:
        st.session_state["document_import_notice"] = ("error", "请先选择 Word 或 PDF 文件。")
        return
    if len(uploads) > 3:
        st.session_state["document_import_notice"] = ("error", "每次最多导入 3 个文件。")
        return
    try:
        sections = [
            f"【文件：{upload.name}】\n{extract_document_text(upload.name, upload.getvalue())}"
            for upload in uploads
        ]
        original = st.session_state.get("unified_input", "").strip()
        combined = "\n\n".join(part for part in [original, *sections] if part)
        if len(combined) > 30_000:
            raise DocumentError("导入后超过 AI 分拣的 30,000 字符上限，请删减内容。")
    except DocumentError as exc:
        st.session_state["document_import_notice"] = ("error", str(exc))
        return
    st.session_state["unified_input"] = combined
    st.session_state["document_import_notice"] = ("success", "文件文字已加入总输入框，请核对后再点击 AI 分拣。")


def _sync_recruitment_event(application_id: int, event_type: str, event_date: str | None) -> None:
    sync_recruitment_event(
        application_id,
        event_type,
        event_date,
        db_path=DB_PATH,
    )


def _create_job(*, first_job: bool) -> None:
    container = (
        st.expander("手动新建职位（无需 AI）")
        if first_job
        else st.sidebar.expander("手动新建职位")
    )
    with container:
        with st.form("create_job", clear_on_submit=True):
            company = st.text_input("公司 *")
            role = st.text_input("岗位 *")
            recruitment_start = st.text_input("招聘开始日期", placeholder="YYYY-MM-DD，可空")
            recruitment_end = st.text_input("投递截止日期", placeholder="YYYY-MM-DD，可空")
            link_url = st.text_input("招聘 / 投递链接（可选）", placeholder="https://...")
            company_info = st.text_area("公司信息（可选）", placeholder="招聘流程、官网公告或其他背景信息")
            jd = st.text_area("JD / 招聘公告", height=150)
            submitted = st.form_submit_button("创建职位", use_container_width=True)
        if submitted:
            if not company.strip() or not role.strip():
                st.error("请填写公司和岗位。")
                return
            try:
                start = _date_or_none(recruitment_start)
                end = _date_or_none(recruitment_end)
                if start and end and start > end:
                    raise ValueError("投递截止日期不能早于招聘开始日期。")
                application_id = database.create_application(
                    company=company.strip(),
                    role=role.strip(),
                    company_info=company_info.strip(),
                    jd=jd.strip(),
                    recruitment_start=start,
                    recruitment_end=end,
                    link_url=link_url,
                    db_path=DB_PATH,
                )
                _sync_recruitment_event(application_id, "招聘开始", start)
                _sync_recruitment_event(application_id, "投递截止", end)
                _rerun_after_change(application_id)
            except ValueError as exc:
                st.error(str(exc))


def _job_details(job: dict) -> None:
    application_id = job["id"]
    with st.expander("岗位信息与修改"):
        with st.form(f"edit_job_{application_id}"):
            company = st.text_input("公司", value=job.get("company") or "")
            role = st.text_input("岗位", value=job.get("role") or "")
            start = st.text_input("招聘开始日期", value=job.get("recruitment_start") or "")
            end = st.text_input("投递截止日期", value=job.get("recruitment_end") or "")
            link_url = st.text_input("招聘 / 投递链接", value=job.get("link_url") or "")
            company_info = st.text_area("公司信息", value=job.get("company_info") or "", height=100)
            jd = st.text_area("JD / 招聘公告", value=job.get("jd") or "", height=180)
            saved = st.form_submit_button("保存岗位信息")
        if saved:
            if not company.strip() or not role.strip():
                st.error("公司和岗位不能为空。")
            else:
                try:
                    start_date = _date_or_none(start)
                    end_date = _date_or_none(end)
                    if start_date and end_date and start_date > end_date:
                        raise ValueError("投递截止日期不能早于招聘开始日期。")
                    database.update_application(
                        application_id,
                        {
                            "company": company.strip(),
                            "role": role.strip(),
                            "company_info": company_info.strip(),
                            "jd": jd.strip(),
                            "recruitment_start": start_date,
                            "recruitment_end": end_date,
                            "link_url": link_url,
                        },
                        db_path=DB_PATH,
                    )
                    _sync_recruitment_event(application_id, "招聘开始", start_date)
                    _sync_recruitment_event(application_id, "投递截止", end_date)
                    _rerun_after_change()
                except ValueError as exc:
                    st.error(str(exc))


def _add_event(application_id: int) -> None:
    with st.expander("＋ 记录投递、面试或截止时间", expanded=False):
        with st.form(f"add_event_{application_id}", clear_on_submit=True):
            col1, col2 = st.columns(2)
            event_type = col1.selectbox("事件类型", EVENT_TYPES)
            event_date = col2.date_input("发生日期", value=TODAY)
            details = st.text_area("简要记录", placeholder="例如：已完成一面，主要讨论项目经验")
            deadline_at = st.text_input("截止时间", placeholder="可空；YYYY-MM-DD 或 YYYY-MM-DD HH:MM")
            deadline_kind = st.selectbox("截止性质", DEADLINE_KINDS)
            source = st.text_input("信息来源", value="用户记录", placeholder="例如：招聘邮件、官网公告")
            source_quote = st.text_input("来源原文", placeholder="可空；例如：请于周五18:00前确认")
            feedback_label = st.selectbox(
                "面试自评（仅面试完成时填写）",
                ["未填写", "1 很差", "2 偏弱", "3 一般", "4 较好", "5 很好"],
            )
            submitted = st.form_submit_button("添加事件", use_container_width=True)
        if submitted:
            try:
                normalized_deadline = _deadline_or_none(deadline_at)
                if normalized_deadline and deadline_kind == "无":
                    raise ValueError("填写截止时间后，请选择截止性质。")
                if not normalized_deadline and deadline_kind != "无":
                    raise ValueError("选择截止性质后，请填写截止时间。")
                feedback_score = None if feedback_label == "未填写" else int(feedback_label[0])
                if feedback_score and event_type != "面试完成":
                    raise ValueError("面试自评应记录在“面试完成”事件中。")
                database.add_event(
                    application_id,
                    event_type=event_type,
                    event_date=event_date.isoformat(),
                    details=details.strip(),
                    deadline_at=normalized_deadline,
                    deadline_kind=None if deadline_kind == "无" else deadline_kind,
                    source=source.strip(),
                    source_quote=source_quote.strip(),
                    feedback_score=feedback_score,
                    db_path=DB_PATH,
                )
                _rerun_after_change()
            except ValueError as exc:
                st.error(str(exc))


def _unified_input(
    jobs: list[dict],
    selected_id: int | None,
    *,
    api_key: str | None = None,
    llm_config: LLMRuntimeConfig | None = None,
) -> None:
    st.markdown('<div class="section-kicker">SMART INTAKE</div>', unsafe_allow_html=True)
    st.markdown("### 岗位信息输入")
    st.caption("逐次粘贴岗位、JD、招聘链接和面试消息；AI 会分成岗位草稿，由你核对后保存。个人简历请到「求职者资料」工作区维护。")
    st.caption("单次最多整理 6 个岗位；更多岗位请分批输入。")
    if st.session_state.pop("clear_unified_input", False):
        st.session_state["unified_input"] = ""
    input_column, guide_column = st.columns([3.35, 1], gap="medium")
    with input_column:
        source_text = st.text_area(
            "总输入框",
            height=180,
            placeholder=(
                "例如：星辰科技招聘数据分析师。JD：负责业务指标分析……\n"
                "招聘截止：2026年10月15日18:00。\n"
                "我在2026年9月20日投递，2026年9月25日完成一面，自评4/5。"
            ),
            key="unified_input",
        )
        st.file_uploader(
            "导入 Word / PDF 文本",
            type=["docx", "pdf"],
            accept_multiple_files=True,
            key="unified_document_uploads",
            help="文件仅在本地提取文字；点击 AI 分拣后才会发送输入框中的内容。",
        )
        st.button(
            "将文件文字加入总输入框",
            on_click=_import_documents_to_unified_input,
            use_container_width=True,
        )
        notice = st.session_state.pop("document_import_notice", None)
        if notice:
            getattr(st, notice[0])(notice[1])
    with guide_column:
        st.markdown(
            """
            <div class="input-guide">
              <b>可以混合粘贴</b><br>
              公司与岗位信息<br>
              JD 或招聘公告<br>
              投递、测评、面试消息<br>
              截止日期与面试自评
              <br>招聘公告或投递链接<br>
              多个岗位可连续粘贴
            </div>
            """,
            unsafe_allow_html=True,
        )
    st.caption("点击 AI 分拣后，输入内容会发送至配置的模型服务；手动录入可在下方进行。")
    if st.button("AI 分拣信息", type="primary", key="unified_extract", use_container_width=True):
        if not source_text.strip():
            st.warning("请先在总输入框粘贴或输入信息。")
        else:
            try:
                with st.spinner("正在分拣职位、日期和进度…"):
                    result = extract_job_intake(
                        source_text,
                        reference_date=TODAY.isoformat(),
                        api_key=api_key,
                        llm_config=llm_config,
                    )
                version = st.session_state.get("unified_draft_version", 0) + 1
                st.session_state["unified_draft_version"] = version
                st.session_state["unified_draft"] = {
                    "version": version,
                    "source_text": source_text,
                    "result": result,
                }
                if not result.get("jobs") and not result.get("unassigned_events"):
                    st.session_state.pop("unified_draft", None)
                    st.info("未识别到岗位。若输入的是个人简历，请切换到「求职者资料」分析。")
            except ExtractionError as exc:
                st.error(str(exc))

    draft = st.session_state.get("unified_draft")
    if not draft:
        return

    batch = draft["result"]
    version = draft["version"]
    st.markdown("#### 核对分拣草稿")
    st.caption("“原文匹配”只表示内容在输入中出现，仍需核对真假、岗位归属与截止时间。")
    draft_jobs = batch.get("jobs") or []
    if draft_jobs:
        draft_index = st.selectbox(
            "当前核对的岗位草稿",
            range(len(draft_jobs)),
            format_func=lambda index: (
                f"{index + 1}. {draft_jobs[index].get('company') or '公司待确认'} · "
                f"{draft_jobs[index].get('role') or '岗位待确认'}"
                + (" · 已保存" if index in draft.get("saved_targets", {}) else "")
            ),
            key=f"draft_job_choice_{version}",
        )
        result = draft_jobs[draft_index]
    else:
        draft_index = None
        result = {}
        st.info("本次没有识别到明确岗位。可在上方的求职者资料区保存档案，或手动创建岗位。")
    widget_suffix = str(version) if draft_index in {None, 0} else f"{version}_{draft_index}"
    saved_target = draft.get("saved_targets", {}).get(draft_index)
    if saved_target is not None and not any(job["id"] == saved_target for job in jobs):
        draft["saved_targets"].pop(draft_index, None)
        saved_target = None
    target_options: list[str | int] = ([] if saved_target else ["新建职位"]) + [job["id"] for job in jobs]
    matched_id = next(
        (
            job["id"]
            for job in jobs
            if result.get("company")
            and result.get("role")
            and job["company"] == result["company"]
            and job["role"] == result["role"]
        ),
        None,
    )
    default_target = saved_target or matched_id or (
        selected_id if selected_id is not None and not result.get("company") and not result.get("role") else "新建职位"
    )
    target = st.selectbox(
        "保存到",
        target_options,
        index=target_options.index(default_target),
        format_func=lambda value: (
            "新建职位"
            if value == "新建职位"
            else next(f"更新：{job['company']} · {job['role']}" for job in jobs if job["id"] == value)
        ),
        key=f"draft_target_{widget_suffix}",
    ) if result else "新建职位"
    if result and target != "新建职位":
        st.caption("更新已有职位时，空白的资料字段会保留原值。请确认保存位置与输入内容属于同一岗位。")

    event_labels = [
        f"{index + 1}. {job.get('company') or '公司待确认'} · {job.get('role') or '岗位待确认'}"
        for index, job in enumerate(draft_jobs)
    ]
    preview_rows = []
    for owner, items in [
        *[(event_labels[index], job.get("events") or []) for index, job in enumerate(draft_jobs)],
        ("未确定归属", batch.get("unassigned_events") or []),
    ]:
        for item in items:
            preview_rows.append({
                "导入": owner != "未确定归属" and bool(item.get("event_date") or item.get("deadline_at")),
                "岗位归属": owner,
                "event_type": item.get("event_type") or "其他",
                "event_date": item.get("event_date") or "",
                "details": item.get("details") or "",
                "deadline_at": item.get("deadline_at") or "",
                "deadline_kind": item.get("deadline_kind") or "",
                "feedback_score": item.get("feedback_score"),
                "source_quote": item.get("source_quote") or "",
                "核对状态": "原文匹配" if item.get("source_quote") else "待确认",
                "人工确认": False,
            })
    candidate = batch.get("candidate_profile") or {}
    if any(candidate.get(field) for field in (
        "name", "contact", "summary", "education", "experiences", "internships", "projects", "skills"
    )):
        st.info("本次还识别到求职者资料；岗位保存不会改写长期档案。")
        if st.button("将识别到的资料放入长期档案草稿", key=f"transfer_candidate_{version}"):
            st.session_state["pending_profile_candidate"] = (candidate, draft["source_text"])
            st.rerun()
    with st.form(f"review_unified_{version}"):
        if result:
            st.write("**岗位与招聘信息**")
            evidence = result.get("evidence") or {}
            col1, col2 = st.columns(2)
            company = col1.text_input("公司", value=result.get("company") or "", key=f"draft_company_{widget_suffix}")
            role = col2.text_input("岗位", value=result.get("role") or "", key=f"draft_role_{widget_suffix}")
            for label, field in (("公司", "company"), ("岗位", "role")):
                item = evidence.get(field) or {}
                st.caption(f"{label}：{item.get('status', '待确认')} · 原文：{item.get('quote') or '未找到'}")
            col3, col4 = st.columns(2)
            start = col3.text_input(
                "招聘开始日期", value=result.get("recruitment_start") or "", key=f"draft_start_{widget_suffix}"
            )
            end = col4.text_input(
                "投递截止日期", value=result.get("recruitment_end") or "", key=f"draft_end_{widget_suffix}"
            )
            for label, field in (("招聘开始日期", "recruitment_start"), ("投递截止日期", "recruitment_end")):
                item = evidence.get(field) or {}
                st.caption(f"{label}：{item.get('status', '待确认')} · 原文：{item.get('quote') or '未找到'}")
            link_url = st.text_input(
                "招聘 / 投递链接", value=result.get("link_url") or "",
                placeholder="https://...", key=f"draft_link_{widget_suffix}",
            )
            link_evidence = evidence.get("link_url") or {}
            st.caption(
                f"链接：{link_evidence.get('status', '待确认')} · "
                f"原文：{link_evidence.get('quote') or '未找到'}。请自行核实网站身份。"
            )
            company_info = st.text_area(
                "公司信息", value=result.get("company_info") or "", height=100, key=f"draft_info_{widget_suffix}"
            )
            jd = st.text_area("JD", value=result.get("jd") or "", height=140, key=f"draft_jd_{widget_suffix}")
            for label, field in (("公司信息", "company_info"), ("JD", "jd")):
                item = evidence.get(field) or {}
                st.caption(f"{label}：{item.get('status', '待确认')}；请核对是否属于当前岗位。")
            confirm_manual_dates = st.checkbox(
                "我已核对手动填写或修改的日期与所属岗位",
                key=f"draft_date_confirm_{widget_suffix}",
            )
        if preview_rows:
            st.write("**时间线事件与岗位归属**")
            edited_events = st.data_editor(
                pd.DataFrame(preview_rows),
                hide_index=True,
                num_rows="fixed",
                width="stretch",
                key=f"draft_events_{version}",
                disabled=["核对状态"],
                column_config={
                    "岗位归属": st.column_config.SelectboxColumn(
                        "岗位归属", options=["未确定归属", *event_labels], required=True,
                    ),
                },
            )
            st.caption("未确定归属的事件默认不导入。改动归属、日期或事件类型时，请勾选“人工确认”；只有当前岗位且勾选“导入”的事件会保存。")
        else:
            edited_events = pd.DataFrame(preview_rows)
        saved = st.form_submit_button("确认保存当前岗位与时间线", use_container_width=True, disabled=not result)

    if st.button("结束本次核对并清除草稿", key=f"finish_review_{version}"):
        st.session_state.pop("unified_draft", None)
        st.session_state["clear_unified_input"] = True
        st.rerun()

    if not saved:
        return
    try:
        start_date = _date_or_none(start)
        end_date = _date_or_none(end)
        if (
            (start_date and start_date != result.get("recruitment_start"))
            or (end_date and end_date != result.get("recruitment_end"))
        ) and not confirm_manual_dates:
            raise ValueError("手动填写或修改了日期，请先核对所属岗位并勾选确认。")
        if start_date and end_date and start_date > end_date:
            raise ValueError("投递截止日期不能早于招聘开始日期。")
        if target == "新建职位" and (not company.strip() or not role.strip()):
            raise ValueError("新建职位需要公司和岗位。请在草稿中补全。")
        link_url = validate_link_url(link_url)

        prepared_events = []
        for index, row in enumerate(edited_events.to_dict("records")):
            if not bool(row.get("导入")):
                continue
            if _text(row.get("岗位归属")) != event_labels[draft_index]:
                continue
            original = preview_rows[index]
            changed = any(
                _text(row.get(field)) != _text(original.get(field))
                for field in ("岗位归属", "event_type", "event_date", "deadline_at", "deadline_kind", "feedback_score")
            )
            if changed and not bool(row.get("人工确认")):
                raise ValueError("修改事件归属、日期或类型后，请勾选该行的“人工确认”。")
            event_type = _text(row.get("event_type"))
            if event_type not in EVENT_TYPES:
                raise ValueError(f"未知事件类型：{event_type}")
            event_date = _date_or_none(row.get("event_date"))
            deadline_at = _deadline_or_none(row.get("deadline_at"))
            if not event_date and deadline_at:
                event_date = deadline_at[:10]
            if not event_date:
                raise ValueError(f"“{event_type}”缺少日期；请补全日期或取消勾选。")
            kind = _text(row.get("deadline_kind")) or None
            if deadline_at and kind not in {"官方截止", "自设截止", "建议跟进"}:
                raise ValueError(f"“{event_type}”有截止时间，请选择截止性质。")
            if kind and not deadline_at:
                raise ValueError(f"“{event_type}”有截止性质，但没有截止时间。")
            score_raw = _text(row.get("feedback_score"))
            score = None
            if score_raw:
                score_number = float(score_raw)
                if not score_number.is_integer() or not 1 <= score_number <= 5 or event_type != "面试完成":
                    raise ValueError(f"“{event_type}”的面试自评需为 1–5 的整数，且只用于面试完成。")
                score = int(score_number)
            quote = _text(row.get("source_quote"))
            if not quote:
                raise ValueError(f"“{event_type}”缺少原文依据；请取消导入，或通过手动录入添加。")
            if quote and quote not in draft["source_text"]:
                raise ValueError(f"“{event_type}”的原文依据不在总输入中，请保留原文或清空该列。")
            prepared_events.append(
                {
                    "event_type": event_type,
                    "event_date": event_date,
                    "details": _text(row.get("details")),
                    "deadline_at": deadline_at,
                    "deadline_kind": kind,
                    "feedback_score": score,
                    "source_quote": quote,
                }
            )

        application_id, imported = save_reviewed_intake(
            target=target,
            company=company.strip(),
            role=role.strip(),
            company_info=company_info.strip(),
            jd=jd.strip(),
            link_url=link_url,
            recruitment_start=start_date,
            recruitment_end=end_date,
            events=prepared_events,
            source_text=draft["source_text"],
            db_path=DB_PATH,
        )
        draft.setdefault("saved_targets", {})[draft_index] = application_id
        st.session_state["flash_success"] = (
            f"已保存第 {draft_index + 1} 个岗位，导入 {imported} 条新事件。"
            "可继续切换并核对其他岗位草稿。"
        )
        _rerun_after_change(application_id)
    except (ValueError, TypeError) as exc:
        st.error(str(exc))


def _edit_events(application_id: int, events: list[dict]) -> None:
    if not events:
        return
    with st.expander("修改或删除已有事件", expanded=False):
        rows = [
            {
                "id": event["id"],
                "event_type": event["event_type"],
                "event_date": event["event_date"] or "",
                "details": event["details"] or "",
                "deadline_at": event["deadline_at"] or "",
                "deadline_kind": event["deadline_kind"] or "",
                "source": event["source"] or "",
                "source_quote": event["source_quote"] or "",
                "feedback_score": event["feedback_score"],
            }
            for event in events
        ]
        edited = st.data_editor(
            pd.DataFrame(rows),
            disabled=["id"],
            hide_index=True,
            num_rows="fixed",
            width="stretch",
            key=f"event_editor_{application_id}_{st.session_state.get('event_table_version', 0)}",
        )
        if st.button("保存表格修改", key=f"save_events_{application_id}"):
            try:
                updates = []
                for row in edited.to_dict("records"):
                    event_type = _text(row["event_type"])
                    if event_type not in EVENT_TYPES:
                        raise ValueError(f"未知事件类型：{event_type}")
                    event_date = _date_or_none(row["event_date"])
                    if not event_date:
                        raise ValueError(f"事件 #{row['id']} 缺少发生日期。")
                    deadline_at = _deadline_or_none(row["deadline_at"])
                    kind = _text(row["deadline_kind"]) or None
                    if deadline_at and kind not in ("官方截止", "自设截止", "建议跟进"):
                        raise ValueError(f"事件 #{row['id']} 的截止性质无效。")
                    if not deadline_at and kind:
                        raise ValueError(f"事件 #{row['id']} 有截止性质但没有截止时间。")
                    score_raw = _text(row["feedback_score"])
                    if score_raw:
                        score_number = float(score_raw)
                        if not score_number.is_integer():
                            raise ValueError(f"事件 #{row['id']} 的面试自评需为 1–5 的整数。")
                        score = int(score_number)
                    else:
                        score = None
                    if score is not None and (score < 1 or score > 5 or event_type != "面试完成"):
                        raise ValueError(f"事件 #{row['id']} 的面试自评无效。")
                    updates.append(
                        (
                            int(row["id"]),
                            {
                                "event_type": event_type,
                                "event_date": event_date,
                                "details": _text(row["details"]),
                                "deadline_at": deadline_at,
                                "deadline_kind": kind,
                                "source": _text(row["source"]),
                                "source_quote": _text(row["source_quote"]),
                                "feedback_score": score,
                            },
                        )
                    )
                for event_id, fields in updates:
                    database.update_event(event_id, fields, db_path=DB_PATH)
                _rerun_after_change()
            except (ValueError, TypeError) as exc:
                st.error(str(exc))

        event_ids = [item["id"] for item in events]
        delete_id = st.selectbox(
            "删除事件",
            event_ids,
            format_func=lambda event_id: next(
                f"#{item['id']} {item['event_date']} {item['event_type']}" for item in events if item["id"] == event_id
            ),
            key=f"delete_event_select_{application_id}",
        )
        if st.button("删除所选事件", key=f"delete_event_button_{application_id}"):
            database.delete_event(delete_id, db_path=DB_PATH)
            _rerun_after_change()


def main() -> None:
    st.set_page_config(
        page_title="求职时间线 Agent",
        page_icon="📅",
        layout="wide",
    )
    inject_styles()
    database.init_db(db_path=DB_PATH)
    st.markdown(
        """
        <div class="hero">
          <div>
            <div class="hero-kicker">JOB SEARCH COMMAND CENTER</div>
            <h1>求职时间线</h1>
            <p>集中整理岗位、截止时间和面试进展，把下一步放在眼前。</p>
          </div>
          <div class="hero-badge">AI 辅助整理 · 本地保存</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    success_message = st.session_state.pop("flash_success", None)
    if success_message:
        st.success(success_message)
    pending_view = st.session_state.pop("pending_workspace_view", None)
    if pending_view in {"岗位看板", "求职者资料", "简历制作"}:
        st.session_state["workspace_view"] = pending_view
    st.segmented_control(
        "工作区", ["岗位看板", "求职者资料", "简历制作"],
        key="workspace_view", default="岗位看板",
    )

    jobs = database.list_applications(db_path=DB_PATH)
    candidate_profile = database.get_candidate_profile(db_path=DB_PATH)
    pending_selection = st.session_state.pop("pending_selection", None)
    if pending_selection is not None and any(job["id"] == pending_selection for job in jobs):
        st.session_state["selected_application"] = pending_selection
    elif "selected_application" in st.session_state and not any(
        job["id"] == st.session_state["selected_application"] for job in jobs
    ):
        del st.session_state["selected_application"]
    with st.sidebar:
        st.markdown("## 求职看板")
        st.caption("选择岗位，并在主区域顶部切换工作区。")
        with st.expander("求职者资料概览", expanded=False):
            if candidate_profile:
                st.write(f"**{candidate_profile.get('name') or '未填写姓名'}**")
                if candidate_profile.get("summary"):
                    st.caption(candidate_profile["summary"])
                if candidate_profile.get("skills"):
                    st.write(" · ".join(candidate_profile["skills"]))
                st.caption(
                    f"教育 {len(candidate_profile.get('education') or [])} 条 · "
                    f"工作 {len(candidate_profile.get('experiences') or [])} 条 · "
                    f"实习 {len(candidate_profile.get('internships') or [])} 条 · "
                    f"项目 {len(candidate_profile.get('projects') or [])} 条"
                )
            else:
                st.caption("在主页面的求职者资料区填写一次，之后随时更新。")
        render_llm_settings()
        with st.expander("AI 用量与限制", expanded=False):
            llm_usage = daily_usage_snapshot()
            token_ratio = min(1.0, llm_usage["total_tokens"] / llm_usage["token_budget"])
            st.progress(token_ratio)
            st.caption(
                f"今日 {llm_usage['total_tokens']:,} / {llm_usage['token_budget']:,} tokens"
            )
            st.caption(
                f"调用 {llm_usage['calls']} / {llm_usage['call_budget']} 次 · "
                f"单次输入最多 {llm_usage['max_input_chars']:,} 字符"
            )
            if llm_usage["reserved_calls"]:
                st.caption(
                    f"其中 {llm_usage['reserved_calls']} 次请求暂未取得实际用量，"
                    f"已预留 {llm_usage['reserved_tokens']:,} tokens。"
                )
        with st.expander("Agent Skills", expanded=False):
            for skill in list_skills():
                st.write(f"**{skill.title}**")
                st.caption(skill.description)
                limits = " · ".join(
                    f"{item.label} ≤ {item.max_chars:,} 字符" for item in skill.input_limits
                )
                st.caption(f"{limits} · 输出 ≤ {skill.max_output_tokens:,} tokens")
                st.caption("发送给模型：" + "、".join(skill.external_data))
            for skill in list_local_document_skills():
                st.write(f"**{skill.title}**")
                st.caption(skill.description)
                size = (
                    f" · 单文件 ≤ {skill.max_file_bytes // 1024 // 1024} MB"
                    if skill.max_file_bytes else ""
                )
                st.caption("格式：" + "、".join(skill.formats) + size + " · 本地处理")
        render_backup_controls(DB_PATH)
        if jobs:
            job_by_id = {job["id"]: job for job in jobs}
            selected_id = st.selectbox(
                "选择职位",
                list(job_by_id),
                format_func=lambda job_id: f"{job_by_id[job_id]['company']} · {job_by_id[job_id]['role']}",
                key="selected_application",
            )
        else:
            selected_id = None
            st.info("在主页面粘贴信息，或使用手动新建。")
        with st.expander("示例数据"):
            st.caption("加入 4 个不同日期范围的岗位，用来测试裁剪、缩放和拖拽。重复点击不会重复创建。")
            if st.button("加载示例数据", use_container_width=True):
                sample_ids = load_sample_data(DB_PATH, today=TODAY)
                st.session_state["flash_success"] = "已加载 4 组时间线示例数据。"
                _rerun_after_change(sample_ids[0])
    llm_config = configured_llm()
    view = st.session_state.get("workspace_view", "岗位看板")
    if view == "求职者资料":
        render_candidate_profile(
            candidate_profile, db_path=DB_PATH, llm_config=llm_config,
        )
        return
    if view == "简历制作":
        if selected_id is None:
            st.info("先在岗位看板添加一个岗位，再到这里制作定制简历。")
            return
        job = database.get_application(selected_id, db_path=DB_PATH)
        if job:
            render_resume_workflow(job, db_path=DB_PATH, llm_config=llm_config)
        return

    _unified_input(jobs, selected_id, llm_config=llm_config)
    _create_job(first_job=not jobs)
    _portfolio_overview(jobs)

    if selected_id is None:
        return

    job = database.get_application(selected_id, db_path=DB_PATH)
    if not job:
        st.warning("职位不存在，请重新选择。")
        return
    events = database.list_events(selected_id, db_path=DB_PATH)
    timeline = domain.build_timeline(events, today=TODAY, now=NOW)
    stage = domain.current_stage(events, today=TODAY)
    outlook = domain.assess_outlook(events, today=TODAY)

    st.subheader(f"{job['company']} · {job['role']}")
    if job.get("recruitment_end"):
        st.caption(f"投递截止：{job['recruitment_end']}")
    if job.get("link_url"):
        st.link_button("打开招聘 / 投递链接", job["link_url"])
        st.caption("打开后请核对网站身份、岗位和实际截止时间。")
    pending_deadlines = [
        row["deadline_at"]
        for row in timeline
        if row.get("status") in {"待办", "今日截止"} and row.get("deadline_at")
    ]
    next_deadline = min(pending_deadlines).replace("T", " ") if pending_deadlines else "暂无"
    col1, col2, col3 = st.columns(3)
    col1.metric("当前阶段", stage)
    col2.metric("最近待办截止", next_deadline)
    col3.metric("机会评估", outlook.get("level", "信息不足"))

    st.markdown("### 时间线")
    if timeline:
        st.dataframe(pd.DataFrame(_timeline_display_rows(timeline)), hide_index=True, width="stretch")
        overdue = [row for row in timeline if row.get("status") == "截止时间已过"]
        if overdue:
            st.warning(f"有 {len(overdue)} 项截止时间已过，请核对实际状态。")
        st.download_button(
            "下载时间线 CSV",
            data=_csv_bytes(timeline),
            file_name=f"{job['company']}_{job['role']}_时间线.csv",
            mime="text/csv",
        )
    else:
        st.info("还没有事件。请添加投递、面试或截止时间。")

    st.markdown("### 下一步与机会评估")
    if outlook.get("reasons"):
        for reason in outlook["reasons"]:
            st.write(f"• {reason}")
    st.caption(outlook.get("uncertainty") or "评估仅供整理进度参考。")

    _add_event(selected_id)
    _edit_events(selected_id, events)
    _job_details(job)

    history = database.list_intake_entries(selected_id, db_path=DB_PATH)
    if history:
        with st.expander("查看原始输入记录"):
            for entry in history:
                st.caption(entry["created_at"])
                st.text_area(
                    "原始输入",
                    value=entry["input_text"],
                    height=130,
                    disabled=True,
                    key=f"intake_history_{entry['id']}",
                )

    with st.sidebar.expander("删除当前职位"):
        st.caption("将同时删除该职位的所有事件。")
        if st.button("确认删除职位", key=f"delete_job_{selected_id}"):
            database.delete_application(selected_id, db_path=DB_PATH)
            _rerun_after_change()


if __name__ == "__main__":
    main()
