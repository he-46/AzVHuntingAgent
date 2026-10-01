"""Tests for the reviewed-intake application use case."""

import tempfile
import unittest
from pathlib import Path

import database
from job_agent.services.intake import save_reviewed_intake


class IntakeServiceTests(unittest.TestCase):
    def test_failed_event_rolls_back_job_without_changing_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            db_path = str(Path(temporary) / "applications.db")
            database.save_candidate_profile({"name": "张三"}, db_path=db_path)
            invalid_event = {
                "event_type": "已投递",
                "event_date": None,
                "details": "",
                "deadline_at": None,
                "deadline_kind": None,
                "feedback_score": None,
                "source_quote": "",
            }
            with self.assertRaises(ValueError):
                save_reviewed_intake(
                    target="新建职位", company="示例公司", role="开发",
                    company_info="", jd="", recruitment_start=None,
                    recruitment_end=None, events=[invalid_event],
                    source_text="岗位资料", db_path=db_path,
                )
            self.assertEqual(database.list_applications(db_path), [])
            self.assertEqual(database.get_candidate_profile(db_path)["name"], "张三")

    def test_create_then_update_keeps_events_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            db_path = str(Path(temporary) / "applications.db")
            event = {
                "event_type": "已投递",
                "event_date": "2026-09-20",
                "details": "官网投递",
                "deadline_at": None,
                "deadline_kind": None,
                "feedback_score": None,
                "source_quote": "2026年9月20日完成投递",
            }
            application_id, imported = save_reviewed_intake(
                target="新建职位",
                company="示例公司",
                role="开发",
                company_info="",
                jd="负责开发",
                recruitment_start="2026-09-01",
                recruitment_end="2026-10-01",
                events=[event],
                source_text="2026年9月20日完成投递",
                db_path=db_path,
            )
            self.assertEqual(imported, 1)

            _, imported_again = save_reviewed_intake(
                target=application_id,
                company="示例公司",
                role="开发",
                company_info="",
                jd="负责开发",
                recruitment_start=None,
                recruitment_end=None,
                events=[event],
                source_text="2026年9月20日完成投递",
                db_path=db_path,
            )
            self.assertEqual(imported_again, 0)
            self.assertEqual(len(database.list_events(application_id, db_path)), 3)
            self.assertEqual(len(database.list_intake_entries(application_id, db_path)), 2)


if __name__ == "__main__":
    unittest.main()
