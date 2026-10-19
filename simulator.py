"""Simulated SO-101 arms for demo mode and tests. Never touches serial ports or saved calibration files."""
import math
import time

from models import Calibration as MotorCalibration

JOINTS = ['shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex', 'wrist_roll', 'gripper']
SERIALS = {'leader': 'DEMO_LEADER', 'follower': 'DEMO_FOLLOWER'}


class Motor:
    def __init__(self, ident):
        self.id = ident


class FakeBus:
    def __init__(self, role, clock=time.monotonic):
        self.role, self.clock = role, clock
        self.motors = {n: Motor(i + 1) for i, n in enumerate(JOINTS)}
        self.is_connected = False
        self.is_calibrated = True
        self.torque = {n: 1 if role == 'follower' else 0 for n in JOINTS}
        self.position = {n: 30.0 if n == 'gripper' else 0.0 for n in JOINTS}
        self.goal = dict(self.position)
        self.leader_override = None  # tests can pin the leader pose
        self.writes = []

    def connect(self):
        self.is_connected = True

    def disconnect(self, disable_torque=True):
        if disable_torque:
            self.disable_torque()
        self.is_connected = False

    @staticmethod
    def to_raw(n, v):
        return round(v * 40.95) if n == 'gripper' else round(v * 4095 / 360 + 2048)

    @staticmethod
    def from_raw(n, v):
        return v / 40.95 if n == 'gripper' else (v - 2048) * 360 / 4095

    def _update(self):
        if self.role == 'leader':
            if self.leader_override is not None:
                self.position = dict(self.leader_override)
            else:
                t = self.clock()
                self.position = {'shoulder_pan': 25 * math.sin(t * .35), 'shoulder_lift': 18 * math.sin(t * .5),
                                 'elbow_flex': 22 * math.sin(t * .42 + 1), 'wrist_flex': 15 * math.sin(t * .6 + 2),
                                 'wrist_roll': 30 * math.sin(t * .3), 'gripper': 35 + 30 * math.sin(t * .8)}
        else:
            for n in JOINTS:
                if self.torque[n]:
                    self.position[n] += (self.goal[n] - self.position[n]) * .5

    def sync_read(self, name, motors=None, *, normalize=True, num_retry=0):
        if not self.is_connected:
            raise ConnectionError('Simulated bus not connected.')
        self._update()
        if name != 'Present_Position':
            raise KeyError(name)
        return {n: (v if normalize else self.to_raw(n, v)) for n, v in self.position.items()}

    def read(self, name, motor, *, normalize=True, num_retry=0):
        if name == 'Torque_Enable':
            return self.torque[motor]
        if name == 'Present_Temperature':
            return 37
        if name == 'Present_Voltage':
            return 121
        raise KeyError(name)

    def sync_write(self, name, values, *, normalize=True, num_retry=0):
        if name == 'Goal_Position':
            for n, v in values.items():
                self.goal[n] = v if normalize else self.from_raw(n, v)
            self.writes.append(dict(self.goal))

    def write(self, name, motor, value, **kw):
        pass

    def disable_torque(self, motors=None, num_retry=0):
        for n in JOINTS:
            self.torque[n] = 0

    def set_half_turn_homings(self):
        return {n: 0 for n in JOINTS}

    def write_calibration(self, calibration):
        self.is_calibrated = True


class FakeArm:
    def __init__(self, role, clock=time.monotonic):
        self.role = role
        self.bus = FakeBus(role, clock)
        self.calibration = {n: MotorCalibration(id=i + 1, drive_mode=0, homing_offset=0,
                                                range_min=0 if n in ('wrist_roll', 'gripper') else 900,
                                                range_max=4095 if n in ('wrist_roll', 'gripper') else 3200)
                            for i, n in enumerate(JOINTS)}

    def configure(self):
        for n in JOINTS:
            self.bus.torque[n] = 1 if self.role == 'follower' else 0

    def _save_calibration(self):
        pass


def fake_factory(role, port, ident):
    return FakeArm(role)


def fake_ports():
    return [{'device': f'/dev/cu.sim-{role}', 'serial': serial, 'description': f'Simulated {role}'}
            for role, serial in SERIALS.items()]
