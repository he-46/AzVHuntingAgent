"""Tests for the explicit application skill registry."""

import unittest

from job_agent.skills import get_skill, list_skills


class SkillRegistryTests(unittest.TestCase):
    def test_registered_skills_have_unique_operations_and_limits(self) -> None:
        skills = list_skills()
        self.assertEqual({skill.key for skill in skills}, {"intake_extraction", "resume_generation"})
        self.assertEqual(len({skill.operation for skill in skills}), len(skills))
        self.assertTrue(all(skill.max_output_tokens > 0 for skill in skills))
        self.assertTrue(all(skill.input_limits for skill in skills))

    def test_resume_limits_are_available_by_name(self) -> None:
        resume = get_skill("resume_generation")
        self.assertEqual(resume.input_limit("source_resume").max_chars, 20_000)
        self.assertEqual(resume.input_limit("jd").max_chars, 15_000)

    def test_unknown_skill_is_rejected(self) -> None:
        with self.assertRaisesRegex(KeyError, "Unknown skill"):
            get_skill("unreviewed_plugin")


if __name__ == "__main__":
    unittest.main()
