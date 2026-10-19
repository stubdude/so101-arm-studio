# Architecture and extension points

## Boundaries

- `server.py`: loopback HTTP server, static frontend, same-origin/token checks, action queue ingress, module manifest.
- `engine.py`: one worker owns both motor buses. HTTP requests never write motor registers directly. The worker samples positions, applies calibrated bounds and speed limits, handles calibration, and records joint traces.
- `paths.py` and `locking.py`: per-user data locations and cross-platform process locking.
- `cameras.py`: optional capture threads. Devices open only on explicit preview actions; the robot worker does not wait for image capture.
- `simulator.py` and `models.py`: dependency-light fake arms for development and CI. Demo mode does not import the robotics stack.
- `static/app.js`: page registry, state rendering and commands. Robot state comes from the server, never a browser-side guess.

A full following loop runs at a requested 40 Hz; actual throughput depends on the USB buses and host. The first three seconds are capped at 10 units/second. Gripper values/speeds are percentages/percentage points per second; body joints use degrees/degrees per second.

## Adding a module

1. Declare its availability honestly in `MODULES` in `server.py`.
2. Add a page and renderer to the `PAGES` registry in `static/app.js`.
3. Validate every action server-side. Commands that affect motor state must enter the existing worker queue.
4. Keep heavy training and encoding jobs in separate processes. They must not block the motor loop or bypass its ownership.
5. Add simulator/state-machine tests and request-boundary tests before enabling hardware behavior.

## Future dataset module

Introduce a timestamped frame buffer per camera and a dedicated writer consuming observations/actions from the engine. Define synchronization, dropped-frame handling, episode boundaries and export validation before calling an export a LeRobot dataset. Current recordings are intentionally joint traces only.

## Future training module

Use validated dataset manifests and a background-job interface exposing queued/running/failed/completed states, logs and checkpoint metadata. Do not accept arbitrary shell commands through HTTP. Keep model downloads and credentials separate from public configuration.

## Future policy evaluation

Policy execution needs its own explicit operating mode and shared ownership of the existing motor worker, bounded actions, stop/heartbeat handling, and a supervised hardware validation procedure. Do not add an HTTP endpoint that directly sends model output to a serial bus.

## Hardware validation

Demo tests cannot prove physical behavior. Any new motor family, platform, controller, or operating mode needs validation with supported arms, clear workspace, and conservative limits. The experimental Feetech option is a driver configuration, not a physical validation claim.
