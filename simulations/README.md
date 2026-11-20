# Drag Sail Deorbit Project mock telemetry

The simulator, validator, and tests live in `simulations/src/`. Run the examples below from the team repository root (`avionics-ground/`) using Python 3.11 or later. The simulator uses only the standard library.

```sh
python3 simulations/src/mockTelemetry.py
python3 simulations/src/mockTelemetry.py --scenario startup_checkout --steps 12 --interval 0
python3 simulations/src/mockTelemetry.py --scenario invalid_sensor --steps 4
python3 simulations/src/mockTelemetry.py --scenario communication_dropout --steps 12 --interval 0
```

The default stream prints one JSON dictionary about once per second. Press Ctrl+C to stop. Select any scenario below with `--scenario`. Each step advances exactly one simulated second, including steps during a dropout. `--steps` limits the run, `--interval` sets the real delay in seconds, and `--interval 0` runs without sleeping. For example, `--scenario low_battery --interval 0 --steps 121` shows two minutes of discharge. Terminal notices and validation messages appear on separate lines from the JSON.

| Scenario | What to look for |
| --- | --- |
| `normal` | Small changes in temperature, voltage, current, and attitude along a continuous ground track. |
| `startup_checkout` | Follow the spacecraft from Safe through Checkout to Standby while its ground position updates every second. |
| `overheating` | Temperature approaches 75 °C from 25 °C, crossing 45 °C after about a minute. |
| `low_battery` | Voltage drops from 12.4 V toward 8 V, reaching about 9.6 V after two minutes. Packets still pass structural validation. |
| `accelerated_decay` | The test removes 0.05 km of altitude each simulated second down to a 250 km floor. This prescribed descent makes trends visible quickly. A drag-sail lifetime prediction would require a separate physical model. |
| `invalid_sensor` | A four-step cycle emits a string voltage, a pitch of 120°, a packet missing temperature, and a healthy packet. The terminal prints exactly what arrived alongside each field error. |
| `communication_dropout` | Each 12-second cycle omits packets at offsets 5, 6, and 7. The display prints a local notice. The next received packet shows four seconds of movement since the last good fix. |

## Run the tests

From `avionics-ground/`, point unittest discovery at the directory containing `test_telemetry.py` and the simulator modules.

```sh
python3 -m unittest discover -s simulations/src -v
```

## Startup checkout for GND-09

The 12-step command above covers simulated seconds 0 through 11. The script applies each event before printing that second’s packet. The transition conditions come from the FSW draft. The event times are demo settings. [FSW States Draft](https://docs.google.com/spreadsheets/d/1Ccid4Vcat6-_tZspArPMWHSoKKobkyo20iwJ8V6unI0/edit?usp=drivesdk)

| Simulated time | Mock `sat_status` | Scripted conditions and events |
| --- | --- | --- |
| 0–2 s | `SAFE` (S00) | Boot with an available ground link, a healthy battery, and spin below the detumble threshold. |
| 3–7 s | `CHECKOUT` (S02) | At 3 s, a simulated ground command starts Checkout under those conditions (T02). |
| 8 s onward | `STANDBY` (S03) | At 8 s, all self-tests report success. The spacecraft automatically enters Standby (T05) and waits for another command. |

## How samples evolve

The first packet represents the start instant. `TelemetrySimulator.step()` returns a fresh dictionary (or `None` for a dropout) and advances the underlying state. Temperature closes 1/90 of the gap to its target each second. Voltage closes 1/120 of its gap. The normal targets follow the orbital phase to mimic cyclic heating and electrical load. Temperature has small additive sensor noise, current follows a smoothly varying load with small random fluctuations, and bounded sine waves supply gentle attitude changes. These are synthetic sensor patterns. The thermal constants, load, and attitude do not come from flight data.

The normal orbit uses circular two-body motion about a spherical Earth with a 6,371 km radius. The chosen altitude of 400 km and inclination of 51.6° yield a test ground track. The radius sets angular speed through `sqrt(mu / radius**3)` and tangential speed through `sqrt(mu / radius)`. This gives a period of about 92.4 minutes and a speed of 7.67 km/s. Projecting the same orbit into three axes gives the velocity vector. Subtracting Earth's rotation from the inertial longitude gives the ground track. Longitude wraps at the antimeridian. During accelerated decay the simulator updates the angular speed as the radius shrinks and includes the inward speed in the velocity vector. The Earth constants are rounded for simulation. [IERS numerical standards](https://www.iers.org/SharedDocs/Publikationen/EN/IERS/Publications/tn/TechnNote13/tn13.pdf?__blob=publicationFile&v=1) and [JPL planetary parameters](https://ssd.jpl.nasa.gov/planets/phys_par.html).

For reproducible sensor variations, pass `--seed 17`. For identical packets, also supply a fixed, timezone-aware start time in Python. Start Python from `avionics-ground/` for this import.

```python
from datetime import datetime, timezone
from simulations.src.mockTelemetry import TelemetrySimulator

sim = TelemetrySimulator(seed=17, start_time=datetime(2026, 1, 1, tzinfo=timezone.utc))
first = sim.step()
second = sim.step()
```

## Provisional FSW assumptions

- **Reference frames.** Velocity components use km/s in an Earth-centered inertial frame. Its z axis points north and its x axis passes through the ascending node, aligned with Greenwich at simulation start. Latitude is geocentric, longitude is east-positive, and altitude is height above the model sphere. Absolute ground position is synthetic. FSW still needs to confirm the inertial axes and epoch, geodetic datum, altitude reference, and attitude axis and rotation conventions.
- **Time.** The packet carries an extended ISO 8601 UTC timestamp. Position uses the same sample instant, truncated to whole Unix seconds in an unsigned 32-bit integer. The validator checks each field independently so a future packet can carry an older position fix. Confirm fix timing, epoch, resolution, and encoding with FSW.
- **Status and limits.** The uppercase status labels above are a mock convention. FSW’s telemetry draft leaves status encoding open, so the validator accepts any nonempty string with non-whitespace content. Operational alarm thresholds also await FSW confirmation. [FSW telemetry draft](https://docs.google.com/spreadsheets/d/10PXnQ42F3zZLbce1RrTY8YYNFtOQ9b4Q2tVs9JHI0fk/edit?usp=drivesdk)
- **Representation.** The validator requires all 15 fields for the local mock profile. Measurements use Python floats, the position timestamp uses an integer, and status and packet time use strings. The JSON display is for local testing. Wire format, precision, byte order, and missing-reading conventions remain open.
