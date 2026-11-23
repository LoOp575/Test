import unittest

from backend.stochastic_engine import brownian_motion, detect_patterns, random_walk


class StochasticEngineTests(unittest.TestCase):
    def test_random_walk_reproducible_and_variance(self):
        a = random_walk(start_price=100, sigma=0.02, periods=10, paths=200, seed=7)
        b = random_walk(start_price=100, sigma=0.02, periods=10, paths=200, seed=7)
        self.assertEqual(a, b)
        self.assertAlmostEqual(a["summary"]["theoreticalVariance"], 4.0)

    def test_brownian_variance_formula(self):
        out = brownian_motion(horizon=2, sigma=3, steps=20, paths=200, seed=9)
        self.assertAlmostEqual(out["summary"]["theoreticalVariance"], 18.0)

    def test_pattern_detector_accepts_series(self):
        data = [(-1) ** i * (0.01 + i * 0.0001) for i in range(40)]
        out = detect_patterns(data, lag=4)
        self.assertTrue(out["ok"])
        self.assertIn(out["summary"]["verdict"], {
            "PERSISTENCE_EVIDENCE", "MEAN_REVERSION_EVIDENCE", "NO_CLEAR_PATTERN"
        })


if __name__ == "__main__":
    unittest.main()
