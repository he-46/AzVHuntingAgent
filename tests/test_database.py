"""SQLite persistence tests using a temporary database."""

import tempfile
import unittest
import sqlite3
from contextlib import closing
from pathlib import Path

from database import (
    add_event,
    add_intake_entry,
    add_resume_version,
    get_candidate_profile,
    create_application,
    delete_application,
    get_application,
    init_db,
    list_applications,
    list_events,
    list_intake_entries,
    list_resume_versions,
    save_candidate_profile,
    update_application,
    update_event,
)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "nested" / "test.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_update_and_cascade_delete(self):
        application_id = create_application(
            "示例公司",
            "产品经理",
            recruitment_end="2026-10-01",
            db_path=self.db_path,
            company_info="主营企业软件",
        )
        event_id = add_event(
            application_id,
            "面试邀请",
            "2026-09-27",
            deadline_at="2026-09-30T18:00:00+08:00",
            deadline_kind="官方截止",
            source="招聘邮件",
            source_quote="请于 9 月 30 日前确认",
            db_path=self.db_path,
        )
        self.assertEqual(get_application(application_id, self.db_path)["company"], "示例公司")
        self.assertEqual(get_application(application_id, self.db_path)["company_info"], "主营企业软件")
        self.assertEqual(len(list_applications(self.db_path)), 1)
        self.assertEqual(list_events(application_id, self.db_path)[0]["id"], event_id)
        self.assertEqual(list_events(application_id, self.db_path)[0]["source"], "招聘邮件")

        update_application(
            application_id,
            {"role": "高级产品经理", "company_info": "主营企业软件与咨询"},
            self.db_path,
        )
        update_event(event_id, {"feedback_score": 4.5}, self.db_path)
        self.assertEqual(get_application(application_id, self.db_path)["role"], "高级产品经理")
        self.assertEqual(get_application(application_id, self.db_path)["company_info"], "主营企业软件与咨询")
        self.assertEqual(list_events(application_id, self.db_path)[0]["feedback_score"], 4.5)

        delete_application(application_id, self.db_path)
        self.assertIsNone(get_application(application_id, self.db_path))
        self.assertEqual(list_events(application_id, self.db_path), [])

    def test_existing_database_adds_company_info_column(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    """CREATE TABLE applications (
                        id INTEGER PRIMARY KEY,
                        company TEXT NOT NULL,
                        role TEXT NOT NULL,
                        jd TEXT NOT NULL DEFAULT '',
                        recruitment_start TEXT,
                        recruitment_end TEXT,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )"""
                )
                conn.execute("INSERT INTO applications(company, role) VALUES ('旧公司', '旧岗位')")
        init_db(self.db_path)
        self.assertEqual(list_applications(self.db_path)[0]["company_info"], "")
        application_id = list_applications(self.db_path)[0]["id"]
        entry_id = add_intake_entry(application_id, "旧申请的新输入", self.db_path)
        self.assertEqual(list_intake_entries(application_id, self.db_path)[0]["id"], entry_id)
        with closing(sqlite3.connect(self.db_path)) as conn:
            versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
        self.assertEqual(versions, [1, 2, 3, 4, 5])

    def test_candidate_profile_round_trip(self):
        save_candidate_profile(
            {
                "name": "张三",
                "summary": "数据分析方向",
                "education": ["示例大学统计学"],
                "experiences": [],
                "internships": ["零售公司数据实习"],
                "projects": ["用户增长分析项目"],
                "skills": ["Python", "SQL"],
            },
            source_text="原始简历",
            db_path=self.db_path,
        )
        profile = get_candidate_profile(self.db_path)
        self.assertEqual(profile["name"], "张三")
        self.assertEqual(profile["skills"], ["Python", "SQL"])
        self.assertEqual(profile["source_text"], "原始简历")

    def test_intake_entries_persist_and_cascade_delete(self):
        application_id = create_application("示例公司", "开发", db_path=self.db_path)
        first_text = "  JD：开发工程师\n9 月 30 日投递截止  "
        first_id = add_intake_entry(application_id, first_text, self.db_path)
        second_id = add_intake_entry(application_id, "已完成一面，等待反馈", self.db_path)

        entries = list_intake_entries(application_id, self.db_path)
        self.assertEqual([entry["id"] for entry in entries], [first_id, second_id])
        self.assertEqual(entries[0]["input_text"], first_text)
        self.assertEqual(entries[0]["application_id"], application_id)
        self.assertTrue(entries[0]["created_at"])

        with self.assertRaises(ValueError):
            add_intake_entry(application_id, " \n\t ", self.db_path)
        self.assertEqual(len(list_intake_entries(application_id, self.db_path)), 2)

        delete_application(application_id, self.db_path)
        self.assertEqual(list_intake_entries(application_id, self.db_path), [])
        with closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM intake_entries").fetchone()[0], 0)

    def test_resume_versions_persist_and_cascade_delete(self):
        application_id = create_application("示例公司", "开发", db_path=self.db_path)
        version_id = add_resume_version(
            application_id,
            "基础简历内容",
            "# 定制简历\n真实经历",
            "匹配 Python",
            "缺少云服务证据",
            self.db_path,
        )
        versions = list_resume_versions(application_id, self.db_path)
        self.assertEqual(versions[0]["id"], version_id)
        self.assertEqual(versions[0]["tailored_resume"], "# 定制简历\n真实经历")
        with self.assertRaises(ValueError):
            add_resume_version(application_id, "", "草稿", db_path=self.db_path)

        delete_application(application_id, self.db_path)
        self.assertEqual(list_resume_versions(application_id, self.db_path), [])

    def test_rejects_invalid_fields_and_dates(self):
        application_id = create_application("示例公司", "开发", db_path=self.db_path)
        with self.assertRaises(ValueError):
            update_application(application_id, {"id": 100}, self.db_path)
        with self.assertRaises(ValueError):
            add_event(application_id, "已投递", "2026-02-30", db_path=self.db_path)
        with self.assertRaises(ValueError):
            add_event(application_id, "已投递", "2026-09-27", feedback_score=6, db_path=self.db_path)
        self.assertEqual(list_events(application_id, self.db_path), [])

    def test_recruitment_start_is_valid_informational_event(self):
        application_id = create_application("示例公司", "开发", db_path=self.db_path)
        event_id = add_event(
            application_id,
            "招聘开始",
            "2026-09-01",
            source="用户录入的招聘信息",
            db_path=self.db_path,
        )
        self.assertEqual(list_events(application_id, self.db_path)[0]["id"], event_id)


if __name__ == "__main__":
    unittest.main()
