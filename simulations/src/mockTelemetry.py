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
    "normal",
    "overheating",
    "low_battery",
    "accelerated_decay",
    "invalid_sensor",
    "communication_dropout",
    "startup_checkout",
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
        self.elapsed_s = 0
        self.scenario = scenario
        self.sat_status = "SAFE" if scenario == "startup_checkout" else "STANDBY"
        self.random = random.Random(seed)

        # Phase is the angle traveled around the orbit. At zero, the spacecraft
        # crosses the equator heading north, directly above our mock Greenwich meridian.
        self.phase = 0.0
        self.altitude_km = INITIAL_ALTITUDE_KM

        self.temperature = 25.0
        self.voltage = 12.4
        self.current = 0.8

    def _radial_speed(self):
        """Prescribe an inward speed in km/s for the accelerated test only."""
        if self.scenario == "accelerated_decay":
            # Artificially decrease altitude by up to 50 m per simulated second,
            # stopping at 250 km. This is for Ground Control testing, not a
            # physical atmospheric-drag or deorbit model.
            return -min(0.05, self.altitude_km - 250.0)
        return 0.0

    def _orbit_fields(self):
        """Find the point below the spacecraft on the map and its velocity in space."""
        cos_phase = math.cos(self.phase)
        sin_phase = math.sin(self.phase)
        cos_tilt = math.cos(INCLINATION)
        sin_tilt = math.sin(INCLINATION)

        # Picture a circular hoop centered on Earth. Cosine and sine trace the
        # hoop. Tilting it around the x axis splits the sine part between y
        # (in the equatorial plane) and z (north). This direction has length 1.
        x = cos_phase
        y = sin_phase * cos_tilt
        z = sin_phase * sin_tilt

        # atan2 gives the angle around Earth's axis as seen from above the
        # North Pole. Subtract Earth's eastward turn to locate the point on
        # the rotating map. After a full orbit, the ground track has shifted west.
        earth_angle = self.elapsed_s * EARTH_ROTATION_RAD_S
        longitude = math.degrees(math.atan2(y, x) - earth_angle)
        # Wrap longitude at the map edge. On a sphere, the northward component
        # z equals the sine of latitude, so asin gives the angle above the equator.
        longitude = (longitude + 180) % 360 - 180
        latitude = math.degrees(math.asin(z))

        # Circular speed depends on distance from Earth's center. A lower
        # circular orbit takes more speed to follow its tighter curve.
        radius = EARTH_RADIUS_KM + self.altitude_km
        forward_speed = math.sqrt(EARTH_MU / radius)
        radial_speed = self._radial_speed()

        # The forward direction is tangent to the hoop, a quarter-turn from
        # the outward direction. Add any inward drift along the radius.
        # These Earth-centered inertial (ECI) axes stay fixed as Earth spins.
        velocity_x = radial_speed * x - forward_speed * sin_phase
        velocity_y = radial_speed * y + forward_speed * cos_phase * cos_tilt
        velocity_z = radial_speed * z + forward_speed * cos_phase * sin_tilt

        return {
            "latitude_deg": round(latitude, 6),
            "longitude_deg": round(longitude, 6),
            "altitude_km": round(self.altitude_km, 3),
            "velocity_x": round(velocity_x, 6),
            "velocity_y": round(velocity_y, 6),
            "velocity_z": round(velocity_z, 6),
        }

    def _advance(self):
        """Advance orbit and sensor states over the same one-second interval."""
        radial = self._radial_speed()
        # In a one-second step, radial speed in km/s gives the radius change
        # in km. Half of that change gives the radius midway through the step.
        
        midpoint_radius = EARTH_RADIUS_KM + self.altitude_km + radial / 2
        # Circular-orbit angular rate (rad/s), derived from gravity.
        # Each simulation step lasts one second, so adding this rate
        # advances the orbital phase by that many radians.
        # Modulo tau wraps the phase after a full revolution.
        angular_speed = math.sqrt(EARTH_MU / midpoint_radius**3)
        self.phase = (self.phase + angular_speed) % math.tau
        self.altitude_km += radial
        self.elapsed_s += 1

        # This wave repeats once per orbit, giving the mock sensors a common
        # warming and cooling cycle without modeling sunlight or hardware.
        cycle = math.sin(self.phase)
        target_temp = 75.0 if self.scenario == "overheating" else 25.0 + 4 * cycle
        target_voltage = 5.0 if self.scenario == "low_battery" else 12.4 + 0.15 * cycle
        target_current = 0.8 + 0.1 * cycle + self.random.uniform(-0.03, 0.03)

        # Each reading closes a fraction of the gap to its target per second.
        # For example, a temperature 9 °C below target rises 0.1 °C this step.
        # Current closes 1/8 of its gap, so it responds faster to load changes.
        self.temperature += (target_temp - self.temperature) / 90
        self.voltage += (target_voltage - self.voltage) / 120
        self.current += (target_current - self.current) / 8

    def step(self):
        """Return the received dictionary, or None during a scheduled radio gap."""
        t = self.elapsed_s
        # Both timestamps describe this sample. The position field records
        # whole seconds since the Unix epoch (1970-01-01 UTC).
        instant = self.start_time + timedelta(seconds=t)
        fix_time = math.floor(instant.timestamp())
        if not 0 <= fix_time <= UINT32_MAX:
            raise ValueError("Simulated UTC time exceeds the long_lat_timestamp range.")

        if self.scenario == "startup_checkout":
            if t == 3 and self.sat_status == "SAFE":
                # Simulate a successful transition to CHECKOUT at second 3.
                # No actual ground command or health-condition check occurs.
                self.sat_status = "CHECKOUT"
                
            elif t == 8 and self.sat_status == "CHECKOUT":
                # Simulate completion of checkout at second 8.
                # Hardware self-tests are not implemented yet.
                self.sat_status = "STANDBY"

        # Add noise to the thermometer reading. The next step starts from
        # the underlying temperature, so measurement noise cannot accumulate.
        measured_temp = self.temperature + self.random.uniform(-0.03, 0.03)
        packet = {
            "timestamp": instant.isoformat(),
            "long_lat_timestamp": fix_time,
            "sat_status": self.sat_status,
            # Attitude describes which way the spacecraft points. Each axis
            # rocks gently at its own pace while the orbit carries it forward.
            "roll_deg": round(3 * math.sin(t / 100), 3),
            "pitch_deg": round(2 * math.sin(t / 80), 3),
            "yaw_deg": round(5 * math.sin(t / 150), 3),
            "battery_voltage_v": round(self.voltage, 4),
            "current_a": round(self.current, 4),
            "temp_c": round(measured_temp, 3),
            **self._orbit_fields(),
        }
        # The spacecraft keeps moving even when the receiver misses a packet.
        # Advance before applying delivery faults to this sample.
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
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="wall-clock seconds between steps (default 1, 0 for no delay)",
    )
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

            # This delay sets the viewing pace. Even with zero delay, each
            # pair of consecutive simulated samples is one second apart.
            if args.interval and (args.steps is None or simulator.elapsed_s < args.steps):
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nSimulator stopped.")


if __name__ == "__main__":
    main()
