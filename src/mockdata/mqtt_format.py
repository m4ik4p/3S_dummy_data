"""The JSON payload the ESP32 publishes over MQTT for one sweep."""

from __future__ import annotations

from datetime import datetime, timezone

REQUIRED_KEYS = ("started_at", "freq_start_hz", "freq_step_hz", "n_points", "power_dbm", "adc")


def format_ts(dt: datetime) -> str:
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def build_payload(
    started_at: datetime,
    freq_start_hz: float,
    freq_step_hz: float,
    power_dbm: float,
    adc: list[int],
) -> dict:
    return {
        "started_at": format_ts(started_at),
        "freq_start_hz": freq_start_hz,
        "freq_step_hz": freq_step_hz,
        "n_points": len(adc),
        "power_dbm": power_dbm,
        "adc": adc,
    }


def validate_payload(payload: dict) -> None:
    missing = [k for k in REQUIRED_KEYS if k not in payload]
    if missing:
        raise ValueError(f"payload missing keys: {', '.join(missing)}")
    if payload["n_points"] != len(payload["adc"]):
        raise ValueError("n_points does not match length of adc")
