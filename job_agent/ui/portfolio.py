"""Portfolio overview and interactive timeline UI."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Callable

import altair as alt
import pandas as pd
import streamlit as st

import database
import domain


def timeline_display_rows(timeline: list[dict]) -> list[dict]:
    return [
        {
            "日期": row.get("date") or "",
            "事件": row.get("event_type") or "",
            "阶段": row.get("stage") or "",
            "说明": row.get("details") or "",
            "截止时间": row.get("deadline_at") or "",
            "截止性质": row.get("deadline_kind") or "",
            "来源": row.get("source") or "",
            "原文依据": row.get("source_quote") or "",
            "状态": row.get("status") or "",
        }
        for row in timeline
    ]


def _job_focus_time(events: list[dict], timeline: list[dict]) -> str:
    pending = sorted(
        str(row["deadline_at"]).replace("T", " ")
        for row in timeline
        if row.get("status") in {"待办", "今日截止"} and row.get("deadline_at")
    )
    if pending:
        return pending[0]
    event_dates = sorted(event["event_date"] for event in events if event.get("event_date"))
    return event_dates[-1] if event_dates else "暂无"


def _job_priority_date(job: dict, timeline: list[dict], today: date) -> date:
    candidates: list[date] = []
    for row in timeline:
        for raw in (row.get("date"), str(row.get("deadline_at") or "")[:10]):
            if raw:
                try:
                    candidates.append(date.fromisoformat(raw))
                except ValueError:
                    pass
    for field in ("recruitment_start", "recruitment_end"):
        if job.get(field):
            candidates.append(date.fromisoformat(job[field]))
    return min(candidates, key=lambda item: (abs((item - today).days), item < today)) if candidates else date.max


def portfolio_snapshots(
    jobs: list[dict], *, db_path: str, today: date, now: datetime
) -> list[dict]:
    snapshots = []
    for job in jobs:
        events = database.list_events(job["id"], db_path=db_path)
        timeline = domain.build_timeline(events, today=today, now=now)
        snapshots.append(
            {
                "job": job,
                "events": events,
                "timeline": timeline,
                "stage": domain.current_stage(events, today=today),
                "outlook": domain.assess_outlook(events, today=today),
                "focus_time": _job_focus_time(events, timeline),
                "row_label": f"{job['company']} · {job['role']} 〔{job['id']}〕",
                "priority_date": _job_priority_date(job, timeline, today),
            }
        )
    return sorted(
        snapshots,
        key=lambda item: (
            abs((item["priority_date"] - today).days),
            item["priority_date"] < today,
            item["job"]["company"],
        ),
    )


def portfolio_chart(snapshots: list[dict], *, today: date) -> alt.LayerChart | None:
    bar_rows = []
    row_order = [snapshot["row_label"] for snapshot in snapshots]
    visible_start = today - timedelta(days=28)
    visible_end = today + timedelta(days=42)
    for snapshot in snapshots:
        job = snapshot["job"]
        timeline = sorted(
            (row for row in snapshot["timeline"] if row.get("date")),
            key=lambda row: (row["date"], row.get("event_type") or ""),
        )
        if not timeline:
            continue
        boundary_dates = [date.fromisoformat(row["date"]) for row in timeline]
        deadline_dates = [
            date.fromisoformat(str(row["deadline_at"])[:10])
            for row in timeline
            if row.get("deadline_at")
        ]
        for raw in (job.get("recruitment_start"), job.get("recruitment_end")):
            if raw:
                boundary_dates.append(date.fromisoformat(raw))
        start = min(boundary_dates)
        end = max(boundary_dates + deadline_dates)
        if end <= start:
            end = start + timedelta(days=7)
        nearest_event = min(
            timeline,
            key=lambda row: abs((date.fromisoformat(row["date"]) - today).days),
        )
        bar_rows.append(
            {
                "岗位": snapshot["row_label"],
                "公司": job["company"],
                "岗位名称": job["role"],
                "开始": start.isoformat(),
                "结束": end.isoformat(),
                "当前进度": snapshot["stage"],
                "最近事件": nearest_event.get("event_type") or "",
                "最近事件日期": nearest_event.get("date") or "",
                "最近说明": nearest_event.get("details") or "",
                "最近截止": snapshot["focus_time"],
            }
        )
    if not bar_rows:
        return None

    data = pd.DataFrame(bar_rows)
    x_scale = alt.Scale(domain=[visible_start.isoformat(), visible_end.isoformat()])
    y_axis = alt.Y(
        "岗位:N",
        sort=row_order,
        title=None,
        axis=alt.Axis(labelLimit=260, labelPadding=12, ticks=False, domain=False),
    )
    bars = alt.Chart(data).mark_bar(
        size=38, cornerRadius=5, clip=True, stroke="#FFFFFF", strokeWidth=2
    ).encode(
        x=alt.X(
            "开始:T",
            title="时间（滚轮缩放，按住拖拽平移）",
            scale=x_scale,
            axis=alt.Axis(format="%m-%d", grid=True, labelAngle=0, tickCount=8),
        ),
        x2=alt.X2("结束:T"),
        y=y_axis,
        color=alt.Color(
            "当前进度:N",
            title="当前进度",
            scale=alt.Scale(
                range=["#3559E0", "#167D9A", "#238A60", "#8B5CF6", "#C2551A", "#B4235A", "#586174"]
            ),
        ),
        tooltip=[
            "公司:N", "岗位名称:N",
            alt.Tooltip("开始:T", title="阶段开始", format="%Y-%m-%d"),
            alt.Tooltip("结束:T", title="阶段结束", format="%Y-%m-%d"),
            "当前进度:N", "最近事件:N", "最近事件日期:N", "最近说明:N", "最近截止:N",
        ],
    )
    # The scale-bound interval exposes its current x domain through the
    # selection parameter. Recalculate the center of each bar's visible
    # intersection whenever the user pans or zooms, so the stage label follows
    # the viewport until the complete bar leaves it.
    labels = (
        alt.Chart(data)
        .transform_calculate(
            _bar_start_ms="toNumber(toDate(datum['开始']))",
            _bar_end_ms="toNumber(toDate(datum['结束']))",
            _window_start_ms=(
                "timeline_window && timeline_window['开始'] "
                f"? toNumber(toDate(timeline_window['开始'][0])) "
                f": toNumber(toDate('{visible_start.isoformat()}'))"
            ),
            _window_end_ms=(
                "timeline_window && timeline_window['开始'] "
                f"? toNumber(toDate(timeline_window['开始'][1])) "
                f": toNumber(toDate('{visible_end.isoformat()}'))"
            ),
            _visible_start_ms=(
                "datum._bar_start_ms > datum._window_start_ms "
                "? datum._bar_start_ms : datum._window_start_ms"
            ),
            _visible_end_ms=(
                "datum._bar_end_ms < datum._window_end_ms "
                "? datum._bar_end_ms : datum._window_end_ms"
            ),
            _dynamic_label_at="toDate((datum._visible_start_ms + datum._visible_end_ms) / 2)",
        )
        .transform_filter(
            "datum._bar_start_ms <= datum._window_end_ms "
            "&& datum._bar_end_ms >= datum._window_start_ms"
        )
        .mark_text(
            clip=True,
            color="white",
            fontSize=12,
            fontWeight=700,
            baseline="middle",
        )
        .encode(
            x=alt.X("_dynamic_label_at:T", scale=x_scale),
            y=y_axis,
            text=alt.Text("当前进度:N"),
        )
    )
    today_line = alt.Chart(pd.DataFrame({"今天": [today.isoformat()]})).mark_rule(
        color="#E11D48", strokeDash=[6, 4], strokeWidth=3
    ).encode(
        x=alt.X("今天:T", scale=x_scale),
        tooltip=[alt.Tooltip("今天:T", title="今天", format="%Y-%m-%d")],
    )
    today_label = alt.Chart(
        pd.DataFrame({"今天": [today.isoformat()], "岗位": [row_order[0]], "文字": ["今天"]})
    ).mark_text(color="#E11D48", fontWeight=700, dy=-27).encode(
        x=alt.X("今天:T", scale=x_scale), y=alt.Y("岗位:N", sort=row_order), text="文字:N"
    )
    zoom = alt.selection_interval(bind="scales", encodings=["x"], name="timeline_window")
    return (
        (bars + labels + today_line + today_label)
        .add_params(zoom)
        .properties(height=max(220, 60 * len(row_order)))
        .configure_view(stroke=None)
    )


def render_portfolio_overview(
    jobs: list[dict],
    *,
    db_path: str,
    today: date,
    now: datetime,
    on_select: Callable[[int], None],
) -> None:
    if not jobs:
        return
    snapshots = portfolio_snapshots(jobs, db_path=db_path, today=today, now=now)
    terminal_stages = {"已录取", "未录取"}
    active_count = sum(snapshot["stage"] not in terminal_stages for snapshot in snapshots)
    interview_count = sum("面试" in snapshot["stage"] for snapshot in snapshots)
    deadline_count = sum(
        1
        for snapshot in snapshots
        for row in snapshot["timeline"]
        if row.get("deadline_at")
        and row.get("status") in {"待办", "今日截止"}
        and 0 <= (date.fromisoformat(str(row["deadline_at"])[:10]) - today).days <= 7
    )
    st.markdown('<div class="section-kicker">APPLICATION OVERVIEW</div>', unsafe_allow_html=True)
    st.markdown(
        f"""
        <div class="summary-grid">
          <div class="summary-card"><span>全部岗位</span><strong>{len(snapshots)}</strong></div>
          <div class="summary-card"><span>进行中</span><strong>{active_count}</strong></div>
          <div class="summary-card"><span>面试阶段</span><strong>{interview_count}</strong></div>
          <div class="summary-card"><span>7 天内截止</span><strong>{deadline_count}</strong></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("### 岗位时间轴")
    st.caption("最近日期优先。每个彩色框是一项岗位时间区间，框内是当前进度；红线是今天，默认位于横轴约 40%。滚轮缩放，按住图表左右拖拽。")
    chart = portfolio_chart(snapshots, today=today)
    if chart is not None:
        st.altair_chart(chart, width="stretch")
    else:
        st.info("这些岗位还没有日期事件。展开岗位后可手动补录。")

    st.markdown("#### 岗位列表")
    for snapshot in snapshots:
        job = snapshot["job"]
        header = f"{job['company']} ｜ {job['role']} ｜ {snapshot['stage']} ｜ {snapshot['focus_time']}"
        with st.expander(header):
            col1, col2, col3 = st.columns(3)
            col1.metric("当前进度", snapshot["stage"])
            col2.metric("最近节点", snapshot["focus_time"])
            col3.metric("机会评估", snapshot["outlook"].get("level", "信息不足"))
            if job.get("link_url"):
                st.link_button("打开招聘 / 投递链接", job["link_url"])
                st.caption("请在原网站核对岗位与截止时间。")
            if job.get("company_info"):
                st.write("**公司信息**")
                st.write(job["company_info"])
            if job.get("jd"):
                st.write("**JD**")
                st.write(job["jd"])
            if snapshot["timeline"]:
                st.write("**详细时间线**")
                st.dataframe(pd.DataFrame(timeline_display_rows(snapshot["timeline"])), hide_index=True, width="stretch")
            else:
                st.info("暂无时间线事件。")
            for reason in snapshot["outlook"].get("reasons") or []:
                st.write(f"• {reason}")
            if st.button("选择此岗位进行编辑", key=f"select_from_overview_{job['id']}"):
                on_select(job["id"])
