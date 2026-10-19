"""Create a checkout-local environment. No global Python changes."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--hardware', action='store_true', help='Install the larger LeRobot hardware stack as well.')
    p.add_argument('--interactive', action='store_true', help='Choose demo or real-arm setup.')
    args = p.parse_args()
    if sys.version_info < (3,12):
        raise SystemExit('Please install Python 3.12 from https://www.python.org/downloads/ and reopen Setup.\n'
                         'Terminal users: run this with python3.12 (Windows: py -3.12).')
    if args.interactive and not args.hardware:
        print('\nWelcome to Arm Studio\n  1. Try the dashboard without a robot (small download)\n'
              '  2. Set up real arms (larger download; several minutes)\n')
        while True:
            choice = input('Choose 1 or 2 [1]: ').strip() or '1'
            if choice in {'1','2'}:
                args.hardware = choice == '2'
                break
            print('Enter 1 for demo or 2 for real arms.')
    if args.hardware and not shutil.which('git'):
        raise SystemExit('Real-arm setup needs Git. Install it from https://git-scm.com/downloads and reopen Setup.')
    root = Path(__file__).resolve().parent
    folder = root / '.venv'
    print('\n[1/2] Preparing your local Python environment…', flush=True)
    try:
        venv.EnvBuilder(with_pip=True).create(folder)
        python = folder / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        requirements = 'requirements-hardware.txt' if args.hardware else 'requirements-demo.txt'
        print('[2/2] Installing ' + ('real-arm support. This can take several minutes…' if args.hardware else 'demo support…'), flush=True)
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(root / requirements)], check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f'\nSetup did not finish: {exc}\nCheck the error above, then run Setup again.\n'
                         'For hardware compatibility and troubleshooting, see README.md.') from exc
    print('\nReady! Open Arm Studio.command (Mac) or Arm Studio.bat (Windows).\n'
          'Or run: python3.12 run.py' + ('' if args.hardware else ' --demo'))


if __name__ == '__main__':
    main()
