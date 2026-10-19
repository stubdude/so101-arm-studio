"""Per-user application state, never part of the source checkout."""
import os
import sys
from pathlib import Path


def data_directory():
    override = os.environ.get('ARM_STUDIO_DATA_DIR')
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'Arm Studio'
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'Arm Studio'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share')) / 'arm-studio'
