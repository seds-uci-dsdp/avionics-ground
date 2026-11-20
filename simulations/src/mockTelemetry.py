"""Standalone, one-second telemetry simulation for Ground Control testing."""

import argparse
import json
import math
import random
import time
from datetime import datetime, timedelta, timezone

if __package__:
    from .telemetryValidation import UINT32_MAX, validate_telemetry
else:
    from telemetryValidation import UINT32_MAX, validate_telemetry


SCENARIOS = (
    "normal", "overheating", "low_battery", "accelerated_decay",
    "invalid_sensor", "communication_dropout", "startup_checkout",
)
EARTH_RADIUS_KM = 6371.0
EARTH_MU = 398600.44  # Gravitational parameter, km^3/s^2
EARTH_ROTATION_RAD_S = 7.292115e-5
INCLINATION = math.radians(51.6)
INITIAL_ALTITUDE_KM = 400.0


class TelemetrySimulator:
    """Each step samples the current instant, then advances one simulated second."""

    def __init__(self, scenario="normal", seed=None, start_time=None):
        if scenario not in SCENARIOS:
            raise ValueError(f"Unknown scenario: {scenario}")
        if start_time is None:
            start_time = datetime.now(timezone.utc)
        if start_time.utcoffset() is None:
            raise ValueError("start_time must include a timezone.")
        self.start_time = start_time.astimezone(timezone.utc)
        self.scenario = scenario
        self.sat_status = "SAFE" if scenario == "startup_checkout" else "STANDBY"
        self.random = random.Random(seed)
        self.elapsed_s = 0
        self.phase = 0.0
        self.altitude_km = INITIAL_ALTITUDE_KM
        self.temperature = 25.0
        self.voltage = 12.4
        self.current = 0.8

    def _radial_speed(self):
        """Prescribe an inward speed in km/s for the accelerated test only."""
        if self.scenario == "accelerated_decay":
            return -min(0.05, self.altitude_km - 250.0)
        return 0.0

    def _orbit_fields(self):
        """Project one inclined orbit into ground coordinates and ECI velocity."""
        cos_u, sin_u = math.cos(self.phase), math.sin(self.phase)
        cos_i, sin_i = math.cos(INCLINATION), math.sin(INCLINATION)
        # Unit radial vector in ECI. The ascending node is on the x axis.
        x, y, z = cos_u, sin_u * cos_i, sin_u * sin_i
        earth_angle = self.elapsed_s * EARTH_ROTATION_RAD_S
        longitude = math.degrees(math.atan2(y, x) - earth_angle)
        radius = EARTH_RADIUS_KM + self.altitude_km
        tangential = math.sqrt(EARTH_MU / radius)
        radial = self._radial_speed()
        return {
            "latitude_deg": round(math.degrees(math.asin(z)), 6),
            "longitude_deg": round((longitude + 180) % 360 - 180, 6),
            "altitude_km": round(self.altitude_km, 3),
            "velocity_x": round(radial * x - tangential * sin_u, 6),
            "velocity_y": round(radial * y + tangential * cos_u * cos_i, 6),
            "velocity_z": round(radial * z + tangential * cos_u * sin_i, 6),
        }

    def _advance(self):
        """Advance orbit and sensor states over the same one-second interval."""
        radial = self._radial_speed()
        midpoint_radius = EARTH_RADIUS_KM + self.altitude_km + radial / 2
        self.phase = (self.phase + math.sqrt(EARTH_MU / midpoint_radius**3)) % math.tau
        self.altitude_km += radial
        self.elapsed_s += 1

        # A smooth orbital cycle supplies mock thermal and electrical loads.
        cycle = math.sin(self.phase)
        target_temp = 75.0 if self.scenario == "overheating" else 25.0 + 4 * cycle #simulating overheating by using a formula, so current tempt will gradually move twards overheating temp

        target_voltage = 5.0 if self.scenario == "low_battery" else 12.4 + 0.15 * cycle

        self.temperature += (target_temp - self.temperature) / 90
        self.voltage += (target_voltage - self.voltage) / 120
        target_current = 0.8 + 0.1 * cycle + self.random.uniform(-0.03, 0.03)
        self.current += (target_current - self.current) / 8

    def step(self):
        """Return the received dictionary, or None during a scheduled radio gap."""
        t = self.elapsed_s
        instant = self.start_time + timedelta(seconds=t)
        fix_time = math.floor(instant.timestamp())
        if not 0 <= fix_time <= UINT32_MAX:
            raise ValueError("Simulated UTC time exceeds the long_lat_timestamp range.")

        if self.scenario == "startup_checkout":
            if t == 3 and self.sat_status == "SAFE":
                # T02: a scripted ground command arrives with healthy battery and low spin.
                self.sat_status = "CHECKOUT"
            elif t == 8 and self.sat_status == "CHECKOUT":
                # T05: all scripted checkout self-tests pass.
                self.sat_status = "STANDBY"

        packet = {
            "timestamp": instant.isoformat(),
            "long_lat_timestamp": fix_time,
            "sat_status": self.sat_status,
            "roll_deg": round(3 * math.sin(t / 100), 3),
            "pitch_deg": round(2 * math.sin(t / 80), 3),
            "yaw_deg": round(5 * math.sin(t / 150), 3),
            "battery_voltage_v": round(self.voltage, 4),
            "current_a": round(self.current, 4),
            "temp_c": round(self.temperature + self.random.uniform(-0.03, 0.03), 3),
            **self._orbit_fields(),
        }
        self._advance()

        if self.scenario == "communication_dropout" and 5 <= t % 12 < 8:
            return None
        if self.scenario == "invalid_sensor":
            # Rotate through three bad packets, followed by one healthy packet.
            fault = t % 4
            if fault == 0:
                packet["battery_voltage_v"] = "NO_READING"
            elif fault == 1:
                packet["pitch_deg"] = 120.0
            elif fault == 2:
                del packet["temp_c"]
        return packet


_default_simulator = None


def generate_telemetry():
    """Continue the default normal stream for callers of the original function."""
    global _default_simulator
    if _default_simulator is None:
        _default_simulator = TelemetrySimulator()
    return _default_simulator.step()


def display_telemetry(telemetry):
    """Show the original packet alongside any validation errors."""
    print(json.dumps(telemetry, indent=4))
    for error in validate_telemetry(telemetry):
        print(f"Validation error: {error}")


def main():
    """Run the selected scenario in the terminal until interrupted or complete."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=SCENARIOS, default="normal")
    parser.add_argument("--seed", type=int, help="reproduce sensor variations")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="wall-clock seconds between steps (default 1, 0 for no delay)")
    parser.add_argument("--steps", type=int, help="stop after this many simulated seconds")
    args = parser.parse_args()
    if not math.isfinite(args.interval) or args.interval < 0:
        parser.error("--interval must be finite and nonnegative")
    if args.steps is not None and args.steps < 1:
        parser.error("--steps must be positive")

    simulator = TelemetrySimulator(args.scenario, seed=args.seed)
    print(f"Ground Control Telemetry Simulator ({args.scenario})")
    try:
        while args.steps is None or simulator.elapsed_s < args.steps:
            packet = simulator.step()
            if packet is None:
                print("[No telemetry received]")
            else:
                display_telemetry(packet)
            if args.interval and (args.steps is None or simulator.elapsed_s < args.steps):
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nSimulator stopped.")


if __name__ == "__main__":
    main()
