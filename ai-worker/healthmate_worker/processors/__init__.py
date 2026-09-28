"""Lazy processor exports keep optional vision stacks independent at import time."""

from importlib import import_module

__all__ = ["analyze_food", "analyze_motion", "analyze_kinetics400"]


def __getattr__(name: str):
    if name == "analyze_motion":
        return import_module(".motion", __name__).analyze_motion
    if name == "analyze_food":
        return import_module(".food", __name__).analyze_food
    if name == "analyze_kinetics400":
        return import_module(".kinetics", __name__).analyze_kinetics400
    raise AttributeError(name)
