"""True magnetic field B(t) patterns used to drive the mock data."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class ScenarioParams:
    b0_mt: float = 1.0  # base field
    amplitude_mt: float = 0.5  # size of the variation
    period_s: float = 120.0  # period for sine / step length for steps
    duration_s: float = 600.0  # total run length (used by drift / magnet)


def constant(t: float, p: ScenarioParams) -> float:
    return p.b0_mt


def drift(t: float, p: ScenarioParams) -> float:
    return p.b0_mt + p.amplitude_mt * (t / p.duration_s)


def steps(t: float, p: ScenarioParams) -> float:
    return p.b0_mt + p.amplitude_mt * (int(t // p.period_s) % 4)


def sine(t: float, p: ScenarioParams) -> float:
    return p.b0_mt + p.amplitude_mt * math.sin(2 * math.pi * t / p.period_s)


def magnet(t: float, p: ScenarioParams) -> float:
    """A magnet approaching and moving away again, peaking halfway through."""
    centre = p.duration_s / 2
    width = p.duration_s / 6
    return p.b0_mt + p.amplitude_mt * math.exp(-(((t - centre) / width) ** 2))


SCENARIOS = {
    "constant": constant,
    "drift": drift,
    "steps": steps,
    "sine": sine,
    "magnet": magnet,
}


def field_at(name: str, t: float, params: ScenarioParams) -> float:
    return max(0.0, SCENARIOS[name](t, params))
