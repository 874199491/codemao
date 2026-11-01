import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from generate_weak_point_report import (
    fallback_knowledge,
    is_useful_knowledge,
    question_key,
    select_display_questions,
)


class SharedKnowledgeValidationTests(unittest.TestCase):
    def test_local_fallback_is_not_accepted_as_ai_generated_cache(self):
        self.assertFalse(is_useful_knowledge(fallback_knowledge("逻辑与运算")))


class DisplayQuestionSelectionTests(unittest.TestCase):
    @staticmethod
    def question(index: int) -> dict:
        return {
            "type": 1,
            "description": f"第 {index} 道真实错题",
            "options": [],
            "knowledgeArr": ["测试知识点"],
        }

    def test_keeps_actual_count_when_fewer_than_two_questions(self):
        questions = [self.question(1)]
        self.assertEqual(select_display_questions(questions), questions)

    def test_uses_two_or_three_real_questions_and_is_stable(self):
        questions = [self.question(index) for index in range(1, 7)]
        first = select_display_questions(questions)
        second = select_display_questions(questions)
        self.assertIn(len(first), (2, 3))
        self.assertEqual([question_key(q) for q in first], [question_key(q) for q in second])
        self.assertTrue(set(map(question_key, first)).issubset(set(map(question_key, questions))))


if __name__ == "__main__":
    unittest.main()
