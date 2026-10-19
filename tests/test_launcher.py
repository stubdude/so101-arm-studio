"""First launch must select a usable mode without opening any hardware."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run import launch


class LauncherTest(unittest.TestCase):
    def run_launcher(self, modules, argv):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            python = root / 'test-python'
            python.touch()
            with patch.dict('os.environ', {'ARM_STUDIO_PYTHON': str(python)}), \
                 patch('run.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(modules))), \
                 patch('run.subprocess.call', return_value=0) as call:
                code = launch(root, argv)
                command = call.call_args[0][0] if call.called else []
                return code, command

    def test_demo_only_install_launches_demo_without_extra_flags(self):
        code, command = self.run_launcher({'serial':True,'lerobot':False}, [])
        self.assertEqual(code, 0)
        self.assertIn('--demo', command)

    def test_hardware_install_can_launch_real_or_practice(self):
        for argv, demo in [([],False), (['--hardware'],False), (['--demo'],True)]:
            code, command = self.run_launcher({'serial':True,'lerobot':True}, argv)
            self.assertEqual(code, 0)
            self.assertEqual('--demo' in command, demo)
            self.assertNotIn('--hardware', command)

    def test_incomplete_install_or_explicit_missing_hardware_does_not_start(self):
        for modules, argv in [({'serial':False},[]), ({'serial':True,'lerobot':False},['--hardware']),
                              ({'serial':True,'lerobot':True},['--demo','--hardware'])]:
            code, command = self.run_launcher(modules, argv)
            self.assertEqual(code, 1)
            self.assertEqual(command, [])


if __name__ == '__main__':
    unittest.main()
