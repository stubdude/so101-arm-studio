"""Single-owner robot engine. Only this worker touches the serial buses."""
import copy
import json
import math
import os
import queue
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from serial.tools import list_ports
import locking
from paths import data_directory

DATA = data_directory()
LOCK = DATA / 'hardware.lock'
JOINTS = ['shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex', 'wrist_roll', 'gripper']
LABELS = ['Base swivel', 'Shoulder lift', 'Elbow', 'Wrist bend', 'Wrist twist', 'Gripper']
# Per-joint speed caps (degrees/s; gripper in percentage points/s). Wrist twist has little inertia and a full turn of travel.
SPEED_DEFAULTS = {'shoulder_pan': 45, 'shoulder_lift': 45, 'elbow_flex': 45, 'wrist_flex': 45, 'wrist_roll': 120, 'gripper': 45}
SPEED_MAX = {'shoulder_pan': 90, 'shoulder_lift': 90, 'elbow_flex': 90, 'wrist_flex': 150, 'wrist_roll': 180, 'gripper': 150}
SPEED_MIN = 5
DEFAULT = {'name': 'My SO-101', 'family': 'hiwonder', 'fps': 40, 'joint_speeds': SPEED_DEFAULTS, 'starter_travel': 10,
           'leader': {'serial': '', 'port': '', 'id': 'my_leader_arm', 'model': 'HX-10HM'},
           'follower': {'serial': '', 'port': '', 'id': 'my_follower_arm', 'model': 'HX-30HM'}}


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def ports():
    return [{'device': p.device, 'serial': p.serial_number, 'description': p.description}
            for p in list_ports.comports() if p.vid is not None]


def factory(role, port, ident, family='hiwonder'):
    try:
        from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
        from lerobot.teleoperators.so_leader import SO101Leader, SO101LeaderConfig
    except ImportError as exc:
        raise RuntimeError('Hardware dependencies are missing. Run install.py --hardware, then restart.') from exc
    model = 'hx30hm' if family == 'hiwonder' else 'sts3215'
    if role == 'leader':
        return SO101Leader(SO101LeaderConfig(port=port, id=ident, motor_model=model))
    return SO101Follower(SO101FollowerConfig(port=port, id=ident, motor_model=model))


class Engine:
    def __init__(self, data_dir=DATA, arm_factory=factory, port_provider=ports):
        self.data = Path(data_dir)
        self.data.mkdir(parents=True, exist_ok=True)
        (self.data / 'sessions').mkdir(exist_ok=True)
        self.profile = copy.deepcopy(DEFAULT)
        if (self.data / 'profile.json').exists():
            saved = json.loads((self.data / 'profile.json').read_text())
            speeds = {**SPEED_DEFAULTS, **saved.pop('joint_speeds', {})}
            if 'speed' in saved:  # profiles saved before per-joint limits
                speeds.update({n: saved['speed'] for n in JOINTS if n != 'wrist_roll'})
            speeds['wrist_roll'] = saved.get('wrist_speed', speeds['wrist_roll'])
            for old in ('speed', 'wrist_speed'):
                saved.pop(old, None)
            self.profile.update(saved, joint_speeds=speeds)
        self.factory, self.port_provider = arm_factory, port_provider
        self.arms, self.lock_file, self.cal, self.record = {}, None, None, None
        self.jobs = queue.PriorityQueue(maxsize=12)
        self.cancel_before = 0
        self.mutex = threading.RLock()
        self.closed = threading.Event()
        self.thread = None
        self.heartbeat = time.monotonic()
        self.previous = self.started = 0
        self.failures = 0
        self.lead_start = self.follow_start = self.goal = None
        self.sensor_at = self.sample_at = 0
        self.history = deque(maxlen=120)
        self.state = {'mode': 'disconnected', 'connected': False, 'holding': None, 'control': 'full',
                      'profile': self.profile, 'leader': {}, 'follower': {}, 'calibration': None,
                      'health': {}, 'history': [], 'events': [], 'busy': False, 'error': None,
                      'recording': None, 'fps': 0, 'revision': 0}
        self.rate_start, self.rate_count = time.monotonic(), 0
        self.event('info', 'Dashboard ready. Connect your arms to begin.')

    def event(self, level, text):
        self.state['events'].insert(0, {'time': datetime.now(timezone.utc).isoformat(), 'level': level, 'text': text})
        del self.state['events'][40:]

    def snapshot(self):
        # Port listing and session reads stay outside the lock so polling never stalls the control loop.
        detected, saved = self.port_provider(), self.sessions()
        with self.mutex:
            result = copy.deepcopy(self.state)
            result['history'] = list(self.history)
        result['ports'], result['sessions'] = detected, saved
        result['time'] = datetime.now(timezone.utc).isoformat()
        return result

    def sessions(self):
        result = []
        for p in sorted((self.data / 'sessions').glob('*.json'), reverse=True):
            try:
                result.append(json.loads(p.read_text()))
            except (ValueError, OSError):
                pass
        return result[:30]

    def submit(self, action, payload):
        if action not in {'connect', 'disconnect', 'hold', 'release', 'start', 'stop', 'settings', 'profile',
                          'calibrate', 'calibration_pose', 'calibration_finish', 'calibration_cancel',
                          'record_start', 'record_stop'}:
            raise ValueError('Unknown action')
        stamp = time.monotonic_ns()
        if action == 'stop':
            self.cancel_before = stamp
        try:
            self.jobs.put_nowait((0 if action == 'stop' else 10, stamp, action, payload))
        except queue.Full:
            if action != 'stop':
                raise
            # Stop must remain available even when ordinary commands fill the queue.
            with self.jobs.mutex:
                saving = next((job for job in self.jobs.queue if job[2] == 'record_stop'), None)
                self.jobs.queue.clear()
            self.jobs.put_nowait((0, stamp, action, payload))
            if saving:
                self.jobs.put_nowait(saving)

    def start_worker(self):
        self.thread = threading.Thread(target=self.run, name='robot-owner', daemon=True)
        self.thread.start()

    def run(self):
        while not self.closed.is_set():
            tick = time.monotonic()
            with self.mutex:
                try:
                    try:
                        _, stamp, action, payload = self.jobs.get_nowait()
                        if action not in {'stop', 'record_stop'} and stamp < self.cancel_before:
                            action = None
                    except queue.Empty:
                        action = None
                    if action:
                        self.state['busy'] = True
                        try:
                            self.handle(action, payload)
                            self.state['error'] = None
                        except Exception as exc:
                            self.state['error'] = str(exc)
                            self.event('error', str(exc))
                        finally:
                            self.state['busy'] = False
                    self.cycle(tick)
                    self.failures = 0
                except Exception as exc:
                    self.cancel_before = time.monotonic_ns()
                    self.failures += 1
                    if self.failures == 1:
                        self.state['error'] = 'Motor communication interrupted. Following has stopped. ' + str(exc)
                        self.event('error', 'Motor communication interrupted; check USB and power connections.')
                    try:
                        self.stop()
                    except Exception:
                        self.state['holding'] = None
                        self.state['mode'] = 'error'
                    if self.failures >= 5:
                        self.close_ports()
                        self.cal = self.state['calibration'] = None
                        self.state.update(connected=False, mode='error', holding=None)
                        self.event('error', 'USB control released after repeated failures. Check cables and power, then reconnect.')
                        self.failures = 0
                self.state['revision'] += 1
            self.closed.wait(max(.001, 1 / self.profile['fps'] - (time.monotonic() - tick)))
        with self.mutex:
            try:
                self.disconnect()
            except Exception:
                self.close_ports()

    def acquire(self):
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        self.lock_file = open(LOCK, 'a+b')
        try:
            locking.acquire(self.lock_file)
        except OSError:
            self.lock_file.close()
            self.lock_file = None
            raise RuntimeError('Another robot controller is using these arms. Stop that session first.')

    def close_ports(self):
        for arm in self.arms.values():
            if arm.bus.is_connected:
                arm.bus.disconnect(disable_torque=False)
        self.arms = {}
        if self.lock_file:
            locking.release(self.lock_file)
            self.lock_file.close()
            self.lock_file = None

    def connect(self):
        if self.state['connected']:
            return
        self.acquire()
        try:
            available = self.port_provider()
            selected = {}
            for role in ('leader', 'follower'):
                spec = self.profile[role]
                matches = [p['device'] for p in available if
                           (spec.get('serial') and p['serial'] == spec['serial']) or
                           (not spec.get('serial') and spec.get('port') and p['device'] == spec['port'])]
                if len(matches) != 1:
                    raise RuntimeError(f'{role.title()} USB connection is missing. Select its device in Robot settings; check its cable and hub.')
                selected[role] = matches[0]
            if selected['leader'] == selected['follower']:
                raise ValueError('Leader and follower must use different USB devices.')
            for role in ('leader', 'follower'):
                spec = self.profile[role]
                port = selected[role]
                arm = (factory(role, port, spec['id'], self.profile['family']) if self.factory is factory
                       else self.factory(role, port, spec['id']))
                self.arms[role] = arm
                arm.bus.connect()
                # Retry isolated packet loss, including calibration-register reads.
                original_read, original_sync = arm.bus.read, arm.bus.sync_read
                def retry_read(*a, _read=original_read, **kw):
                    kw['num_retry'] = max(2, kw.get('num_retry', 0))
                    return _read(*a, **kw)
                def retry_sync(*a, _read=original_sync, **kw):
                    kw['num_retry'] = max(2, kw.get('num_retry', 0))
                    return _read(*a, **kw)
                arm.bus.read, arm.bus.sync_read = retry_read, retry_sync
                valid = arm.bus.is_calibrated
                self.state[role] = {'port': port, 'model': spec['model'], 'calibrated': valid,
                                    'positions': {}, 'raw': {}, 'limits': self.limits(arm)}
            self.state['connected'] = True
            self.state['holding'] = all(self.arms['follower'].bus.read('Torque_Enable', n) == 1 for n in JOINTS)
            self.state['mode'] = 'holding' if self.state['holding'] else 'ready'
            self.sample()
            self.event('success', 'Both arms connected. Saved calibration checked.')
        except Exception as exc:
            self.close_ports()
            self.state['connected'] = False
            self.state['mode'] = 'disconnected'
            raise RuntimeError('Could not connect the motors. Confirm both 12V adapters are powered. ' + str(exc)) from exc

    @staticmethod
    def limits(arm):
        result = {}
        for n, c in arm.calibration.items():
            span = c.range_max - c.range_min
            result[n] = [0, 100] if n == 'gripper' else [-span * 180 / 4095, span * 180 / 4095]
        return result

    def sample(self):
        for role, arm in self.arms.items():
            info = self.state[role]
            valid = info['calibrated'] and not (self.cal and self.cal['role'] == role)
            values = arm.bus.sync_read('Present_Position', normalize=valid)
            info['positions' if valid else 'raw'] = values
            if not valid:
                info['positions'] = {}
        self.sample_at = time.monotonic()

    def stop(self):
        if not self.state['connected']:
            self.state['mode'] = 'disconnected'
            self.goal = None
            return
        if self.state['connected'] and self.state['holding']:
            bus = self.arms['follower'].bus
            raw = bus.sync_read('Present_Position', normalize=False)
            bus.sync_write('Goal_Position', raw, normalize=False, num_retry=2)
        self.state['mode'] = 'calibrating' if self.cal else ('holding' if self.state['holding'] else 'ready')
        self.goal = None

    def hold(self, supported):
        if self.cal:
            raise ValueError('Finish or cancel calibration first.')
        if not self.state['holding'] and not supported:
            raise ValueError('Support the follower before enabling holding.')
        self.stop()
        r = self.arms['follower']
        if not self.state['holding']:
            r.bus.disable_torque()
            current = r.bus.sync_read('Present_Position', normalize=False)
            r.bus.sync_write('Goal_Position', current, normalize=False, num_retry=2)
            r.configure()
        self.state['holding'] = all(r.bus.read('Torque_Enable', n) == 1 for n in JOINTS)
        if not self.state['holding']:
            raise RuntimeError('Holding could not be verified. Keep the follower supported.')
        self.state['mode'] = 'holding'
        self.event('success', 'Follower holding its current pose.')

    def disconnect(self):
        if self.cal:
            self.cancel_calibration()
        self.stop_recording()
        if self.state['connected']:
            self.stop()
        self.close_ports()
        self.state['connected'] = False
        self.state['mode'] = 'disconnected'
        self.event('info', 'Disconnected. Support the follower before removing motor power.')

    def begin(self, control):
        if self.cal:
            raise ValueError('Finish calibration before following.')
        if not self.state['holding']:
            raise ValueError('Enable follower holding before starting.')
        if not all(self.state[r]['calibrated'] for r in ('leader', 'follower')):
            raise ValueError('Calibrate both arms before following.')
        if control not in ('full', 'starter'):
            raise ValueError('Choose full or starter control.')
        self.arms['leader'].configure()
        self.sample()
        self.lead_start = self.state['leader']['positions'].copy()
        self.follow_start = self.state['follower']['positions'].copy()
        self.goal = self.follow_start.copy()
        self.started = self.previous = self.heartbeat = time.monotonic()
        self.state['control'] = control
        self.state['mode'] = 'teleoperation'
        self.event('success', f'{control.title()} teleoperation started. Stop keeps the follower holding.')

    def settings(self, payload):
        # Speed limits apply live, even while following; rate and starter range need a stop first.
        updates = {}
        for key, low, high in [('fps', 20, 50), ('starter_travel', 3, 30)]:
            if key in payload:
                if self.state['mode'] == 'teleoperation':
                    raise ValueError('Stop following before changing the update rate or starter range.')
                value = float(payload[key])
                if not math.isfinite(value) or not low <= value <= high:
                    raise ValueError(f'{key} must be between {low} and {high}.')
                updates[key] = value
        speeds = payload.get('joint_speeds', {})
        if not isinstance(speeds, dict):
            raise ValueError('joint_speeds must be an object.')
        new_speeds = dict(self.profile['joint_speeds'])
        for n, v in speeds.items():
            if n not in SPEED_MAX:
                raise ValueError(f'Unknown joint {n}.')
            value = float(v)
            if not math.isfinite(value) or not SPEED_MIN <= value <= SPEED_MAX[n]:
                raise ValueError(f'{n} speed must be between {SPEED_MIN} and {SPEED_MAX[n]}.')
            new_speeds[n] = value
        self.profile.update(updates, joint_speeds=new_speeds)
        temporary = self.data / 'profile.tmp'
        temporary.write_text(json.dumps(self.profile, indent=2))
        os.replace(temporary, self.data / 'profile.json')
        if updates:
            self.event('info', 'Control preferences saved.')
        if speeds:
            self.event('info', 'Speed limits updated: ' + ', '.join(f'{n.replace("_", " ")} {new_speeds[n]:g}' for n in speeds) + '.')

    def calibrate(self, role, supported):
        if not supported:
            raise ValueError('Support the selected arm before calibration.')
        if role not in self.arms or self.cal:
            raise ValueError('Choose a connected arm; finish the current calibration first.')
        self.stop()
        self.stop_recording()
        arm = self.arms[role]
        saved = copy.deepcopy(arm.calibration)
        arm.bus.disable_torque()
        for n in JOINTS:
            arm.bus.write('Operating_Mode', n, 0)
        if role == 'follower':
            self.state['holding'] = False
        self.cal = {'role': role, 'phase': 'pose', 'saved': saved, 'offsets': {}, 'mins': {}, 'maxes': {}}
        self.state['calibration'] = {'role': role, 'phase': 'pose', 'ranges': {}}
        self.state['mode'] = 'calibrating'
        self.state[role]['calibrated'] = False
        self.event('info', f'{role.title()} released. Keep it supported and set the starting pose.')

    def calibration_pose(self):
        if not self.cal or self.cal['phase'] != 'pose':
            raise ValueError('Set the calibration starting pose first.')
        arm = self.arms[self.cal['role']]
        self.cal['offsets'] = arm.bus.set_half_turn_homings()
        values = arm.bus.sync_read('Present_Position', normalize=False)
        names = [n for n in JOINTS if n != 'wrist_roll']
        self.cal['mins'] = {n: values[n] for n in names}
        self.cal['maxes'] = self.cal['mins'].copy()
        self.cal['phase'] = 'range'
        self.state['calibration']['phase'] = 'range'
        self.event('info', 'Starting pose recorded. Move five joints through their usable ranges; skip wrist twist.')

    def finish_calibration(self):
        if not self.cal or self.cal['phase'] != 'range':
            raise ValueError('Record the ranges before saving calibration.')
        c, role = self.cal, self.cal['role']
        missing = [n for n in c['mins'] if c['maxes'][n] - c['mins'][n] < 100]
        if missing:
            raise ValueError('Move these joints through more of their range: ' + ', '.join(missing))
        arm = self.arms[role]
        if self.factory is factory:
            from lerobot.motors.motors_bus import MotorCalibration
        else:
            from models import Calibration as MotorCalibration
        result = {n: MotorCalibration(id=m.id, drive_mode=0, homing_offset=c['offsets'][n],
                  range_min=0 if n == 'wrist_roll' else c['mins'][n],
                  range_max=4095 if n == 'wrist_roll' else c['maxes'][n]) for n, m in arm.bus.motors.items()}
        arm.bus.write_calibration(result)
        arm.calibration = result
        arm._save_calibration()
        if not arm.bus.is_calibrated:
            raise RuntimeError('Calibration verification failed. Keep the arm supported.')
        self.cal = self.state['calibration'] = None
        self.state[role]['calibrated'] = True
        self.state[role]['limits'] = self.limits(arm)
        self.state['mode'] = 'holding' if self.state['holding'] else 'ready'
        self.event('success', f'{role.title()} calibration saved and verified. The calibrated arm stays released.')

    def cancel_calibration(self):
        if not self.cal:
            return
        arm = self.arms[self.cal['role']]
        role, saved = self.cal['role'], self.cal['saved']
        if saved:
            arm.bus.write_calibration(saved)
            arm.calibration = saved
        self.state[role]['calibrated'] = bool(saved) and arm.bus.is_calibrated
        self.cal = self.state['calibration'] = None
        self.state['mode'] = 'holding' if self.state['holding'] else 'ready'
        self.event('info', 'Calibration cancelled; previous calibration restored. Arm stays released.')

    def start_recording(self, label):
        if self.record:
            raise ValueError('A recording is already running.')
        if self.cal:
            raise ValueError('Finish calibration before recording.')
        label = str(label).strip()[:80] or 'Untitled motion'
        ident = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + str(time.monotonic_ns())[-6:]
        stream = open(self.data / 'sessions' / (ident + '.jsonl'), 'w')
        self.record = {'id': ident, 'label': label, 'frames': 0, 'started': time.monotonic(), 'stream': stream,
                       'created': datetime.now(timezone.utc).isoformat()}
        self.state['recording'] = {k: v for k, v in self.record.items() if k not in ('stream', 'started')}
        self.event('success', 'Joint motion recording started. Camera frames are not included.')

    def stop_recording(self):
        if not self.record:
            return
        r = self.record
        r['stream'].close()
        meta = {k: v for k, v in r.items() if k not in ('stream', 'started')}
        meta.update(duration=round(time.monotonic() - r['started'], 2), format='joint-trace-jsonl', cameras=False)
        (self.data / 'sessions' / (r['id'] + '.json')).write_text(json.dumps(meta, indent=2))
        self.record = self.state['recording'] = None
        self.event('success', f'Recording saved: {meta["frames"]} frames.')

    def handle(self, action, p):
        if action == 'profile':
            return self.save_profile(p)
        if action == 'connect':
            return self.connect()
        if action == 'settings':
            return self.settings(p)
        if action == 'stop':
            self.stop()
            self.event('info', 'Following stopped; follower remains holding when enabled.')
            return
        if action == 'disconnect':
            return self.disconnect()
        if not self.state['connected']:
            raise ValueError('Connect both arms first.')
        if action == 'hold':
            return self.hold(p.get('supported') is True)
        if action == 'release':
            if p.get('supported') is not True:
                raise ValueError('Support the follower before releasing its motors.')
            if self.cal:
                raise ValueError('Finish or cancel calibration before release.')
            self.stop()
            self.arms['follower'].bus.disable_torque()
            self.state.update(holding=False, mode='ready')
            self.event('info', 'Follower released. Keep it supported or resting safely.')
        elif action == 'start':
            self.begin(p.get('control', 'full'))
        elif action == 'calibrate':
            self.calibrate(p.get('role'), p.get('supported') is True)
        elif action == 'calibration_pose':
            self.calibration_pose()
        elif action == 'calibration_finish':
            self.finish_calibration()
        elif action == 'calibration_cancel':
            self.cancel_calibration()
        elif action == 'record_start':
            self.start_recording(p.get('label', ''))
        elif action == 'record_stop':
            self.stop_recording()

    def save_profile(self, payload):
        import re
        if self.state['connected']:
            raise ValueError('Disconnect USB control before changing arm assignments.')
        family = payload.get('family', self.profile['family'])
        if family not in ('hiwonder', 'feetech'):
            raise ValueError('Choose Hiwonder HX or Feetech STS motors.')
        updated = copy.deepcopy(self.profile)
        updated['family'] = family
        available = self.port_provider()
        selected = []
        for role in ('leader', 'follower'):
            spec = payload.get(role)
            if not isinstance(spec, dict):
                raise ValueError('Assign both the leader and follower.')
            port = spec.get('port')
            matches = [p for p in available if p['device'] == port]
            if len(matches) != 1:
                raise ValueError(f'Select the detected {role} USB device.')
            ident = str(spec.get('id', '')).strip()
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,60}', ident):
                raise ValueError('Calibration names use 1–60 letters, digits, underscores or hyphens.')
            selected.append(port)
            updated[role] = {'serial': matches[0].get('serial') or '', 'port': port, 'id': ident,
                             'model': ('HX-10HM' if role == 'leader' else 'HX-30HM') if family == 'hiwonder' else 'STS3215'}
        if len(set(selected)) != 2:
            raise ValueError('Leader and follower must use different USB devices.')
        self.profile.clear()
        self.profile.update(updated)
        temporary = self.data / 'profile.tmp'
        temporary.write_text(json.dumps(self.profile, indent=2))
        os.replace(temporary, self.data / 'profile.json')
        self.event('success', 'Arm assignments saved. Connect your arms when powered and ready.')

    def cycle(self, now):
        if not self.state['connected']:
            return
        following = self.state['mode'] == 'teleoperation'
        if following and now - self.heartbeat > 8:
            self.stop()
            self.event('warning', 'Dashboard heartbeat lost. Following paused and the follower stays holding.')
            following = False
        if following or self.cal or now - self.sample_at > .2:
            self.sample()
        if self.cal and self.cal['phase'] == 'range':
            raw = self.state[self.cal['role']]['raw']
            for n in self.cal['mins']:
                self.cal['mins'][n] = min(self.cal['mins'][n], raw[n])
                self.cal['maxes'][n] = max(self.cal['maxes'][n], raw[n])
            self.state['calibration']['ranges'] = {n: [self.cal['mins'][n], self.cal['maxes'][n]] for n in self.cal['mins']}
        if following:
            dt = min(.1, max(.001, now - self.previous))
            self.previous = now
            leader = self.state['leader']['positions']
            for n in JOINTS:
                if self.state['control'] == 'full':
                    target = leader[n]
                else:
                    delta = leader[n] - self.lead_start[n]
                    if n != 'gripper':
                        delta = (delta + 180) % 360 - 180
                    travel = self.profile['starter_travel']
                    target = self.follow_start[n] + clamp(delta, -travel, travel)
                lo, hi = self.state['follower']['limits'][n]
                target = clamp(target, lo + (1 if n != 'gripper' else 0), hi - (1 if n != 'gripper' else 0))
                limit = self.profile['joint_speeds'][n]
                speed = min(10, limit) if now - self.started < 3 else limit
                if self.state['control'] == 'starter':
                    speed = min(speed, 10)
                self.goal[n] += clamp(target - self.goal[n], -speed * dt, speed * dt)
            self.arms['follower'].bus.sync_write('Goal_Position', self.goal, num_retry=2)
            self.rate_count += 1
            if now - self.rate_start >= 1:
                self.state['fps'] = round(self.rate_count / (now - self.rate_start), 1)
                self.rate_start, self.rate_count = now, 0
        else:
            self.state['fps'] = 0
            self.rate_start, self.rate_count = now, 0
        if now - self.sensor_at > 3 and not self.cal:
            bus = self.arms['follower'].bus
            temp = bus.read('Present_Temperature', 'shoulder_lift', normalize=False)
            volts = bus.read('Present_Voltage', 'shoulder_lift', normalize=False) / 10
            self.state['health'] = {'temperature': temp, 'voltage': volts}
            self.sensor_at = now
        if not self.history or now - self.history[-1]['t'] >= .5:
            self.history.append({'t': now, 'leader': self.state['leader']['positions'].copy(), 'follower': self.state['follower']['positions'].copy()})
        if self.record and not self.cal:
            r = self.record
            r['stream'].write(json.dumps({'time': datetime.now(timezone.utc).isoformat(), 'monotonic': now,
              'leader': self.state['leader']['positions'], 'follower': self.state['follower']['positions'],
              'command': self.goal, 'mode': self.state['mode']}) + '\n')
            r['frames'] += 1
            if r['frames'] % 40 == 0:
                r['stream'].flush()
            self.state['recording']['frames'] = r['frames']
            self.state['recording']['duration'] = round(now - r['started'], 1)
