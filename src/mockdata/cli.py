"""mockdb: command-line interface for the NV-magnetometer mock database."""

from __future__ import annotations

import argparse
import contextlib
import csv
import json
import os
import random
import re
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone

from . import db, physics
from .generator import SweepConfig, all_params, generate, make_payload
from .mqtt_format import build_payload, format_ts, parse_ts
from .scenarios import SCENARIOS, ScenarioParams, field_at

DEFAULT_DB = "mock.db"
_DURATION_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*(ms|s|m|h|d)?\s*$")
_UNITS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400, None: 1}


def duration(value: str) -> float:
    match = _DURATION_RE.match(value)
    if not match:
        raise argparse.ArgumentTypeError(f"invalid duration {value!r} (use e.g. 500ms, 2s, 10m, 1h)")
    seconds = float(match.group(1)) * _UNITS[match.group(2)]
    if seconds <= 0:
        raise argparse.ArgumentTypeError("duration must be positive")
    return seconds


def timestamp(value: str) -> str:
    try:
        return format_ts(parse_ts(value))
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid timestamp {value!r} (use ISO-8601)")


def _fmt(value, spec: str = "") -> str:
    return "-" if value is None else format(value, spec)


def _mhz(hz) -> str:
    return "-" if hz is None else f"{hz / 1e6:.3f}"


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
    print("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(c.ljust(w) for c, w in zip(row, widths)))


def _open(args, init: bool = True):
    conn = db.connect(args.db)
    if init:
        db.init_schema(conn)
    return conn


def _output(path: str | None):
    return open(path, "w", newline="") if path else contextlib.nullcontext(sys.stdout)


def _model_params(args) -> tuple[ScenarioParams, physics.OdmrParams, SweepConfig]:
    sparams = ScenarioParams(
        b0_mt=args.b0, amplitude_mt=args.amplitude, period_s=args.period, duration_s=args.duration
    )
    odmr = physics.OdmrParams(
        baseline_v=args.baseline, contrast=args.contrast, linewidth_hz=args.linewidth, noise=args.noise
    )
    cfg = SweepConfig(
        freq_start_hz=args.freq_start,
        freq_stop_hz=args.freq_stop,
        freq_step_hz=args.freq_step,
        power_dbm=args.power,
    )
    if cfg.freq_stop_hz <= cfg.freq_start_hz or cfg.freq_step_hz <= 0:
        raise SystemExit("error: need freq-stop > freq-start and freq-step > 0")
    return sparams, odmr, cfg


# ── commands ────────────────────────────────────────────────────────────


def cmd_init(args) -> None:
    _open(args).close()
    print(f"Schema ready in {args.db}")


def cmd_generate(args) -> None:
    sparams, odmr, cfg = _model_params(args)
    start = parse_ts(args.start) if args.start else datetime.now(timezone.utc) - timedelta(seconds=args.duration)
    conn = _open(args)
    run_id, count = generate(conn, args.scenario, sparams, odmr, cfg, start, args.interval, args.seed)
    stats = conn.execute(
        "SELECT SUM(fit_ok), MIN(timestamp), MAX(timestamp) FROM measurements m"
        " JOIN mock_truth t ON t.sweep_id = m.sweep_id WHERE t.mock_run_id = ?",
        (run_id,),
    ).fetchone()
    conn.close()
    print(
        f"Mock run {run_id}: {count} sweeps x {cfg.n_points} points ({args.scenario}, seed {args.seed})\n"
        f"  {stats[1]} -> {stats[2]}, {stats[0] or 0}/{count} fits ok\n"
        f"  written to {args.db}"
    )


def cmd_live(args) -> None:
    sparams, odmr, cfg = _model_params(args)
    rng = random.Random(args.seed)
    conn = _open(args)
    params = all_params(sparams, odmr, cfg, interval_s=args.interval, live=True)
    run_id = db.create_mock_run(conn, args.scenario, params, args.seed)
    conn.commit()
    print(f"Live mock run {run_id}: one sweep every {args.interval}s, Ctrl-C to stop")
    t0 = time.monotonic()
    n = 0
    try:
        while args.count is None or n < args.count:
            t = time.monotonic() - t0
            b_true = field_at(args.scenario, t, sparams)
            payload = make_payload(b_true, datetime.now(timezone.utc), cfg, odmr, rng)
            sweep_id, fit = db.ingest_payload(conn, payload)
            db.insert_truth(conn, sweep_id, run_id, b_true)
            conn.commit()
            n += 1
            print(f"{payload['started_at']}  sweep {sweep_id}  B={_fmt(fit.b_mt, '.4f')} mT  (true {b_true:.4f})")
            if args.count is None or n < args.count:
                time.sleep(max(0.0, t0 + n * args.interval - time.monotonic()))
    except KeyboardInterrupt:
        print()
    finally:
        conn.close()
    print(f"Stopped after {n} sweeps")


def cmd_measurements_list(args) -> None:
    conn = _open(args)
    rows = db.list_measurements(conn, args.since, args.until, args.limit)
    conn.close()
    _print_table(
        ["sweep", "timestamp", "B (mT)", "B true", "f- (MHz)", "f+ (MHz)", "contrast", "FWHM (MHz)", "ok"],
        [
            [
                str(r["sweep_id"]),
                r["timestamp"],
                _fmt(r["b_mt"], ".4f"),
                _fmt(r["b_true_mt"], ".4f"),
                _mhz(r["f_minus_hz"]),
                _mhz(r["f_plus_hz"]),
                _fmt(r["contrast"], ".4f"),
                _mhz(r["linewidth_hz"]),
                "yes" if r["fit_ok"] else "NO",
            ]
            for r in rows
        ],
    )


def cmd_sweeps_list(args) -> None:
    conn = _open(args)
    rows = db.list_sweeps(conn, args.since, args.until, args.limit)
    conn.close()
    _print_table(
        ["id", "started_at", "start (MHz)", "stop (MHz)", "step (MHz)", "points", "power (dBm)"],
        [
            [
                str(r["id"]),
                r["started_at"],
                _mhz(r["freq_start_hz"]),
                _mhz(r["freq_stop_hz"]),
                _mhz(r["freq_step_hz"]),
                str(r["n_points"]),
                _fmt(r["power_dbm"], ".1f"),
            ]
            for r in rows
        ],
    )


def cmd_sweeps_show(args) -> None:
    conn = _open(args)
    sweep = db.get_sweep(conn, args.id)
    if sweep is None:
        raise SystemExit(f"error: sweep {args.id} not found")
    points = db.get_sweep_points(conn, args.id)
    m = db.get_measurement(conn, args.id)
    conn.close()

    volts = [p["voltage"] for p in points]
    lowest = min(points, key=lambda p: p["voltage"])
    print(f"Sweep {sweep['id']} at {sweep['started_at']}")
    print(
        f"  range      {_mhz(sweep['freq_start_hz'])} - {_mhz(sweep['freq_stop_hz'])} MHz,"
        f" step {_mhz(sweep['freq_step_hz'])} MHz, {sweep['n_points']} points, {sweep['power_dbm']} dBm"
    )
    print(
        f"  photodiode min {min(volts):.4f} V @ {_mhz(lowest['freq_hz'])} MHz,"
        f" max {max(volts):.4f} V, median {statistics.median(volts):.4f} V"
    )
    if m:
        print(f"  fit        {'ok' if m['fit_ok'] else 'FAILED'}")
        print(f"  f- / f+    {_mhz(m['f_minus_hz'])} / {_mhz(m['f_plus_hz'])} MHz")
        print(f"  contrast   {_fmt(m['contrast'], '.4f')}, FWHM {_mhz(m['linewidth_hz'])} MHz")
        print(f"  B          {_fmt(m['b_mt'], '.4f')} mT (true {_fmt(m['b_true_mt'], '.4f')} mT)")
    if args.points:
        print()
        _print_table(
            ["idx", "freq (MHz)", "adc", "voltage (V)"],
            [[str(p["idx"]), _mhz(p["freq_hz"]), str(p["adc_raw"]), f"{p['voltage']:.4f}"] for p in points],
        )


def cmd_stats(args) -> None:
    conn = _open(args)
    rows = db.list_measurements(conn, args.since, args.until)
    conn.close()
    if not rows:
        print("No measurements in range")
        return
    values = [r["b_mt"] for r in rows if r["fit_ok"]]
    errors = [r["b_mt"] - r["b_true_mt"] for r in rows if r["fit_ok"] and r["b_true_mt"] is not None]
    print(f"Measurements  {len(rows)} ({len(rows) - len(values)} failed fits)")
    print(f"Time range    {rows[0]['timestamp']} -> {rows[-1]['timestamp']}")
    print(f"Latest B      {_fmt(rows[-1]['b_mt'], '.4f')} mT")
    if values:
        std = statistics.stdev(values) if len(values) > 1 else 0.0
        print(f"B min/max     {min(values):.4f} / {max(values):.4f} mT")
        print(f"B mean/std    {statistics.mean(values):.4f} / {std:.4f} mT")
    if errors:
        mae = statistics.mean(abs(e) for e in errors)
        print(f"Fit error     mean {statistics.mean(errors):+.5f} mT, mean abs {mae:.5f} mT (vs mock_truth)")


MEASUREMENT_FIELDS = [
    "sweep_id", "timestamp", "b_mt", "f_minus_hz", "f_plus_hz", "contrast", "linewidth_hz", "fit_ok", "b_true_mt",
]


def _write_records(records: list[dict], fields: list[str], fmt: str, out) -> None:
    if fmt == "json":
        json.dump(records, out, indent=2)
        out.write("\n")
    else:
        writer = csv.DictWriter(out, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)


def cmd_export_measurements(args) -> None:
    conn = _open(args)
    rows = db.list_measurements(conn, args.since, args.until)
    conn.close()
    records = [{k: r[k] for k in MEASUREMENT_FIELDS} for r in rows]
    with _output(args.output) as out:
        _write_records(records, MEASUREMENT_FIELDS, args.format, out)


def cmd_export_sweep(args) -> None:
    conn = _open(args)
    if db.get_sweep(conn, args.id) is None:
        raise SystemExit(f"error: sweep {args.id} not found")
    records = [dict(p) for p in db.get_sweep_points(conn, args.id)]
    conn.close()
    with _output(args.output) as out:
        _write_records(records, ["idx", "freq_hz", "adc_raw", "voltage"], args.format, out)


def cmd_export_mqtt(args) -> None:
    conn = _open(args)
    sweeps = db.list_sweeps(conn, args.since, args.until)
    with _output(args.output) as out:
        for s in sweeps:
            adc = [p["adc_raw"] for p in db.get_sweep_points(conn, s["id"])]
            payload = build_payload(
                parse_ts(s["started_at"]), s["freq_start_hz"], s["freq_step_hz"], s["power_dbm"], adc
            )
            out.write(json.dumps(payload, separators=(",", ":")) + "\n")
    conn.close()


def cmd_delete(args) -> None:
    if not args.since and not args.until:
        raise SystemExit("error: give --from and/or --to (use 'reset' to delete everything)")
    conn = _open(args)
    n = db.delete_range(conn, args.since, args.until)
    conn.close()
    print(f"Deleted {n} sweeps (with their points, measurements and truth)")


def cmd_reset(args) -> None:
    if not args.yes:
        raise SystemExit("error: this deletes all data; re-run with --yes")
    conn = _open(args)
    db.reset(conn)
    conn.close()
    print(f"All data deleted from {args.db}")


# ── parser ──────────────────────────────────────────────────────────────


def _add_range(p: argparse.ArgumentParser) -> None:
    p.add_argument("--from", dest="since", type=timestamp, help="start timestamp (ISO-8601, UTC if no offset)")
    p.add_argument("--to", dest="until", type=timestamp, help="end timestamp (ISO-8601, UTC if no offset)")


def _add_model(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("field scenario")
    g.add_argument("--scenario", choices=sorted(SCENARIOS), default="magnet")
    g.add_argument("--duration", type=duration, default=600.0, help="total time, e.g. 10m (default 10m)")
    g.add_argument("--interval", type=duration, default=2.0, help="time between sweeps (default 2s)")
    g.add_argument("--b0", type=float, default=1.0, help="base field in mT (default 1.0)")
    g.add_argument("--amplitude", type=float, default=0.5, help="field variation in mT (default 0.5)")
    g.add_argument("--period", type=duration, default=120.0, help="sine period / step length (default 2m)")
    g.add_argument("--seed", type=int, default=42, help="random seed (default 42)")

    g = p.add_argument_group("ODMR signal")
    g.add_argument("--baseline", type=float, default=2.4, help="photodiode voltage off resonance (default 2.4)")
    g.add_argument("--contrast", type=float, default=0.03, help="dip depth fraction (default 0.03)")
    g.add_argument("--linewidth", type=float, default=8e6, help="dip FWHM in Hz (default 8e6)")
    g.add_argument("--noise", type=float, default=0.003, help="noise std as fraction of baseline (default 0.003)")

    g = p.add_argument_group("synthesizer sweep")
    g.add_argument("--freq-start", type=float, default=2.80e9, help="Hz (default 2.80e9)")
    g.add_argument("--freq-stop", type=float, default=2.94e9, help="Hz (default 2.94e9)")
    g.add_argument("--freq-step", type=float, default=1e6, help="Hz (default 1e6)")
    g.add_argument("--power", type=float, default=-10.0, help="synth power in dBm (default -10)")


def build_parser() -> argparse.ArgumentParser:
    db_help = f"SQLite file (default $MOCKDATA_DB or {DEFAULT_DB})"
    parser = argparse.ArgumentParser(prog="mockdb", description=__doc__)
    parser.add_argument("--db", default=os.environ.get("MOCKDATA_DB", DEFAULT_DB), help=db_help)
    # lets --db also be given after the subcommand without overriding the top-level default
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=argparse.SUPPRESS, help=db_help)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", parents=[common], help="create the schema").set_defaults(func=cmd_init)

    p = sub.add_parser("generate", parents=[common], help="generate a batch of mock sweeps")
    _add_model(p)
    p.add_argument("--start", help="timestamp of the first sweep (default: now minus duration)")
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("live", parents=[common], help="add one mock sweep every interval in real time")
    _add_model(p)
    p.add_argument("--count", type=int, help="stop after N sweeps (default: run until Ctrl-C)")
    p.set_defaults(func=cmd_live)

    p = sub.add_parser("measurements", parents=[common], help="magnetic field measurements")
    msub = p.add_subparsers(dest="action", required=True)
    q = msub.add_parser("list", parents=[common], help="list measurements")
    _add_range(q)
    q.add_argument("--limit", type=int)
    q.set_defaults(func=cmd_measurements_list)

    p = sub.add_parser("sweeps", parents=[common], help="raw sweeps")
    ssub = p.add_subparsers(dest="action", required=True)
    q = ssub.add_parser("list", parents=[common], help="list sweeps")
    _add_range(q)
    q.add_argument("--limit", type=int)
    q.set_defaults(func=cmd_sweeps_list)
    q = ssub.add_parser("show", parents=[common], help="show one sweep and its fit")
    q.add_argument("id", type=int)
    q.add_argument("--points", action="store_true", help="also print every point")
    q.set_defaults(func=cmd_sweeps_show)

    p = sub.add_parser("stats", parents=[common], help="summary of measurements")
    _add_range(p)
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("export", parents=[common], help="export data")
    esub = p.add_subparsers(dest="what", required=True)
    q = esub.add_parser("measurements", parents=[common], help="measurements as CSV/JSON")
    _add_range(q)
    q.add_argument("--format", choices=["csv", "json"], default="csv")
    q.add_argument("-o", "--output", help="file (default stdout)")
    q.set_defaults(func=cmd_export_measurements)
    q = esub.add_parser("sweep", parents=[common], help="one sweep's points as CSV/JSON")
    q.add_argument("id", type=int)
    q.add_argument("--format", choices=["csv", "json"], default="csv")
    q.add_argument("-o", "--output", help="file (default stdout)")
    q.set_defaults(func=cmd_export_sweep)
    q = esub.add_parser("mqtt", parents=[common], help="sweeps as ESP32 MQTT payloads (JSON lines)")
    _add_range(q)
    q.add_argument("-o", "--output", help="file (default stdout)")
    q.set_defaults(func=cmd_export_mqtt)

    p = sub.add_parser("delete", parents=[common], help="delete sweeps in a time range")
    _add_range(p)
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("reset", parents=[common], help="delete all data")
    p.add_argument("--yes", action="store_true", help="confirm")
    p.set_defaults(func=cmd_reset)

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except BrokenPipeError:
        sys.stderr.close()


if __name__ == "__main__":
    main()
