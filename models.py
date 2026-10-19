"""Dependency-free calibration data for the simulator."""
from dataclasses import dataclass


@dataclass
class Calibration:
    id: int
    drive_mode: int
    homing_offset: int
    range_min: int
    range_max: int
