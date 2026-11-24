import random
import unittest

from mockdata import physics
from mockdata.scenarios import SCENARIOS, ScenarioParams, field_at


def _estimate(b_mt: float, seed: int = 1, **odmr) -> physics.FitResult:
    freqs = physics.frequencies(2.80e9, 1e6, 141)
    adc = physics.simulate_spectrum(b_mt, freqs, physics.OdmrParams(**odmr), random.Random(seed))
    return physics.estimate_field(freqs, [physics.adc_to_voltage(a) for a in adc])


class EstimateFieldTest(unittest.TestCase):
    def test_recovers_field_across_range(self):
        for b in (0.5, 1.0, 1.5, 2.0):
            fit = _estimate(b)
            self.assertTrue(fit.fit_ok, b)
            self.assertAlmostEqual(fit.b_mt, b, delta=0.03)

    def test_estimate_is_unbiased(self):
        errors = [_estimate(1.0, seed=s).b_mt - 1.0 for s in range(100)]
        self.assertAlmostEqual(sum(errors) / len(errors), 0.0, delta=0.003)

    def test_dips_are_symmetric_around_d(self):
        fit = _estimate(1.0)
        self.assertAlmostEqual((fit.f_minus_hz + fit.f_plus_hz) / 2, physics.D_HZ, delta=1e6)

    def test_reports_contrast_and_linewidth(self):
        fit = _estimate(1.0)
        self.assertAlmostEqual(fit.contrast, 0.03, delta=0.01)
        self.assertAlmostEqual(fit.linewidth_hz, 8e6, delta=3e6)

    def test_fails_without_dips(self):
        fit = _estimate(1.0, contrast=0.0)
        self.assertFalse(fit.fit_ok)
        self.assertIsNone(fit.b_mt)

    def test_same_seed_same_spectrum(self):
        freqs = physics.frequencies(2.80e9, 1e6, 141)
        p = physics.OdmrParams()
        a = physics.simulate_spectrum(1.0, freqs, p, random.Random(7))
        b = physics.simulate_spectrum(1.0, freqs, p, random.Random(7))
        self.assertEqual(a, b)

    def test_adc_is_clamped(self):
        self.assertEqual(physics.voltage_to_adc(5.0), physics.ADC_MAX)
        self.assertEqual(physics.voltage_to_adc(-1.0), 0)


class ScenarioTest(unittest.TestCase):
    def test_all_scenarios_non_negative(self):
        p = ScenarioParams(b0_mt=0.1, amplitude_mt=1.0)
        for name in SCENARIOS:
            for t in range(0, 600, 10):
                self.assertGreaterEqual(field_at(name, t, p), 0.0)

    def test_magnet_peaks_halfway(self):
        p = ScenarioParams()
        self.assertAlmostEqual(field_at("magnet", 300, p), p.b0_mt + p.amplitude_mt)


if __name__ == "__main__":
    unittest.main()
