"""Optional camera preview module; devices open only after an explicit UI action."""
import json
import threading
import time
from pathlib import Path


class Cameras:
    def __init__(self, data):
        self.path = Path(data) / 'cameras.json'
        self.profiles = {'front': {'label': 'Scene camera', 'index': 0, 'width': 640, 'height': 480, 'fps': 15},
                         'wrist': {'label': 'Wrist camera', 'index': 1, 'width': 640, 'height': 480, 'fps': 15}}
        if self.path.exists():
            self.profiles.update(json.loads(self.path.read_text()))
        self.running, self.frames, self.errors, self.stops = {}, {}, {}, {}
        self.lock = threading.RLock()

    def snapshot(self):
        with self.lock:
            return {role: {**p, 'running': self.running.get(role, False), 'error': self.errors.get(role),
                           'has_frame': role in self.frames} for role, p in self.profiles.items()}

    def save(self, p):
        role = p.get('role')
        if role not in self.profiles:
            raise ValueError('Choose front or wrist camera.')
        if self.running.get(role):
            raise ValueError('Stop preview before editing its profile.')
        result = {k: int(p[k]) for k in ('index', 'width', 'height', 'fps')}
        if not (0 <= result['index'] <= 9 and 160 <= result['width'] <= 1920 and 120 <= result['height'] <= 1080 and 1 <= result['fps'] <= 30):
            raise ValueError('Camera settings are outside supported bounds.')
        with self.lock:
            self.profiles[role].update(result)
            self.path.write_text(json.dumps(self.profiles, indent=2))

    def start(self, role):
        if role not in self.profiles:
            raise ValueError('Unknown camera role.')
        if self.running.get(role):
            return
        self.stops[role] = threading.Event()
        self.running[role] = True
        self.errors.pop(role, None)
        threading.Thread(target=self.capture, args=(role,), daemon=True).start()

    def capture(self, role):
        cap = None
        try:
            import cv2
            p = self.profiles[role].copy()
            cap = cv2.VideoCapture(p['index'])
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, p['width'])
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, p['height'])
            if not cap.isOpened():
                raise RuntimeError('Camera could not open. Check USB and macOS camera permission.')
            while not self.stops[role].is_set():
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError('No camera frame received.')
                ok, jpg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if ok:
                    with self.lock:
                        self.frames[role] = jpg.tobytes()
                self.stops[role].wait(1 / p['fps'])
        except Exception as exc:
            with self.lock:
                self.errors[role] = str(exc)
        finally:
            if cap is not None:
                cap.release()
            with self.lock:
                self.running[role] = False
                self.frames.pop(role, None)

    def stop(self, role):
        if role in self.stops:
            self.stops[role].set()
