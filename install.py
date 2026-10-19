"""Create a checkout-local environment. No shell profile or global Python changes."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import venv


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--hardware',action='store_true',help='Install the larger LeRobot hardware stack as well.')
    args = p.parse_args()
    if sys.version_info < (3,12):
        raise SystemExit('Arm Studio needs Python 3.12 or newer; Python 3.12 is recommended for hardware.')
    root = Path(__file__).resolve().parent
    folder = root / '.venv'
    if not folder.exists():
        venv.EnvBuilder(with_pip=True).create(folder)
    python = folder / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    requirements = 'requirements-hardware.txt' if args.hardware else 'requirements-demo.txt'
    subprocess.run([str(python),'-m','pip','install','-r',str(root / requirements)],check=True)
    print('\nInstalled. Start with:')
    print(f'"{python}" "{root / "server.py"}" --open' + ('' if args.hardware else ' --demo'))


if __name__ == '__main__':
    main()
