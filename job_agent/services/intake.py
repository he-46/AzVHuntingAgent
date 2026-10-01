"""Use case for committing a reviewed unified-intake draft."""

from __future__ import annotations

import database


def _sync_recruitment_event(
    application_id: int,
    event_type: str,
    event_date: str | None,
    *,
    db_path: str,
) -> None:
    managed = [
        event
        for event in database.list_events(application_id, db_path=db_path)
        if event["event_type"] == event_type and event["source"] == "用户录入的招聘信息"
    ]
    fields = {
        "event_date": event_date,
        "deadline_at": event_date if event_type == "投递截止" else None,
        "deadline_kind": "官方截止" if event_type == "投递截止" else None,
    }
    if event_date:
        if managed:
            database.update_event(managed[0]["id"], fields, db_path=db_path)
        else:
            database.add_event(
                application_id,
                event_type=event_type,
                event_date=event_date,
                deadline_at=fields["deadline_at"],
                deadline_kind=fields["deadline_kind"],
                source="用户录入的招聘信息",
                db_path=db_path,
            )
    else:
        for event in managed:
            database.delete_event(event["id"], db_path=db_path)


def _save_reviewed_intake_unchecked(
    *,
    target: str | int,
    company: str,
    role: str,
    company_info: str,
    jd: str,
    link_url: str,
    recruitment_start: str | None,
    recruitment_end: str | None,
    events: list[dict],
    source_text: str,
    db_path: str,
) -> tuple[int, int]:
    """Create/update one application and persist its reviewed events."""
    if target == "新建职位":
        application_id = database.create_application(
            company=company,
            role=role,
            company_info=company_info,
            jd=jd,
            link_url=link_url,
            recruitment_start=recruitment_start,
            recruitment_end=recruitment_end,
            db_path=db_path,
        )
        _sync_recruitment_event(application_id, "招聘开始", recruitment_start, db_path=db_path)
        _sync_recruitment_event(application_id, "投递截止", recruitment_end, db_path=db_path)
    else:
        application_id = int(target)
        current_job = database.get_application(application_id, db_path=db_path)
        if current_job is None:
            raise ValueError("保存位置中的职位已不存在，请重新选择。")
        updates = {
            key: value
            for key, value in {
                "company": company,
                "role": role,
                "company_info": company_info,
                "jd": jd,
                "link_url": link_url,
                "recruitment_start": recruitment_start,
                "recruitment_end": recruitment_end,
            }.items()
            if value
        }
        effective_start = recruitment_start or current_job.get("recruitment_start")
        effective_end = recruitment_end or current_job.get("recruitment_end")
        if effective_start and effective_end and effective_start > effective_end:
            raise ValueError("更新后的投递截止日期不能早于招聘开始日期。")
        if updates:
            database.update_application(application_id, updates, db_path=db_path)
        if recruitment_start:
            _sync_recruitment_event(application_id, "招聘开始", recruitment_start, db_path=db_path)
        if recruitment_end:
            _sync_recruitment_event(application_id, "投递截止", recruitment_end, db_path=db_path)

    existing = database.list_events(application_id, db_path=db_path)
    imported = 0
    for event in events:
        duplicate = any(
            prior["event_type"] == event["event_type"]
            and prior["event_date"] == event["event_date"]
            and (
                event["event_type"] in {"招聘开始", "投递截止"}
                or prior["source_quote"] == event["source_quote"]
            )
            for prior in existing
        )
        if duplicate:
            continue
        database.add_event(
            application_id,
            event_type=event["event_type"],
            event_date=event["event_date"],
            details=event["details"],
            deadline_at=event["deadline_at"],
            deadline_kind=event["deadline_kind"],
            source="总输入（用户确认）",
            source_quote=event["source_quote"],
            feedback_score=event["feedback_score"],
            db_path=db_path,
        )
        existing.append(event)
        imported += 1
    database.add_intake_entry(application_id, source_text, db_path=db_path)
    return application_id, imported


def save_reviewed_intake(
    *,
    target: str | int,
    company: str,
    role: str,
    company_info: str,
    jd: str,
    link_url: str = "",
    recruitment_start: str | None,
    recruitment_end: str | None,
    events: list[dict],
    source_text: str,
    db_path: str,
) -> tuple[int, int]:
    """Save a reviewed job, its events and source without touching the profile."""
    with database.atomic(db_path):
        result = _save_reviewed_intake_unchecked(
            target=target,
            company=company,
            role=role,
            company_info=company_info,
            jd=jd,
            link_url=link_url,
            recruitment_start=recruitment_start,
            recruitment_end=recruitment_end,
            events=events,
            source_text=source_text,
            db_path=db_path,
        )
        return result


def sync_recruitment_event(
    application_id: int, event_type: str, event_date: str | None, *, db_path: str
) -> None:
    """Public use case used by manual job editing."""
    _sync_recruitment_event(application_id, event_type, event_date, db_path=db_path)
