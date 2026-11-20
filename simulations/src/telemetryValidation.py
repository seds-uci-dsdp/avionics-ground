"""Structural checks for the provisional Ground Control telemetry dictionary."""

import math
from datetime import datetime, timedelta


# None means FSW has not specified numeric bounds for this field.
FLOAT_RANGES = {
    "roll_deg": (-180, 180),
    "pitch_deg": (-90, 90),
    "yaw_deg": (-180, 180),
    "velocity_x": None,
    "velocity_y": None,
    "velocity_z": None,
    "battery_voltage_v": (0, 30),
    "current_a": None,
    "temp_c": None,
    "latitude_deg": (-90, 90),
    "longitude_deg": (-180, 180),
    "altitude_km": None,
}
REQUIRED_FIELDS = ("timestamp", "sat_status", *FLOAT_RANGES, "long_lat_timestamp")
UINT32_MAX = 2**32 - 1


def validate_telemetry(telemetry):
    """Return field-specific structural errors while preserving the received packet."""
    if not isinstance(telemetry, dict):
        return ["Telemetry must be a dictionary."]

    errors = [
        f"Missing required field: {field}"
        for field in REQUIRED_FIELDS if field not in telemetry
    ]

    if "timestamp" in telemetry:
        value = telemetry["timestamp"]
        if not isinstance(value, str):
            errors.append("timestamp must be a string.")
        else:
            try:
                # fromisoformat also accepts arbitrary separator characters.
                if value[10:11] not in ("T", " "):
                    raise ValueError
                parsed = datetime.fromisoformat(value)
            except ValueError:
                errors.append("timestamp must be an ISO 8601 date and time.")
            else:
                if parsed.utcoffset() != timedelta(0):
                    errors.append("timestamp must include a UTC offset of zero.")

    if "sat_status" in telemetry:
        status = telemetry["sat_status"]
        if not isinstance(status, str) or not status.strip():
            errors.append("sat_status must be a non-empty string.")

    for field, bounds in FLOAT_RANGES.items():
        if field not in telemetry:
            continue
        value = telemetry[field]
        if not isinstance(value, float) or not math.isfinite(value):
            errors.append(f"{field} must be a finite float.")
        elif bounds and not bounds[0] <= value <= bounds[1]:
            errors.append(f"{field} must be between {bounds[0]} and {bounds[1]}.")

    if "long_lat_timestamp" in telemetry:
        value = telemetry["long_lat_timestamp"]
        if type(value) is not int or not 0 <= value <= UINT32_MAX:
            errors.append("long_lat_timestamp must be an unsigned 32-bit integer.")
    return errors
