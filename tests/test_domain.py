"""Behavior tests for the timeline and qualitative outlook rules."""

import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from domain import assess_outlook, build_timeline, current_stage


class DomainTests(unittest.TestCase):
    def test_stage_ignores_announcements_and_future_events(self):
        today = date.today()
        events = [
            {"id": 0, "event_type": "招聘开始", "event_date": "2020-01-01"},
            {"id": 1, "event_type": "岗位发布", "event_date": "2020-01-01"},
            {"id": 2, "event_type": "已投递", "event_date": "2020-01-02"},
            {"id": 3, "event_type": "投递截止", "event_date": "2020-01-03"},
            {
                "id": 4,
                "event_type": "面试完成",
                "event_date": (today + timedelta(days=2)).isoformat(),
            },
        ]
        self.assertEqual(current_stage(events, today=today), "简历筛选中")
        self.assertEqual(build_timeline(events, today=today)[0]["stage"], "待投递")

    def test_timeline_orders_events_and_distinguishes_deadline_kinds(self):
        events = [
            {
                "id": 2,
                "event_type": "面试邀请",
                "event_date": "2026-09-27",
                "deadline_at": "2026-09-30T18:00:00+08:00",
                "deadline_kind": "官方截止",
                "source": "招聘邮件",
                "source_quote": "请于 9 月 30 日前确认",
            },
            {
                "id": 1,
                "event_type": "已投递",
                "event_date": "2026-09-20",
                "details": "官网投递",
            },
        ]
        rows = build_timeline(events, today="2026-09-27")
        self.assertEqual([row["event_type"] for row in rows], ["已投递", "面试邀请"])
        self.assertEqual(rows[0]["stage"], "简历筛选中")
        self.assertEqual(rows[1]["stage"], "待面试")
        self.assertEqual(rows[1]["status"], "待办")
        self.assertEqual(rows[1]["deadline_kind"], "官方截止")
        self.assertEqual(rows[1]["source_quote"], "请于 9 月 30 日前确认")
        self.assertEqual(build_timeline(events, today="2026-10-01")[1]["status"], "截止时间已过")

    def test_outlook_uses_scores_but_not_waiting_time_as_negative_evidence(self):
        events = [
            {
                "event_type": "面试完成",
                "event_date": "2026-09-10",
                "feedback_score": 4.5,
            }
        ]
        outlook = assess_outlook(events, today="2026-09-27")
        self.assertEqual(outlook["level"], "机会偏高")
        self.assertTrue(any("17 天" in reason for reason in outlook["reasons"]))
        self.assertIn("无法给出可靠百分比", outlook["uncertainty"])

    def test_completed_deadline_does_not_look_overdue(self):
        events = [
            {
                "id": 1,
                "event_type": "面试邀请",
                "event_date": "2026-09-10",
                "deadline_at": "2026-09-15",
                "deadline_kind": "官方截止",
            },
            {
                "id": 2,
                "event_type": "面试完成",
                "event_date": "2026-09-14",
                "deadline_at": "2026-09-21",
                "deadline_kind": "建议跟进",
            },
        ]
        rows = build_timeline(events, today="2026-09-27")
        self.assertEqual(rows[0]["status"], "已完成")
        self.assertEqual(rows[1]["status"], "截止时间已过")

    def test_late_completion_is_recorded_as_late(self):
        events = [
            {
                "id": 1,
                "event_type": "投递截止",
                "event_date": "2026-09-15",
                "deadline_at": "2026-09-15",
                "deadline_kind": "官方截止",
            },
            {"id": 2, "event_type": "已投递", "event_date": "2026-09-16"},
        ]
        self.assertEqual(
            build_timeline(events, today="2026-09-27")[0]["status"], "截止后完成"
        )

    def test_timed_deadline_uses_supplied_local_clock(self):
        events = [
            {
                "event_type": "面试邀请",
                "event_date": "2026-09-26",
                "deadline_at": "2026-09-27T18:00",
                "deadline_kind": "官方截止",
            },
            {
                "event_type": "投递截止",
                "event_date": "2026-09-27",
                "deadline_at": "2026-09-27",
                "deadline_kind": "官方截止",
            },
        ]
        zone = ZoneInfo("Asia/Shanghai")
        before = datetime(2026, 9, 27, 17, 0, tzinfo=zone)
        after = datetime(2026, 9, 27, 19, 0, tzinfo=zone)
        self.assertEqual(build_timeline(events, now=before)[0]["status"], "今日截止")
        after_rows = build_timeline(events, now=after)
        self.assertEqual(after_rows[0]["status"], "截止时间已过")
        self.assertEqual(after_rows[1]["status"], "今日截止")

    def test_same_day_completion_cannot_prove_timed_cutoff_was_met(self):
        events = [
            {
                "event_type": "已投递",
                "event_date": "2026-09-27",
                "deadline_at": "2026-09-27T18:00",
                "deadline_kind": "官方截止",
            }
        ]
        now = datetime(2026, 9, 27, 19, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual(
            build_timeline(events, now=now)[0]["status"], "已完成（时刻未核实）"
        )

    def test_terminal_result_overrides_previous_assessment(self):
        events = [
            {"event_type": "面试完成", "event_date": "2026-09-10", "feedback_score": 1},
            {"event_type": "录取通知", "event_date": "2026-09-20"},
        ]
        self.assertEqual(assess_outlook(events, today="2026-09-27")["level"], "已录取")


if __name__ == "__main__":
    unittest.main()
