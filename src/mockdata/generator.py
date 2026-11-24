"""Mock ESP32: turns a B(t) scenario into MQTT sweep payloads."""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from . import db, physics
from .mqtt_format import build_payload
from .scenarios import ScenarioParams, field_at


@dataclass
class SweepConfig:
    freq_start_hz: float = 2.80e9
    freq_stop_hz: float = 2.94e9
    freq_step_hz: float = 1e6
    power_dbm: float = -10.0

    @property
    def n_points(self) -> int:
        return int(round((self.freq_stop_hz - self.freq_start_hz) / self.freq_step_hz)) + 1


def make_payload(
    b_mt: float,
    started_at: datetime,
    cfg: SweepConfig,
    odmr: physics.OdmrParams,
    rng: random.Random,
) -> dict:
    freqs = physics.frequencies(cfg.freq_start_hz, cfg.freq_step_hz, cfg.n_points)
    adc = physics.simulate_spectrum(b_mt, freqs, odmr, rng)
    return build_payload(started_at, cfg.freq_start_hz, cfg.freq_step_hz, cfg.power_dbm, adc)


def all_params(scenario: ScenarioParams, odmr: physics.OdmrParams, cfg: SweepConfig, **extra) -> dict:
    return {"scenario": asdict(scenario), "odmr": asdict(odmr), "sweep": asdict(cfg), **extra}


def generate(
    conn,
    scenario: str,
    sparams: ScenarioParams,
    odmr: physics.OdmrParams,
    cfg: SweepConfig,
    start: datetime,
    interval_s: float,
    seed: int,
) -> tuple[int, int]:
    """Generate sweeps covering sparams.duration_s and ingest them. Returns (mock_run_id, n_sweeps)."""
    rng = random.Random(seed)
    count = int(sparams.duration_s // interval_s)
    params = all_params(sparams, odmr, cfg, interval_s=interval_s, start=start.isoformat())
    run_id = db.create_mock_run(conn, scenario, params, seed)
    for i in range(count):
        t = i * interval_s
        b_true = field_at(scenario, t, sparams)
        payload = make_payload(b_true, start + timedelta(seconds=t), cfg, odmr, rng)
        sweep_id, _ = db.ingest_payload(conn, payload)
        db.insert_truth(conn, sweep_id, run_id, b_true)
    conn.commit()
    return run_id, count
