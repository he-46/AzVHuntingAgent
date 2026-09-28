"""Idempotent demonstration data for the job timeline prototype."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import database


SAMPLE_PREFIX = "示例·"


def _day(today: date, offset: int) -> str:
    return (today + timedelta(days=offset)).isoformat()


def load_sample_data(
    db_path: str | Path = database.DEFAULT_DB_PATH,
    *,
    today: date | None = None,
) -> list[int]:
    """Add four timeline scenarios once and return their application ids."""

    today = today or date.today()
    specifications = [
        {
            "company": f"{SAMPLE_PREFIX}远航科技",
            "role": "AI 产品经理",
            "start": -62,
            "end": 18,
            "info": "企业服务 AI 团队，流程包含业务面、案例面和终面。",
            "jd": "负责智能招聘产品规划、用户研究和跨团队交付。",
            "events": [
                ("招聘开始", -62, "秋招职位开放", None, None, None),
                ("已投递", -36, "官网完成投递", None, None, None),
                ("面试邀请", -12, "收到业务一面邀请", -9, "官方截止", None),
                ("面试完成", -9, "业务一面沟通顺畅", None, None, 4),
                ("面试邀请", -3, "案例面邀请", 4, "官方截止", None),
            ],
        },
        {
            "company": f"{SAMPLE_PREFIX}云杉数据",
            "role": "数据分析师",
            "start": -14,
            "end": 24,
            "info": "零售数据平台，重视 SQL、业务分析和数据表达。",
            "jd": "搭建业务指标体系，完成专题分析并推动策略落地。",
            "events": [
                ("招聘开始", -14, "岗位发布", None, None, None),
                ("已投递", -8, "内推投递", None, None, None),
                ("测评邀请", -2, "在线数据分析测评", 3, "官方截止", None),
            ],
        },
        {
            "company": f"{SAMPLE_PREFIX}星环机器人",
            "role": "算法工程师",
            "start": -31,
            "end": -1,
            "info": "机器人感知团队，技术面后由 HR 沟通录用方案。",
            "jd": "负责多模态感知算法训练、评测及部署优化。",
            "events": [
                ("招聘开始", -31, "岗位开放", None, None, None),
                ("已投递", -26, "完成投递", None, None, None),
                ("面试完成", -16, "技术一面表现良好", None, None, 4),
                ("面试完成", -8, "技术终面完成", None, None, 5),
                ("录取通知", -2, "已收到录用通知", None, None, None),
            ],
        },
        {
            "company": f"{SAMPLE_PREFIX}青岚金融",
            "role": "风险策略实习生",
            "start": 8,
            "end": 37,
            "info": "金融科技风控团队，未来批次尚未开始。",
            "jd": "参与风险策略分析、监控和实验评估。",
            "events": [
                ("招聘开始", 8, "下一批招聘启动", None, None, None),
                ("投递截止", 37, "网申截止", 37, "官方截止", None),
            ],
        },
    ]

    existing = {
        (job["company"], job["role"]): job["id"]
        for job in database.list_applications(db_path=db_path)
    }
    ids: list[int] = []
    for spec in specifications:
        key = (spec["company"], spec["role"])
        application_id = existing.get(key)
        if application_id is None:
            application_id = database.create_application(
                spec["company"],
                spec["role"],
                jd=spec["jd"],
                company_info=spec["info"],
                recruitment_start=_day(today, spec["start"]),
                recruitment_end=_day(today, spec["end"]),
                db_path=db_path,
            )
            for event_type, offset, details, deadline_offset, deadline_kind, score in spec["events"]:
                database.add_event(
                    application_id,
                    event_type=event_type,
                    event_date=_day(today, offset),
                    details=details,
                    deadline_at=_day(today, deadline_offset) if deadline_offset is not None else None,
                    deadline_kind=deadline_kind,
                    feedback_score=score,
                    source="内置示例数据",
                    db_path=db_path,
                )
        ids.append(application_id)
    return ids
