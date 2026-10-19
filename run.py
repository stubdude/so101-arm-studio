"""Use the installed local environment and open the dashboard."""
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parent
python = Path(os.environ.get('ARM_STUDIO_PYTHON', root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')))
if not python.is_file():
    raise SystemExit('Run python install.py first (add --hardware to control real arms).')
raise SystemExit(subprocess.call([str(python),str(root / 'server.py'),'--open',*sys.argv[1:]],cwd=root))
