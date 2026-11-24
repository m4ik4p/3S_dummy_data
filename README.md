Setup to interact with the DB with dummy data for the Quantum Sensing Project

# mockdb

A small CLI (Python standard library only) that fills an SQLite database with mock data from the
NV-centre magnetometer, so the web app backend can be built before the hardware is finished.

The real system works like this:

```
ESP32 --MQTT (raw sweep)--> Raspberry Pi --> find the two ODMR dips --> B (mT) --> SQLite --> web app
```

The ESP32 sweeps the synthesizer around 2.87 GHz and reads the photodiode at each frequency. A
magnetic field splits the single fluorescence dip into two, at `f± = 2.87 GHz ± 28 MHz/mT · B`.
The Pi measures the gap and computes `B = (f+ − f−) / (2 · 28 MHz/mT)`.

`mockdb` does the ESP32's part (a simulated spectrum for a chosen field pattern), then runs the same
ingest step the Pi will run (`mockdata.db.ingest_payload`).

## Usage

No install needed:

```sh
PYTHONPATH=src python3 -m mockdata --help
```

Or install it to get the `mockdb` command: `pip install -e .`

The database is `mock.db` by default. Use `--db FILE` or `MOCKDATA_DB=FILE` to pick another one.

```sh
mockdb init                                   # create tables
mockdb generate                               # 10 min of sweeps, one every 2 s (magnet scenario)
mockdb generate --scenario sine --b0 1.2 --amplitude 0.3 --period 1m --duration 10m
mockdb live --interval 2s --scenario sine     # add a sweep every 2 s in real time (Ctrl-C to stop)

mockdb measurements list --from 2026-11-24T12:00:00Z --limit 20
mockdb stats                                  # B min/max/mean, fit error vs. ground truth
mockdb sweeps list
mockdb sweeps show 150 --points               # one sweep's spectrum and fit

mockdb export measurements --format csv -o field.csv
mockdb export sweep 150 --format json
mockdb export mqtt > payloads.jsonl           # ESP32 MQTT payloads, one per line

mockdb delete --from 2026-11-24T12:05:00Z     # delete sweeps in a time range
mockdb reset --yes                            # delete everything
```

Field scenarios (`--scenario`): `constant`, `drift`, `steps`, `sine`, `magnet` (a magnet approaching
and moving away again). Run `mockdb generate --help` for all signal and sweep options.

### Regenerating the committed `mock.db`

```sh
PYTHONPATH=src python3 -m mockdata reset --yes
PYTHONPATH=src python3 -m mockdata generate --scenario magnet --duration 10m --interval 2s \
    --start 2026-11-24T12:00:00Z --seed 42
```

The same command with the same seed always produces the same data. Git can't merge a binary
database, so it's best if one person regenerates and commits it.

## Data model

Backend tables (the Pi will store these for real):

| Table | Contents |
|---|---|
| `sweeps` | One row per sweep: time (ISO-8601 UTC) and synthesizer settings (start/stop/step frequency, power) |
| `sweep_points` | Raw photodiode readings: one row per frequency step (`freq_hz`, `adc_raw` 0–4095, `voltage`) |
| `measurements` | Result computed from each sweep: `b_mt`, dip positions, contrast, linewidth and `fit_ok`. `b_mt` is NULL if the fit failed |

Mock-only tables (the real backend can drop these):

| Table | Contents |
|---|---|
| `mock_runs` | How each batch of fake data was made (scenario, parameters, seed) |
| `mock_truth` | The true field used for each sweep, to check the Pi's calculation |

Useful queries for the web app:

```sql
-- field over time
SELECT timestamp, b_mt FROM measurements WHERE timestamp BETWEEN ? AND ? ORDER BY timestamp;
-- spectrum of one sweep
SELECT freq_hz, voltage FROM sweep_points WHERE sweep_id = ? ORDER BY idx;
-- latest value
SELECT * FROM measurements ORDER BY timestamp DESC LIMIT 1;
```

## MQTT payload (ESP32 → Pi)

```json
{"started_at": "2026-11-24T12:00:00.000Z", "freq_start_hz": 2800000000.0,
 "freq_step_hz": 1000000.0, "n_points": 141, "power_dbm": -10.0, "adc": [2976, 2976, ...]}
```

## Tests

```sh
PYTHONPATH=src python3 -m unittest discover -s tests
```
