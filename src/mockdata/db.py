"""SQLite access: schema, ingest (what the Pi does with an MQTT payload) and queries."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from importlib import resources

from . import physics
from .mqtt_format import format_ts, validate_payload

ALL_TABLES = ("mock_truth", "mock_runs", "measurements", "sweep_points", "sweeps")


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    sql = resources.files(__package__).joinpath("schema.sql").read_text()
    conn.executescript(sql)
    conn.commit()


def ingest_payload(conn: sqlite3.Connection, payload: dict) -> tuple[int, physics.FitResult]:
    """Store a sweep and its points, compute B and store the measurement. Does not commit."""
    validate_payload(payload)
    n = payload["n_points"]
    start = payload["freq_start_hz"]
    step = payload["freq_step_hz"]
    freqs = physics.frequencies(start, step, n)
    voltages = [physics.adc_to_voltage(a) for a in payload["adc"]]

    cur = conn.execute(
        "INSERT INTO sweeps (started_at, freq_start_hz, freq_stop_hz, freq_step_hz, n_points, power_dbm)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (payload["started_at"], start, freqs[-1], step, n, payload["power_dbm"]),
    )
    sweep_id = cur.lastrowid
    conn.executemany(
        "INSERT INTO sweep_points (sweep_id, idx, freq_hz, adc_raw, voltage) VALUES (?, ?, ?, ?, ?)",
        [
            (sweep_id, i, f, a, round(v, 6))
            for i, (f, a, v) in enumerate(zip(freqs, payload["adc"], voltages))
        ],
    )

    fit = physics.estimate_field(freqs, voltages)
    conn.execute(
        "INSERT INTO measurements (sweep_id, timestamp, b_mt, f_minus_hz, f_plus_hz, contrast,"
        " linewidth_hz, fit_ok) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            sweep_id,
            payload["started_at"],
            fit.b_mt,
            fit.f_minus_hz,
            fit.f_plus_hz,
            fit.contrast,
            fit.linewidth_hz,
            int(fit.fit_ok),
        ),
    )
    return sweep_id, fit


def create_mock_run(conn: sqlite3.Connection, scenario: str, params: dict, seed: int) -> int:
    cur = conn.execute(
        "INSERT INTO mock_runs (created_at, scenario, params_json, seed) VALUES (?, ?, ?, ?)",
        (format_ts(datetime.now(timezone.utc)), scenario, json.dumps(params, sort_keys=True), seed),
    )
    return cur.lastrowid


def insert_truth(conn: sqlite3.Connection, sweep_id: int, mock_run_id: int, b_true_mt: float) -> None:
    conn.execute(
        "INSERT INTO mock_truth (sweep_id, mock_run_id, b_true_mt) VALUES (?, ?, ?)",
        (sweep_id, mock_run_id, b_true_mt),
    )


def _range_clause(column: str, since: str | None, until: str | None) -> tuple[str, list]:
    parts, args = [], []
    if since:
        parts.append(f"{column} >= ?")
        args.append(since)
    if until:
        parts.append(f"{column} <= ?")
        args.append(until)
    return (" WHERE " + " AND ".join(parts)) if parts else "", args


def list_measurements(
    conn: sqlite3.Connection,
    since: str | None = None,
    until: str | None = None,
    limit: int | None = None,
) -> list[sqlite3.Row]:
    where, args = _range_clause("m.timestamp", since, until)
    sql = (
        "SELECT m.*, t.b_true_mt FROM measurements m"
        " LEFT JOIN mock_truth t ON t.sweep_id = m.sweep_id" + where + " ORDER BY m.timestamp"
    )
    if limit:
        sql += " LIMIT ?"
        args.append(limit)
    return conn.execute(sql, args).fetchall()


def list_sweeps(
    conn: sqlite3.Connection,
    since: str | None = None,
    until: str | None = None,
    limit: int | None = None,
) -> list[sqlite3.Row]:
    where, args = _range_clause("started_at", since, until)
    sql = "SELECT * FROM sweeps" + where + " ORDER BY started_at"
    if limit:
        sql += " LIMIT ?"
        args.append(limit)
    return conn.execute(sql, args).fetchall()


def get_sweep(conn: sqlite3.Connection, sweep_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM sweeps WHERE id = ?", (sweep_id,)).fetchone()


def get_sweep_points(conn: sqlite3.Connection, sweep_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT idx, freq_hz, adc_raw, voltage FROM sweep_points WHERE sweep_id = ? ORDER BY idx",
        (sweep_id,),
    ).fetchall()


def get_measurement(conn: sqlite3.Connection, sweep_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT m.*, t.b_true_mt FROM measurements m"
        " LEFT JOIN mock_truth t ON t.sweep_id = m.sweep_id WHERE m.sweep_id = ?",
        (sweep_id,),
    ).fetchone()


def delete_range(conn: sqlite3.Connection, since: str | None, until: str | None) -> int:
    where, args = _range_clause("started_at", since, until)
    cur = conn.execute("DELETE FROM sweeps" + where, args)
    conn.execute(
        "DELETE FROM mock_runs WHERE id NOT IN (SELECT DISTINCT mock_run_id FROM mock_truth)"
    )
    conn.commit()
    return cur.rowcount


def reset(conn: sqlite3.Connection) -> None:
    for table in ALL_TABLES:
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.execute("VACUUM")
