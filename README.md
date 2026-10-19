# Arm Studio · SO-101

A local dashboard for a leader/follower SO-101 setup: teleoperation, live joint status, per-joint speed controls, guided calibration, optional camera previews, and motion-session recording.

**Runs on your computer. No account, hosted backend, or cloud connection is needed to operate the arms.**

## First time: setup, then launch

Install **[Python 3.12](https://www.python.org/downloads/)** first (Windows: enable the Python launcher during installation).

1. [Download the app ZIP](https://github.com/stubdude/so101-arm-studio/archive/refs/heads/main.zip) and extract it to a local folder. Or clone this repository with Git.
2. **Mac:** open `Setup.command`. **Windows:** open `Setup.bat`. Choose **1 — demo** to try it without a robot, or **2 — real arms** to install hardware support. Real-arm setup also needs [Git](https://git-scm.com/downloads/).
3. When setup says **Ready**, open **Arm Studio.command** (Mac) or **Arm Studio.bat** (Windows). Keep its terminal window open while using the dashboard.
4. Follow the **next step** card at the top of Operate. In demo, click **Connect arms → Start teleoperation** (the simulated follower starts holding). Try **Stop & hold** before using real arms.

macOS may ask before opening a downloaded script. If you trust this source, use Finder’s **Open** command; if macOS blocks it, use the terminal commands below. You do not need to disable system security settings.

### Terminal / Linux

```sh
git clone https://github.com/stubdude/so101-arm-studio.git
cd so101-arm-studio
python3.12 install.py --interactive
python3.12 run.py
```

On Windows use `py -3.12` instead of `python3.12`. For unattended demo setup use `python3.12 install.py`; for real arms add `--hardware`.

The launcher opens the browser at [127.0.0.1:8765](http://127.0.0.1:8765). A demo-only installation automatically opens demo mode; a hardware installation opens real-arm mode. Add `--demo` to practice after installing hardware, or `--hardware` to require real-arm support explicitly. Stop the current server with **Ctrl+C** before switching modes.

Demo mode never opens serial ports or reads your real calibration files. Camera preview is separate and opens a real camera only if you explicitly start it.

## Use your own arms

```sh
python3.12 install.py --hardware
python3.12 run.py --hardware
```

Hardware installation downloads the larger PyTorch/LeRobot stack from the official Hiwonder fork, pinned to a specific revision. It can take several minutes and needs Git and free disk space. The lightweight demo is a good first installation check.

1. Clamp the bases, leave space around the follower, and connect each arm by a **USB data cable**. Connect the appropriate power supplies for your specific motors. Do not assume all SO-101 variants use the same voltage.
2. Open **Robot settings**. Select the motor family and assign the leader and follower USB devices. Unplug one USB cable at a time to identify them if necessary.
3. Choose calibration names. To reuse an existing LeRobot calibration on this computer, use the **same IDs** as your earlier calibration commands. Otherwise use the dashboard's supported, guided calibration on each arm. No other user's calibration files are included.
4. Click **Connect arms**. If the follower is released, support its forearm and select **Enable holding**. Do not let go until it holds steadily.
5. Keep hands clear of the follower, hold the leader comfortably, and choose **Starter** or **Full range** before starting.

Connecting checks the motors; it does **not** automatically start following or enable a released follower. Full mode gradually matches the leader's current pose. Starter mode copies small relative changes around the starting poses. These modes behave differently when the arms start in different positions.

### Start it again

- macOS: double-click **Arm Studio.command**, or run `python3 run.py`.
- Windows: double-click **Arm Studio.bat**, or run `py -3.12 run.py`.
- Linux: run `python3 run.py`.
- Demo: add `--demo` to the command. Stop the current server before switching modes.

The launchers use the checkout's `.venv`. For an existing environment, set `ARM_STUDIO_PYTHON` to its Python executable, or run `python server.py --open` from that environment.

## What works where?

| Environment | Status |
| --- | --- |
| Apple Silicon macOS + Hiwonder HX-10HM/HX-30HM | Original control core exercised on real arms; public app also has simulator/API tests. Each new setup needs its own device assignment and calibration. |
| Linux x86-64 | Portable code and CI simulator tests; real USB/motor/camera behavior still needs hardware validation. Serial permissions may require membership in `dialout` or your distribution's equivalent. |
| Windows x86-64 | Portable paths/locking/launcher and CI simulator tests; real hardware still needs validation. USB serial drivers and camera permissions depend on your board. |
| Intel macOS or ARM Linux/Windows | Lightweight demo may work with Python 3.12. Hardware installation is **not claimed supported**: the pinned PyTorch/codec dependencies may lack suitable wheels. |
| Feetech STS3215 SO-101 | An experimental motor-family option uses the vendor fork's Feetech driver; it has **not** been physically validated here. |

CI passing checks the application with simulated arms. It does not certify motor wiring, physical limits, power supplies, or real-time performance on another computer. This is a personal robot control tool, not a certified safety controller.

## Controls and shutdown

- **Stop & hold** (or Escape in the page) stops following and retains follower holding when communication is available.
- If the page closes or its heartbeat is lost for eight seconds, following pauses. Keep the control tab open during use.
- **Release** disables follower holding only after you acknowledge supporting it. Support its weight before releasing or removing motor power.
- To finish: stop following, support/rest the follower, remove motor power, and then disconnect USB. A browser button cannot physically disconnect the power supply.
- Stop other control scripts before connecting this dashboard. The dashboard uses a process lock to prevent competing Arm Studio instances; third-party programs may not honor that lock.
- Per-joint speed sliders apply live. Start conservatively; wrist cables must remain slack.

## Cameras, recording, and ML

**Available:** two optional camera profiles (scene/wrist), explicit camera preview, joint-only JSONL session recording and download, and a module-oriented workspace.

**Not yet implemented:** synchronized camera/action LeRobot datasets, training-job execution, model checkpoints, or autonomous policy deployment. Joint traces are not represented as training-ready LeRobot datasets. The Learning page marks these as planned.

Camera indices can change when USB devices are reconnected. Preview each view before use. macOS may request camera permission for the Python/Terminal process. No camera opens automatically at dashboard startup.

## Local data and privacy

Settings, camera profiles, session traces, and the lock file stay outside the checkout:

- macOS: `~/Library/Application Support/Arm Studio`
- Linux: `$XDG_DATA_HOME/arm-studio` or `~/.local/share/arm-studio`
- Windows: `%LOCALAPPDATA%\Arm Studio`

Set `ARM_STUDIO_DATA_DIR` to override the data directory. LeRobot stores arm calibration in its own per-user calibration cache. Demo state is kept in a separate `demo` directory. Back up local data you want to retain; do not commit recordings, calibration, device IDs, or credentials to a public repository.

The server binds only to `127.0.0.1`; writes require a same-origin browser request and a per-process token. Do not expose or reverse-proxy it onto a LAN or the internet without adding authentication and reviewing the control model.

## Development

```sh
python3 install.py
# Activate .venv, or call its Python directly:
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python server.py --demo
node --check static/app.js  # optional JavaScript syntax check
```

On Windows the environment interpreter is `.venv\Scripts\python.exe`. CI runs simulator and API tests on macOS, Windows, and Ubuntu using Python 3.12. It never controls physical hardware.

Read [Architecture and extension points](docs/architecture.md) before adding dataset, training, or policy-execution modules.

## Troubleshooting

- **No USB device:** use a data-capable cable, check the hub and driver, then use **Discard edits / refresh USB** in Robot settings. Devices without serial numbers use their selected port, which can change on reconnection.
- **Calibration mismatch:** select the right motor family and existing calibration names, or recalibrate with the arm supported. Do not run motor-ID setup on an assembled, already configured arm without its manufacturer's procedure.
- **Hardware imports fail:** install the hardware requirements with Python 3.12 and check the platform table. Some Linux camera/video packages also require OS-level video libraries.
- **Dependency download/build fails:** the hardware stack is pinned, not guaranteed available on every CPU/OS. Keep the error log and open an issue with OS, CPU, Python version, and motor family, without personal paths or device IDs.
- **Port 8765 busy:** open the existing dashboard or stop its server. `server.py --port 8766 --open` uses another port.
- **Cloud-synced checkout:** keep the checkout/environment downloaded locally; offloaded executable files can prevent startup.

## License and attribution

This application is MIT licensed. Hardware dependencies are separate projects with their own licenses, including the Apache-2.0 [Hiwonder LeRobot fork](https://github.com/Hiwonder-official/hiwonder-SoArm-101), based on [Hugging Face LeRobot](https://github.com/huggingface/lerobot). This repository does not include or redistribute that dependency's source or pretrained models. It is not an official Hiwonder or Hugging Face product.
