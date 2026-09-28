"""Deterministic timeline and outlook rules for job applications.

The functions in this module only use events supplied by the user or extracted
from a source. They never manufacture a recruiting deadline or a probability.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping


STAGE_BY_EVENT = {
    "已投递": "简历筛选中",
    "测评邀请": "待测评",
    "测评完成": "等待测评反馈",
    "面试邀请": "待面试",
    "面试完成": "等待面试反馈",
    "HR反馈": "等待后续通知",
    "录取通知": "已录取",
    "拒绝": "未录取",
}


def _as_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError as exc:
            raise ValueError(f"日期必须采用 ISO 格式：{value}") from exc
    raise TypeError("日期必须是 ISO 字符串或 date 对象")


def _today(value: date | str | None) -> date:
    return _as_date(value) or date.today()


def _as_deadline_datetime(value: Any) -> datetime | None:
    """Return a datetime only when the source specifies a time of day."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and len(value) > 10 and value[10] in {"T", "t", " "}:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"截止时间必须采用 ISO 格式：{value}") from exc
    return None


def _deadline_has_passed(value: Any, now: datetime) -> bool:
    deadline = _as_deadline_datetime(value)
    if deadline is None:
        return False
    if deadline.tzinfo is None and now.tzinfo is not None:
        # The user entered local wall time; use the caller's local timezone.
        deadline = deadline.replace(tzinfo=now.tzinfo)
    elif deadline.tzinfo is not None and now.tzinfo is None:
        # A naive caller clock is interpreted in the deadline's timezone.
        now = now.replace(tzinfo=deadline.tzinfo)
    return deadline < now


def _ordered(events: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted(
        events,
        key=lambda event: (
            _as_date(event.get("event_date")) or date.min,
            int(event.get("id") or 0),
        ),
    )


def current_stage(
    events: list[Mapping[str, Any]], today: date | str | None = None
) -> str:
    """Return the latest observed recruiting stage as of the reference date."""
    stage = "待投递"
    reference_date = _today(today)
    for event in _ordered(events):
        event_date = _as_date(event.get("event_date"))
        if event_date is not None and event_date > reference_date:
            continue
        stage = STAGE_BY_EVENT.get(str(event.get("event_type", "")), stage)
    return stage


def _task_completion_date(
    ordered_events: list[Mapping[str, Any]],
    position: int,
    reference_date: date,
) -> date | None:
    """Recognize only a completion that can be matched to this deadline row."""
    event = ordered_events[position]
    event_type = event.get("event_type")
    event_date = _as_date(event.get("event_date"))
    if event.get("deadline_kind") == "建议跟进":
        return None
    if event_type in {"已投递", "测评完成", "面试完成"}:
        return event_date if event_date is not None and event_date <= reference_date else None
    if event_type in {"岗位发布", "投递截止"}:
        submission_dates = [
            submission_date
            for later in ordered_events
            if later.get("event_type") == "已投递"
            if (submission_date := _as_date(later.get("event_date"))) is not None
            and submission_date <= reference_date
        ]
        return min(submission_dates) if submission_dates else None
    successor = {"测评邀请": "测评完成", "面试邀请": "面试完成"}.get(
        event_type
    )
    if successor is None:
        return None
    for later in ordered_events[position + 1 :]:
        later_date = _as_date(later.get("event_date"))
        if later_date is not None and later_date > reference_date:
            break
        if later.get("event_type") == event_type:
            break  # A newer invitation begins another round.
        if later.get("event_type") == successor:
            return later_date
    return None


def build_timeline(
    events: list[Mapping[str, Any]],
    today: date | str | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Build display rows without inferring missing event or deadline dates."""
    reference_date = _as_date(today) or (now.date() if now is not None else date.today())
    stage = "待投递"
    rows: list[dict[str, Any]] = []
    ordered_events = _ordered(events)
    for position, event in enumerate(ordered_events):
        event_date = _as_date(event.get("event_date"))
        deadline = _as_date(event.get("deadline_at"))
        if event_date is not None and event_date <= reference_date:
            stage = STAGE_BY_EVENT.get(str(event.get("event_type", "")), stage)

        if deadline is not None:
            completion_date = _task_completion_date(
                ordered_events, position, reference_date
            )
            if completion_date is not None:
                if completion_date < deadline:
                    status = "已完成"
                elif completion_date > deadline:
                    status = "截止后完成"
                elif _as_deadline_datetime(event.get("deadline_at")) is not None:
                    status = "已完成（时刻未核实）"
                else:
                    status = "已完成"
            elif deadline < reference_date or (
                deadline == reference_date
                and now is not None
                and _deadline_has_passed(event.get("deadline_at"), now)
            ):
                status = "截止时间已过"
            elif deadline == reference_date:
                status = "今日截止"
            else:
                status = "待办"
        elif event_date is not None and event_date > reference_date:
            status = "未开始"
        else:
            status = "已发生"

        rows.append(
            {
                "id": event.get("id"),
                "date": event_date.isoformat() if event_date else None,
                "event_type": event.get("event_type", ""),
                "stage": stage,
                "details": event.get("details", ""),
                "deadline_at": event.get("deadline_at"),
                "deadline_kind": event.get("deadline_kind"),
                "source": event.get("source", ""),
                "source_quote": event.get("source_quote", ""),
                "status": status,
            }
        )
    return rows


def assess_outlook(
    events: list[Mapping[str, Any]], today: date | str | None = None
) -> dict[str, Any]:
    """Assess qualitative outlook from recorded milestones and self ratings.

    Waiting time is presented as an action cue, not as evidence of a lower
    chance. A numeric probability would require calibrated outcome data.
    """
    reference_date = _today(today)
    observed = [
        event
        for event in _ordered(events)
        if (_as_date(event.get("event_date")) or date.min) <= reference_date
    ]
    uncertainty = "仅基于已记录的进度和主观反馈；缺少企业历史录取数据，无法给出可靠百分比。"
    for event in reversed(observed):
        event_type = event.get("event_type")
        if event_type == "录取通知":
            return {
                "level": "已录取",
                "reasons": ["已记录录取通知。"],
                "uncertainty": "请以企业正式通知为准。",
            }
        if event_type == "拒绝":
            return {
                "level": "未录取",
                "reasons": ["已记录拒绝通知。"],
                "uncertainty": "请以企业正式通知为准。",
            }

    reasons: list[str] = []
    interview_done = [
        event for event in observed if event.get("event_type") == "面试完成"
    ]
    scores: list[float] = []
    for event in observed:
        if event.get("event_type") not in {"面试完成", "HR反馈"}:
            continue
        score = event.get("feedback_score")
        if score is None or score == "":
            continue
        try:
            numeric = float(score)
        except (TypeError, ValueError):
            continue
        if 1 <= numeric <= 5:
            scores.append(numeric)

    if scores and interview_done:
        average = sum(scores) / len(scores)
        if average >= 4:
            level = "机会偏高"
        elif average <= 2:
            level = "机会偏低"
        else:
            level = "机会中等"
        reasons.append(f"已完成面试；记录的反馈评分平均为 {average:.1f}/5。")
    else:
        level = "信息不足"
        if interview_done:
            reasons.append("已完成面试，但尚无可用的表现评分。")
        elif any(event.get("event_type") == "面试邀请" for event in observed):
            reasons.append("已收到面试邀请，尚无面试表现记录。")
        elif any(event.get("event_type") == "已投递" for event in observed):
            reasons.append("已投递，尚无面试表现记录。")
        else:
            reasons.append("尚无投递或面试记录。")

    if interview_done:
        latest_interview = max(
            _as_date(event.get("event_date")) or date.min for event in interview_done
        )
        days_waiting = (reference_date - latest_interview).days
        if days_waiting >= 7 and not any(
            event.get("event_type") == "HR反馈"
            and (_as_date(event.get("event_date")) or date.min) > latest_interview
            for event in observed
        ):
            reasons.append(
                f"距最近一次面试已有 {days_waiting} 天，可考虑礼貌跟进；等待时长本身不能判断录取结果。"
            )

    return {"level": level, "reasons": reasons, "uncertainty": uncertainty}
