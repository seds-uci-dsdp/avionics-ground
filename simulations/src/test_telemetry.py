"""Run from the project root with python3 -m unittest -v."""

import io
import json
import math
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone

from mockTelemetry import TelemetrySimulator, display_telemetry
from telemetryValidation import validate_telemetry


START = datetime(2026, 1, 1, 12, 0, 0, 500000, tzinfo=timezone.utc)
MEASUREMENTS = (
    "roll_deg", "pitch_deg", "yaw_deg", "velocity_x", "velocity_y", "velocity_z",
    "battery_voltage_v", "current_a", "temp_c", "latitude_deg", "longitude_deg",
    "altitude_km",
)


def stream(scenario="normal"):
    return TelemetrySimulator(scenario, seed=17, start_time=START)


class SimulatorTests(unittest.TestCase):
    def test_packet_contract_and_seed(self):
        simulator = stream()
        replay = stream()
        for second in range(10):
            packet = simulator.step()
            self.assertEqual(packet, replay.step())
            self.assertEqual(set(packet), {*MEASUREMENTS, "timestamp", "sat_status",
                                           "long_lat_timestamp"})
            for field in MEASUREMENTS:
                self.assertIs(type(packet[field]), float, field)
            self.assertIs(type(packet["sat_status"]), str)
            self.assertIs(type(packet["long_lat_timestamp"]), int)
            instant = START + timedelta(seconds=second)
            self.assertEqual(datetime.fromisoformat(packet["timestamp"]), instant)
            self.assertEqual(packet["long_lat_timestamp"], int(instant.timestamp()))
            self.assertEqual(json.loads(json.dumps(packet, allow_nan=False)), packet)
            self.assertEqual(validate_telemetry(packet), [])

    def test_startup_checkout_sequence_and_continuity(self):
        startup = stream("startup_checkout")
        normal = stream()
        statuses = ["SAFE"] * 3 + ["CHECKOUT"] * 5 + ["STANDBY"] * 12
        for second, status in enumerate(statuses):
            with self.subTest(second=second):
                packet = startup.step()
                expected = normal.step()
                self.assertEqual(expected["sat_status"], "STANDBY")
                expected["sat_status"] = status
                self.assertEqual(packet, expected)
                self.assertEqual(validate_telemetry(packet), [])

    def test_gradual_sensors(self):
        simulator = stream()
        previous = simulator.step()
        self.assertAlmostEqual(previous["temp_c"], 25, delta=0.03)
        self.assertEqual(previous["battery_voltage_v"], 12.4)
        self.assertEqual(previous["current_a"], 0.8)
        limits = {"temp_c": 0.08, "battery_voltage_v": 0.001, "current_a": 0.01,
                  "roll_deg": 0.04, "pitch_deg": 0.04, "yaw_deg": 0.04}
        for _ in range(360):
            packet = simulator.step()
            for field, limit in limits.items():
                self.assertLess(abs(packet[field] - previous[field]), limit, field)
            previous = packet
        self.assertGreater(packet["temp_c"], 25.5)
        self.assertGreater(packet["battery_voltage_v"], 12.41)

    def test_complete_ground_track(self):
        simulator = stream()
        # One circular orbit at 400 km takes approximately 5545 seconds.
        for second in range(5546):
            packet = simulator.step()
            self.assertEqual(validate_telemetry(packet), [])
            if second == 1386:
                self.assertAlmostEqual(packet["latitude_deg"], 51.6, delta=0.1)
        self.assertAlmostEqual(packet["latitude_deg"], 0.0, delta=0.1)
        # Earth has rotated east by about 23 degrees underneath the orbit.
        self.assertAlmostEqual(packet["longitude_deg"], -23.2, delta=0.1)
        self.assertEqual(packet["altitude_km"], 400.0)
        speed = math.hypot(*(packet["velocity_" + axis] for axis in "xyz"))
        self.assertAlmostEqual(speed, 7.67, delta=0.02)

    def test_velocity_matches_motion_in_both_orbits(self):
        def inertial_position(packet, second):
            lat = math.radians(packet["latitude_deg"])
            lon = math.radians(packet["longitude_deg"]) + second * 7.292115e-5
            r = 6371 + packet["altitude_km"]
            return (r * math.cos(lat) * math.cos(lon),
                    r * math.cos(lat) * math.sin(lon), r * math.sin(lat))

        for scenario in ("normal", "accelerated_decay"):
            with self.subTest(scenario=scenario):
                simulator = stream(scenario)
                for _ in range(500):
                    simulator.step()
                before = inertial_position(simulator.step(), 500)
                middle = simulator.step()
                after = inertial_position(simulator.step(), 502)
                for axis, a, b in zip("xyz", before, after):
                    self.assertAlmostEqual(middle["velocity_" + axis], (b - a) / 2,
                                           delta=0.0002)

    def test_operational_scenarios(self):
        for scenario, field, change in (
            ("overheating", "temp_c", 20),
            ("low_battery", "battery_voltage_v", -1),
            ("accelerated_decay", "altitude_km", -2),
        ):
            with self.subTest(scenario=scenario):
                simulator = stream(scenario)
                first = simulator.step()
                for _ in range(60):
                    packet = simulator.step()
                    self.assertEqual(validate_telemetry(packet), [])
                delta = packet[field] - first[field]
                self.assertGreater(delta / change, 1)

    def test_invalid_sensor_preserves_received_evidence(self):
        simulator = stream("invalid_sensor")
        for field, evidence in (("battery_voltage_v", '"NO_READING"'),
                                ("pitch_deg", "120.0"), ("temp_c", None)):
            packet = simulator.step()
            original = packet.copy()
            errors = validate_telemetry(packet)
            self.assertEqual(len(errors), 1)
            self.assertIn(field, errors[0])
            output = io.StringIO()
            with redirect_stdout(output):
                display_telemetry(packet)
            self.assertIn(json.dumps(original, indent=4), output.getvalue())
            self.assertIn(errors[0], output.getvalue())
            self.assertEqual(packet, original)
            if evidence is None:
                self.assertEqual(errors, ["Missing required field: temp_c"])
            else:
                self.assertIn(evidence, output.getvalue())
        self.assertEqual(validate_telemetry(simulator.step()), [])

    def test_dropout_skips_packets_but_state_and_clock_continue(self):
        dropout = stream("communication_dropout")
        normal = stream()
        for second in range(24):
            expected = normal.step()
            received = dropout.step()
            if 5 <= second % 12 < 8:
                self.assertIsNone(received)
            else:
                self.assertEqual(received, expected)


class ValidatorTests(unittest.TestCase):
    def test_missing_and_invalid_are_distinct(self):
        packet = stream().step()
        del packet["longitude_deg"]
        packet["latitude_deg"] = None
        errors = validate_telemetry(packet)
        self.assertIn("Missing required field: longitude_deg", errors)
        self.assertIn("latitude_deg must be a finite float.", errors)

    def test_types_ranges_and_times(self):
        for field, values in {
            "timestamp": (None, "yesterday", "2026-02-30T10:00:00Z",
                          "2026-01-01T12:00:00", "2026-01-01T12:00:00+02:00"),
            "sat_status": (" ", False),
            "temp_c": (math.nan, math.inf, -math.inf),
            "battery_voltage_v": (True, 12, -0.1, 30.1),
            "roll_deg": (180.1,), "latitude_deg": (-90.1,), "longitude_deg": (181.0,),
            "long_lat_timestamp": (-1, 2**32, 3.0, True),
        }.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    packet = stream().step()
                    packet[field] = value
                    errors = validate_telemetry(packet)
                    self.assertEqual(len(errors), 1)
                    self.assertIn(field, errors[0])

    def test_valid_boundaries_and_low_voltage(self):
        packet = stream().step()
        packet.update(timestamp="2026-01-01T00:00:00Z", roll_deg=-180.0,
                      pitch_deg=90.0, battery_voltage_v=0.0, long_lat_timestamp=0)
        self.assertEqual(validate_telemetry(packet), [])
        packet.update(battery_voltage_v=30.0, long_lat_timestamp=2**32 - 1)
        self.assertEqual(validate_telemetry(packet), [])


if __name__ == "__main__":
    unittest.main()
