import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone

from mockdata import db, physics
from mockdata.cli import main
from mockdata.generator import SweepConfig, generate
from mockdata.scenarios import ScenarioParams

START = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)


class DbTest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_schema(self.conn)

    def tearDown(self):
        self.conn.close()

    def _generate(self, duration_s=60.0, interval_s=2.0, seed=42):
        return generate(
            self.conn,
            "sine",
            ScenarioParams(duration_s=duration_s, period_s=30.0),
            physics.OdmrParams(),
            SweepConfig(),
            START,
            interval_s,
            seed,
        )

    def _count(self, table):
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_generate_fills_all_tables(self):
        run_id, n = self._generate()
        self.assertEqual(n, 30)
        self.assertEqual(self._count("sweeps"), 30)
        self.assertEqual(self._count("sweep_points"), 30 * 141)
        self.assertEqual(self._count("measurements"), 30)
        self.assertEqual(self._count("mock_truth"), 30)
        self.assertEqual(self._count("mock_runs"), 1)

    def test_measurements_match_truth(self):
        self._generate()
        for row in db.list_measurements(self.conn):
            self.assertTrue(row["fit_ok"])
            self.assertAlmostEqual(row["b_mt"], row["b_true_mt"], delta=0.03)

    def test_time_range_filter(self):
        self._generate()
        rows = db.list_measurements(self.conn, "2026-11-24T12:00:10.000Z", "2026-11-24T12:00:20.000Z")
        self.assertEqual(len(rows), 6)

    def test_delete_range_cascades(self):
        self._generate()
        db.delete_range(self.conn, None, "2026-11-24T12:00:09.000Z")
        self.assertEqual(self._count("sweeps"), 25)
        self.assertEqual(self._count("sweep_points"), 25 * 141)
        self.assertEqual(self._count("measurements"), 25)
        self.assertEqual(self._count("mock_truth"), 25)

    def test_reset(self):
        self._generate()
        db.reset(self.conn)
        for table in db.ALL_TABLES:
            self.assertEqual(self._count(table), 0)

    def test_ingest_rejects_bad_payload(self):
        with self.assertRaises(ValueError):
            db.ingest_payload(self.conn, {"started_at": "x", "adc": []})


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "test.db")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            main(["--db", self.db, *args])
        return buf.getvalue()

    def test_generate_and_export_mqtt_roundtrip(self):
        self.run_cli("generate", "--duration", "20s", "--start", "2026-11-24T12:00:00Z")
        lines = self.run_cli("export", "mqtt").strip().splitlines()
        self.assertEqual(len(lines), 10)
        payload = json.loads(lines[0])
        self.assertEqual(payload["started_at"], "2026-11-24T12:00:00.000Z")
        self.assertEqual(payload["n_points"], len(payload["adc"]))

        # re-ingesting the exported payloads reproduces the same measurements
        conn = db.connect(":memory:")
        db.init_schema(conn)
        for line in lines:
            db.ingest_payload(conn, json.loads(line))
        src = db.connect(self.db)
        self.assertEqual(
            [r["b_mt"] for r in db.list_measurements(conn)],
            [r["b_mt"] for r in db.list_measurements(src)],
        )
        conn.close()
        src.close()

    def test_commands_run(self):
        self.run_cli("generate", "--duration", "10s")
        self.assertIn("B (mT)", self.run_cli("measurements", "list"))
        self.assertIn("Fit error", self.run_cli("stats"))
        self.assertIn("B ", self.run_cli("sweeps", "show", "1"))
        self.assertIn("freq_hz", self.run_cli("export", "sweep", "1"))
        self.assertIn("b_mt", self.run_cli("export", "measurements", "--format", "json"))


if __name__ == "__main__":
    unittest.main()
