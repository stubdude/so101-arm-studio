"""State-machine tests against simulated arms. Run: <robot python> -m unittest discover -s tests"""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine as engine_module  # noqa: E402
from engine import Engine  # noqa: E402
from simulator import FakeArm, fake_ports, SERIALS  # noqa: E402


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_lock = engine_module.LOCK
        engine_module.LOCK = Path(self.tmp.name) / 'hardware.lock'
        self.arms = {}

        def factory(role, port, ident):
            self.arms[role] = FakeArm(role)
            return self.arms[role]

        self.engine = Engine(self.tmp.name, factory, fake_ports)
        for role in ('leader', 'follower'):
            self.engine.profile[role]['serial'] = SERIALS[role]

    def tearDown(self):
        self.engine.close_ports()
        engine_module.LOCK = self.old_lock
        self.tmp.cleanup()

    def act(self, action, payload=None):
        self.engine.handle(action, payload or {})

    def test_connect_detects_holding_follower(self):
        self.act('connect')
        s = self.engine.snapshot()
        self.assertTrue(s['connected'])
        self.assertTrue(s['holding'])
        self.assertEqual(s['mode'], 'holding')
        self.assertTrue(s['leader']['calibrated'] and s['follower']['calibrated'])
        self.assertAlmostEqual(s['follower']['limits']['shoulder_lift'][1], 2300 * 180 / 4095)

    def test_arm_assignments_are_saved_for_this_computer(self):
        detected = fake_ports()
        self.act('profile', {'family':'feetech', 'leader':{'port':detected[0]['device'],'id':'alice_leader'},
                            'follower':{'port':detected[1]['device'],'id':'alice_follower'}})
        saved = json.loads((Path(self.tmp.name) / 'profile.json').read_text())
        self.assertEqual(saved['leader']['serial'], SERIALS['leader'])
        self.assertEqual(saved['follower']['model'], 'STS3215')
        self.act('connect')
        with self.assertRaisesRegex(ValueError, 'Disconnect'):
            self.act('profile', {})

    def test_duplicate_devices_and_unsafe_calibration_names_are_rejected(self):
        p = fake_ports()[0]['device']
        with self.assertRaisesRegex(ValueError, 'different USB'):
            self.act('profile', {'leader':{'port':p,'id':'leader'},'follower':{'port':p,'id':'follower'}})
        with self.assertRaisesRegex(ValueError, 'Calibration names'):
            self.act('profile', {'leader':{'port':p,'id':'../outside'}})

    def test_blank_profile_never_opens_an_arbitrary_usb_device(self):
        self.engine.profile['leader'].update(serial='', port='')
        with self.assertRaisesRegex(RuntimeError, 'Select its device'):
            self.act('connect')
        self.assertFalse(self.arms)

    def test_missing_usb_refuses_connect(self):
        self.engine.port_provider = lambda: fake_ports()[:1]
        with self.assertRaisesRegex(RuntimeError, 'Follower USB connection is missing'):
            self.act('connect')
        self.assertFalse(self.engine.state['connected'])

    def test_second_controller_is_locked_out(self):
        self.act('connect')
        other = Engine(self.tmp.name, lambda *a: FakeArm(a[0]), fake_ports)
        with self.assertRaisesRegex(RuntimeError, 'Another robot controller'):
            other.connect()

    def test_release_and_hold_require_support(self):
        self.act('connect')
        with self.assertRaisesRegex(ValueError, 'Support'):
            self.act('release')
        self.act('release', {'supported': True})
        self.assertFalse(self.engine.state['holding'])
        with self.assertRaisesRegex(ValueError, 'Support'):
            self.act('hold')
        self.act('hold', {'supported': True})
        self.assertTrue(self.engine.state['holding'])

    def test_start_requires_holding(self):
        self.act('connect')
        self.act('release', {'supported': True})
        with self.assertRaisesRegex(ValueError, 'holding'):
            self.act('start', {'control': 'full'})

    def test_full_teleoperation_is_speed_limited_and_stop_holds(self):
        self.act('connect')
        self.arms['leader'].bus.leader_override = {'shoulder_pan': 60, 'shoulder_lift': 0, 'elbow_flex': 0,
                                                   'wrist_flex': 0, 'wrist_roll': 0, 'gripper': 30}
        self.act('start', {'control': 'full'})
        self.assertEqual(self.engine.state['mode'], 'teleoperation')
        now = self.engine.previous
        for _ in range(10):
            now += .025
            self.engine.heartbeat = now
            self.engine.cycle(now)
        # First 3 s are capped at 10 deg/s: 10 cycles x 25 ms -> at most 2.5 deg.
        self.assertLessEqual(self.engine.goal['shoulder_pan'], 2.5 + 1e-6)
        self.assertGreater(self.engine.goal['shoulder_pan'], 0)
        self.act('stop')
        self.assertEqual(self.engine.state['mode'], 'holding')
        self.assertTrue(self.engine.state['holding'])
        self.assertIsNone(self.engine.goal)

    def test_wrist_twist_has_its_own_faster_limit(self):
        self.act('connect')
        bus = self.arms['leader'].bus
        bus.leader_override = {n: 0.0 for n in bus.position}
        self.act('start', {'control': 'full'})
        bus.leader_override = {**bus.leader_override, 'shoulder_pan': 90, 'wrist_roll': 90}
        self.engine.started -= 5  # past the gentle startup ramp
        now = self.engine.previous
        for _ in range(10):
            now += .025
            self.engine.heartbeat = now
            self.engine.cycle(now)
        self.assertAlmostEqual(self.engine.goal['shoulder_pan'], 45 * .25, places=3)
        self.assertAlmostEqual(self.engine.goal['wrist_roll'], 120 * .25, places=3)

    def test_starter_mode_clamps_excursion(self):
        self.act('connect')
        bus = self.arms['leader'].bus
        bus.leader_override = {n: 0.0 for n in bus.position}
        self.act('start', {'control': 'starter'})
        bus.leader_override = {**bus.leader_override, 'elbow_flex': 80}
        now = self.engine.previous
        for _ in range(400):
            now += .025
            self.engine.heartbeat = now
            self.engine.cycle(now)
        self.assertAlmostEqual(self.engine.goal['elbow_flex'], self.engine.profile['starter_travel'], places=3)

    def test_heartbeat_loss_pauses_following(self):
        self.act('connect')
        self.act('start', {'control': 'full'})
        self.engine.cycle(self.engine.heartbeat + 9)
        self.assertEqual(self.engine.state['mode'], 'holding')
        self.assertEqual(self.engine.state['events'][0]['level'], 'warning')

    def test_stop_cancels_older_queued_start_even_with_a_full_queue(self):
        self.act('connect')
        for _ in range(12):
            self.engine.submit('start', {'control':'full'})
        self.engine.submit('stop', {})
        self.engine.start_worker()
        try:
            time.sleep(.25)
            self.assertEqual(self.engine.state['mode'], 'holding')
            self.assertTrue(self.engine.jobs.empty())
            self.assertFalse(any('teleoperation started' in e['text'] for e in self.engine.state['events']))
        finally:
            self.engine.closed.set()
            self.engine.thread.join(3)

    def test_stop_does_not_cancel_a_recording_save(self):
        self.act('connect')
        self.act('start', {'control':'full'})
        self.act('record_start', {'label':'stop then save'})
        self.engine.cycle(time.monotonic())
        self.engine.submit('record_stop', {})
        self.engine.submit('stop', {})
        self.engine.start_worker()
        try:
            deadline = time.monotonic() + 2
            while not self.engine.sessions() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertEqual(self.engine.state['mode'], 'holding')
            self.assertIsNone(self.engine.state['recording'])
            self.assertEqual(self.engine.sessions()[0]['label'], 'stop then save')
        finally:
            self.engine.closed.set()
            self.engine.thread.join(3)

    def test_settings_validation_and_persistence(self):
        with self.assertRaises(ValueError):
            self.act('settings', {'joint_speeds': {'shoulder_lift': 500}})
        with self.assertRaises(ValueError):
            self.act('settings', {'joint_speeds': {'nose': 30}})
        self.act('settings', {'joint_speeds': {'elbow_flex': 30}, 'fps': 35})
        saved = json.loads((Path(self.tmp.name) / 'profile.json').read_text())
        self.assertEqual((saved['joint_speeds']['elbow_flex'], saved['joint_speeds']['wrist_roll'], saved['fps']), (30, 120, 35))
        reloaded = Engine(self.tmp.name, lambda *a: FakeArm(a[0]), fake_ports)
        self.assertEqual(reloaded.profile['joint_speeds']['elbow_flex'], 30)

    def test_old_single_speed_profile_migrates(self):
        (Path(self.tmp.name) / 'profile.json').write_text(json.dumps({'speed': 30, 'wrist_speed': 90, 'fps': 40}))
        speeds = Engine(self.tmp.name, lambda *a: FakeArm(a[0]), fake_ports).profile['joint_speeds']
        self.assertEqual((speeds['shoulder_pan'], speeds['gripper'], speeds['wrist_roll']), (30, 30, 90))

    def test_speed_limits_change_live_while_following(self):
        self.act('connect')
        bus = self.arms['leader'].bus
        bus.leader_override = {n: 0.0 for n in bus.position}
        self.act('start', {'control': 'full'})
        bus.leader_override = {**bus.leader_override, 'elbow_flex': 90}
        self.engine.started -= 5
        self.act('settings', {'joint_speeds': {'elbow_flex': 80}})
        self.assertEqual(self.engine.state['mode'], 'teleoperation')
        now = self.engine.previous
        for _ in range(10):
            now += .025
            self.engine.heartbeat = now
            self.engine.cycle(now)
        self.assertAlmostEqual(self.engine.goal['elbow_flex'], 80 * .25, places=3)
        with self.assertRaisesRegex(ValueError, 'Stop following'):
            self.act('settings', {'fps': 30})

    def test_recording_writes_trace_and_metadata(self):
        self.act('connect')
        self.act('record_start', {'label': 'pick & place'})
        now = time.monotonic()
        for i in range(5):
            self.engine.cycle(now + i * .025)
        self.act('record_stop')
        sessions = self.engine.sessions()
        self.assertEqual(sessions[0]['label'], 'pick & place')
        self.assertEqual(sessions[0]['frames'], 5)
        lines = (Path(self.tmp.name) / 'sessions' / (sessions[0]['id'] + '.jsonl')).read_text().splitlines()
        self.assertEqual(len(lines), 5)
        self.assertIn('leader', json.loads(lines[0]))

    def test_calibration_cancel_restores_and_blocks_following(self):
        self.act('connect')
        with self.assertRaisesRegex(ValueError, 'Support'):
            self.act('calibrate', {'role': 'follower'})
        self.act('calibrate', {'role': 'follower', 'supported': True})
        self.assertEqual(self.engine.state['mode'], 'calibrating')
        self.assertFalse(self.engine.state['holding'])
        with self.assertRaises(ValueError):
            self.act('start', {'control': 'full'})
        self.act('calibration_pose')
        self.engine.cycle(time.monotonic())
        with self.assertRaisesRegex(ValueError, 'more of their range'):
            self.act('calibration_finish')
        self.act('calibration_cancel')
        self.assertIsNone(self.engine.state['calibration'])
        self.assertTrue(self.engine.state['follower']['calibrated'])

    def test_repeated_bus_failures_release_ports(self):
        self.act('connect')
        self.engine.start_worker()
        try:
            def broken(*a, **kw):
                raise OSError('USB gone')
            with self.engine.mutex:
                self.engine.arms['follower'].bus.sync_read = broken
            deadline = time.monotonic() + 3
            while self.engine.state['connected'] and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertFalse(self.engine.state['connected'])
            self.assertEqual(self.engine.state['mode'], 'error')
            errors = [e for e in self.engine.state['events'] if e['level'] == 'error']
            self.assertEqual(len(errors), 2)  # one interruption notice, one release notice; no flood
        finally:
            self.engine.closed.set()
            self.engine.thread.join(timeout=3)


if __name__ == '__main__':
    unittest.main()
