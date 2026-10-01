"""The resume studio uses the shared profile with per-job selections."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import database


class ResumeProfileSelectionTests(unittest.TestCase):
    def test_jd_suggestions_can_be_changed_without_leaking_unselected_items(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            db_path = str(Path(temporary) / "applications.db")
            database.save_candidate_profile({
                "name": "张三",
                "contact": "zhangsan@example.com",
                "education": ["统计学本科"],
                "skills": ["Python", "SQL", "Java"],
                "projects": ["用户增长分析项目", "Java 后端项目"],
            }, db_path=db_path)
            application_id = database.create_application(
                "星辰科技", "数据分析师",
                jd="需要 Python，负责用户增长分析。", db_path=db_path,
            )
            with patch.dict(os.environ, {"JOB_AGENT_DB_PATH": db_path}):
                app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=30)
                self.assertFalse(app.exception)
                skill_key = f"resume_profile_skills_{application_id}"
                project_key = f"resume_profile_projects_{application_id}"
                self.assertEqual(app.get_by_key(skill_key).value, ["Python"])
                self.assertEqual(app.get_by_key(project_key).value, ["用户增长分析项目"])

                app.get_by_key(skill_key).set_value(["SQL"]).run()
                app.get_by_key(project_key).set_value([]).run()
                material = next(
                    widget.value for widget in app.text_area
                    if widget.label == "本次送入模型的简历素材"
                )
                self.assertIn("SQL", material)
                self.assertIn("zhangsan@example.com", material)
                self.assertNotIn("Python", material)
                self.assertNotIn("Java", material)
                self.assertNotIn("用户增长分析项目", material)


if __name__ == "__main__":
    unittest.main()
