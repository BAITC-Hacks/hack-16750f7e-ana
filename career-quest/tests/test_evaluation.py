import unittest
from evaluation import run_evaluation


class EvaluationTest(unittest.TestCase):
    def test_all_business_invariants(self):
        result = run_evaluation()
        self.assertEqual(result["scenarios"], 12)
        self.assertEqual(result["constraint_violations"], 0, result["results"])


if __name__ == "__main__":
    unittest.main()
