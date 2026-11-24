-- Backend tables: what the Raspberry Pi stores for real.

CREATE TABLE IF NOT EXISTS sweeps (
    id            INTEGER PRIMARY KEY,
    started_at    TEXT    NOT NULL,          -- ISO-8601 UTC, e.g. 2026-11-24T14:00:00.000Z
    freq_start_hz REAL    NOT NULL,
    freq_stop_hz  REAL    NOT NULL,
    freq_step_hz  REAL    NOT NULL,
    n_points      INTEGER NOT NULL,
    power_dbm     REAL                       -- synthesizer output power
);
CREATE INDEX IF NOT EXISTS idx_sweeps_started_at ON sweeps(started_at);

CREATE TABLE IF NOT EXISTS sweep_points (
    sweep_id INTEGER NOT NULL REFERENCES sweeps(id) ON DELETE CASCADE,
    idx      INTEGER NOT NULL,               -- position within the sweep
    freq_hz  REAL    NOT NULL,
    adc_raw  INTEGER NOT NULL,               -- 0-4095 (ESP32 12-bit ADC)
    voltage  REAL    NOT NULL,               -- adc_raw * 3.3 / 4095
    PRIMARY KEY (sweep_id, idx)
);

CREATE TABLE IF NOT EXISTS measurements (
    id           INTEGER PRIMARY KEY,
    sweep_id     INTEGER NOT NULL UNIQUE REFERENCES sweeps(id) ON DELETE CASCADE,
    timestamp    TEXT    NOT NULL,           -- same as sweeps.started_at
    b_mt         REAL,                       -- |B| along the NV axis in mT; NULL if the fit failed
    f_minus_hz   REAL,
    f_plus_hz    REAL,
    contrast     REAL,                       -- mean dip depth as a fraction of the baseline
    linewidth_hz REAL,                       -- mean dip FWHM
    fit_ok       INTEGER NOT NULL CHECK (fit_ok IN (0, 1))
);
CREATE INDEX IF NOT EXISTS idx_measurements_timestamp ON measurements(timestamp);

-- Mock-only tables: the real backend can drop these.

CREATE TABLE IF NOT EXISTS mock_runs (
    id          INTEGER PRIMARY KEY,
    created_at  TEXT    NOT NULL,
    scenario    TEXT    NOT NULL,
    params_json TEXT    NOT NULL,
    seed        INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS mock_truth (
    sweep_id    INTEGER PRIMARY KEY REFERENCES sweeps(id) ON DELETE CASCADE,
    mock_run_id INTEGER NOT NULL REFERENCES mock_runs(id) ON DELETE CASCADE,
    b_true_mt   REAL    NOT NULL
);
