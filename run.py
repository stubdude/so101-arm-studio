"""Launch the installed dashboard, choosing demo when hardware support is absent."""
import json
import os
from pathlib import Path
import subprocess
import sys


def launch(root=None, argv=None):
    root = root or Path(__file__).resolve().parent
    argv = list(sys.argv[1:] if argv is None else argv)
    python = Path(os.environ.get('ARM_STUDIO_PYTHON', root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')))
    if not python.is_file():
        print('First-time setup needed. Open Setup.command (Mac) or Setup.bat (Windows),\n'
              'or run: python3.12 install.py --interactive', file=sys.stderr)
        return 1
    check = subprocess.run([str(python), '-c',
        'import importlib.util,json; print(json.dumps({m: importlib.util.find_spec(m) is not None for m in ["serial","lerobot"]}))'],
        capture_output=True, text=True)
    try:
        available = json.loads(check.stdout) if check.returncode == 0 else {}
    except ValueError:
        available = {}
    if not available.get('serial'):
        print('Installation is incomplete. Run Setup again to repair it.', file=sys.stderr)
        return 1
    hardware = '--hardware' in argv
    if hardware:
        argv.remove('--hardware')
    if hardware and '--demo' in argv:
        print('Choose either --hardware or --demo.', file=sys.stderr)
        return 1
    if hardware and not available.get('lerobot'):
        print('Real-arm support is not installed. Run: python3.12 install.py --hardware', file=sys.stderr)
        return 1
    if '--demo' not in argv and not available.get('lerobot'):
        argv.append('--demo')
    demo = '--demo' in argv
    print('\nArm Studio · ' + ('DEMO — simulated arms, no USB control' if demo else 'REAL ARMS — connect only when ready'), flush=True)
    print('Keep this window open while using the dashboard. Press Ctrl+C here to quit.\n', flush=True)
    return subprocess.call([str(python), str(root / 'server.py'), '--open', *argv], cwd=root)


if __name__ == '__main__':
    raise SystemExit(launch())
