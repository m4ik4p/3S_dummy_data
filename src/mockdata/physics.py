"""ODMR physics: simulate an NV-centre spectrum and estimate B from it."""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass

D_HZ = 2.87e9  # zero-field splitting of the NV ground state
GAMMA_HZ_PER_MT = 28.0e6  # gyromagnetic ratio of the NV electron spin
ADC_MAX = 4095  # ESP32 12-bit ADC
V_REF = 3.3


@dataclass
class OdmrParams:
    baseline_v: float = 2.4  # photodiode voltage off resonance
    contrast: float = 0.03  # depth of each dip as a fraction of the baseline
    linewidth_hz: float = 8e6  # FWHM of each dip
    noise: float = 0.003  # gaussian noise std as a fraction of the baseline


@dataclass
class FitResult:
    b_mt: float | None
    f_minus_hz: float | None
    f_plus_hz: float | None
    contrast: float | None
    linewidth_hz: float | None
    fit_ok: bool


def frequencies(start_hz: float, step_hz: float, n_points: int) -> list[float]:
    return [start_hz + i * step_hz for i in range(n_points)]


def adc_to_voltage(adc: int) -> float:
    return adc * V_REF / ADC_MAX


def voltage_to_adc(v: float) -> int:
    return max(0, min(ADC_MAX, round(v / V_REF * ADC_MAX)))


def _lorentzian(f: float, f0: float, fwhm: float) -> float:
    return 1.0 / (1.0 + ((f - f0) / (fwhm / 2.0)) ** 2)


def resonances(b_mt: float) -> tuple[float, float]:
    shift = GAMMA_HZ_PER_MT * abs(b_mt)
    return D_HZ - shift, D_HZ + shift


def simulate_spectrum(
    b_mt: float, freqs: list[float], params: OdmrParams, rng: random.Random
) -> list[int]:
    """Photodiode ADC readings for one sweep under field b_mt."""
    f_minus, f_plus = resonances(b_mt)
    adc = []
    for f in freqs:
        dip = _lorentzian(f, f_minus, params.linewidth_hz) + _lorentzian(
            f, f_plus, params.linewidth_hz
        )
        v = params.baseline_v * (1.0 - params.contrast * dip)
        v += rng.gauss(0.0, params.noise * params.baseline_v)
        adc.append(voltage_to_adc(v))
    return adc


def _smooth(values: list[float], window: int = 5) -> list[float]:
    half = window // 2
    out = []
    for i in range(len(values)):
        chunk = values[max(0, i - half) : i + half + 1]
        out.append(sum(chunk) / len(chunk))
    return out


def _parabolic_min(freqs: list[float], values: list[float], i: int, half_width: int = 4) -> float:
    """Least-squares parabola over the 2*half_width+1 points around i; returns its vertex."""
    h = min(half_width, i, len(values) - 1 - i)
    if h < 1:
        return freqs[i]
    xs = list(range(-h, h + 1))  # symmetric window, so the fit coefficients decouple
    ys = values[i - h : i + h + 1]
    n = len(xs)
    sx2 = sum(x * x for x in xs)
    sx4 = sum(x**4 for x in xs)
    slope = sum(x * y for x, y in zip(xs, ys)) / sx2
    curv = (n * sum(x * x * y for x, y in zip(xs, ys)) - sx2 * sum(ys)) / (n * sx4 - sx2 * sx2)
    if curv <= 0:
        return freqs[i]
    offset = max(-h, min(h, -slope / (2 * curv)))
    step = freqs[i + 1] - freqs[i]
    return freqs[i] + offset * step


def _fwhm(freqs: list[float], values: list[float], i: int, half_level: float) -> float:
    def crossing(j: int, direction: int) -> float:
        while 0 <= j + direction < len(values) and values[j + direction] < half_level:
            j += direction
        k = j + direction
        if not 0 <= k < len(values):
            return freqs[j]
        # linear interpolation between j (below half level) and k (above)
        dv = values[k] - values[j]
        frac = (half_level - values[j]) / dv if dv else 0.0
        return freqs[j] + frac * (freqs[k] - freqs[j])

    return crossing(i, +1) - crossing(i, -1)


def estimate_field(freqs: list[float], voltages: list[float]) -> FitResult:
    """What the Raspberry Pi does: find both dips and convert their splitting to B."""
    failed = FitResult(None, None, None, None, None, False)
    if len(freqs) < 5 or len(freqs) != len(voltages):
        return failed

    smooth = _smooth(voltages)
    baseline = statistics.median(voltages)
    diffs = [b - a for a, b in zip(voltages, voltages[1:])]
    noise_std = statistics.pstdev(diffs) / math.sqrt(2)

    lower = [i for i, f in enumerate(freqs) if f < D_HZ]
    upper = [i for i, f in enumerate(freqs) if f >= D_HZ]
    if not lower or not upper:
        return failed

    i_minus = min(lower, key=lambda i: smooth[i])
    i_plus = min(upper, key=lambda i: smooth[i])

    depths = [baseline - smooth[i_minus], baseline - smooth[i_plus]]
    f_minus = _parabolic_min(freqs, voltages, i_minus)
    f_plus = _parabolic_min(freqs, voltages, i_plus)
    widths = [
        _fwhm(freqs, smooth, i, baseline - d / 2) for i, d in zip((i_minus, i_plus), depths)
    ]
    contrast = sum(depths) / 2 / baseline if baseline > 0 else None

    at_edge = i_minus == 0 or i_plus == len(freqs) - 1
    significant = all(d > 5 * noise_std and d > 0 for d in depths)
    fit_ok = significant and not at_edge and baseline > 0

    b_mt = max(0.0, (f_plus - f_minus) / (2 * GAMMA_HZ_PER_MT)) if fit_ok else None
    return FitResult(
        b_mt=b_mt,
        f_minus_hz=f_minus,
        f_plus_hz=f_plus,
        contrast=contrast,
        linewidth_hz=sum(widths) / 2,
        fit_ok=fit_ok,
    )
