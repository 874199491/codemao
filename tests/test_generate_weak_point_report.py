import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from generate_weak_point_report import fallback_knowledge, is_useful_knowledge


class SharedKnowledgeValidationTests(unittest.TestCase):
    def test_local_fallback_is_not_accepted_as_ai_generated_cache(self):
        self.assertFalse(is_useful_knowledge(fallback_knowledge("逻辑与运算")))


if __name__ == "__main__":
    unittest.main()
