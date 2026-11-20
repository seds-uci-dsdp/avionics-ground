"""Check the structure of packets received by Ground Control.

A reading can be alarming and still be valid telemetry. For example, 0.0 V
is within the voltage field's range. Ground Control can use that reading to
raise a battery alarm after the packet passes validation.
"""

import math
from datetime import datetime, timedelta


# Each measurement must be a finite float. A pair gives its inclusive limits.
# None means the mock profile checks the type but has no agreed numeric limits.
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
REQUIRED_FIELDS = (
    "timestamp",
    "sat_status",
    *FLOAT_RANGES,
    "long_lat_timestamp",
)
UINT32_MAX = 2**32 - 1


def _timestamp_error(value):
    """Check the packet clock, which the simulator reports in UTC."""
    if not isinstance(value, str):
        return "timestamp must be a string."

    # The mock date starts with YYYY-MM-DD. Python also accepts arbitrary
    # separators, so check what follows the date before parsing the clock.
    if value[10:11] not in ("T", " "):
        return "timestamp must be an ISO 8601 date and time."
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return "timestamp must be an ISO 8601 date and time."

    # Both "Z" and "+00:00" mean UTC. A clock without a timezone is ambiguous.
    if parsed.utcoffset() != timedelta(0):
        return "timestamp must include a UTC offset of zero."
    return None


def validate_telemetry(telemetry):
    """Return all field errors, or an empty list for a valid packet.

    Leave the original readings intact so the terminal and logs can show
    exactly what arrived, including a bad value or an absent field.
    """
    if not isinstance(telemetry, dict):
        return ["Telemetry must be a dictionary."]

    # A missing key means no reading arrived. A present key with a value of
    # None means a reading arrived in the wrong form. Report these separately.
    errors = [
        f"Missing required field: {field}"
        for field in REQUIRED_FIELDS
        if field not in telemetry
    ]

    if "timestamp" in telemetry:
        error = _timestamp_error(telemetry["timestamp"])
        if error:
            errors.append(error)

    if "sat_status" in telemetry:
        # FSW's status encoding is still open. Accept any label with visible text.
        status = telemetry["sat_status"]
        if not isinstance(status, str) or not status.strip():
            errors.append("sat_status must be a non-empty string.")

    for field, bounds in FLOAT_RANGES.items():
        if field not in telemetry:
            continue

        value = telemetry[field]
        # NaN and infinity are floats in Python, but neither gives a sensor
        # reading that the dashboard can plot on a finite scale.
        if not isinstance(value, float) or not math.isfinite(value):
            errors.append(f"{field} must be a finite float.")
        elif bounds is not None:
            minimum, maximum = bounds
            if not minimum <= value <= maximum:
                errors.append(f"{field} must be between {minimum} and {maximum}.")

    if "long_lat_timestamp" in telemetry:
        # The position fix has its own clock in whole Unix seconds. An unsigned
        # 32-bit field holds 0 through 2**32 - 1. Check the exact integer type
        # because Python also treats True and False as integers.
        value = telemetry["long_lat_timestamp"]
        if type(value) is not int or not 0 <= value <= UINT32_MAX:
            errors.append("long_lat_timestamp must be an unsigned 32-bit integer.")

    return errors
