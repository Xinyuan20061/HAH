# -*- coding: utf-8 -*-
"""Regression: the strict doctor gate must not reject a working Worker.

Confirmed defect (found in review): ``strict_doctor`` failed the release gate when
the *source checkout* path contained non-ASCII characters, even though MediaPipe
loaded and completed a real inference through ``start_worker.ps1``'s ``venvlink``
junction.

The root cause is that ``Path(__file__).resolve()`` follows junctions / reparse
points / ``subst``, so a joined path cannot be laundered into ASCII — the check
was gating on a fact the process cannot verify. What actually breaks MediaPipe is
a non-ASCII path to the **interpreter's** native resources
(``site-packages/mediapipe/modules/.../*.binarypb``), which is what the hard
``mediapipe_pose_inference`` check already observes directly.

These tests pin the distinction so the false negative cannot return.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

WORKER_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = WORKER_ROOT / "scripts"


def _load_strict_doctor():
    """Import the script by path (scripts/ is not a package)."""
    path = SCRIPTS_DIR / "strict_doctor.py"
    spec = importlib.util.spec_from_file_location("strict_doctor_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def strict_doctor():
    return _load_strict_doctor()


def test_path_check_does_not_gate_on_the_source_tree(strict_doctor):
    """A non-ASCII *source* path must not, by itself, fail the gate.

    The interpreter in this environment lives under the same non-ASCII checkout,
    so the check may legitimately report FAIL — but the detail must make clear
    *which* path is the problem, and the source path must be reported as an
    advisory fact rather than being conflated with the interpreter.
    """
    ok, detail = strict_doctor._path_facts()
    assert "interpreter=" in detail
    assert "source=" in detail
    # The verdict must track the interpreter, never the source tree alone.
    interpreter_ascii = strict_doctor._is_ascii(
        str(Path(sys.executable).resolve())
    )
    assert ok is interpreter_ascii


def test_source_path_is_reported_but_not_fatal(strict_doctor, capsys):
    """A non-ASCII source path yields an advisory, not an extra failure."""
    source_resolved = str(Path(strict_doctor.WORKER_ROOT).resolve())
    if strict_doctor._is_ascii(source_resolved):
        pytest.skip("this checkout is already under a pure-ASCII path")

    # The advisory branch is what main() prints; assert the classification rule.
    assert not strict_doctor._is_ascii(source_resolved)
    # And the path check itself must not fail merely because of that.
    ok, detail = strict_doctor._path_facts()
    assert ok is strict_doctor._is_ascii(str(Path(sys.executable).resolve())), detail


def test_ascii_helper_is_exact(strict_doctor):
    assert strict_doctor._is_ascii("C:\\HealthMate\\venv") is True
    assert strict_doctor._is_ascii("/opt/healthmate") is True
    assert strict_doctor._is_ascii("D:\\学习资料\\health-assistant") is False


def test_pose_inference_check_is_the_decisive_evidence(strict_doctor):
    """The engine check must be the gate that decides capability availability."""
    ok, detail = strict_doctor._pose_inference_ok()
    if ok:
        assert "MediaPipe" in detail
    else:
        # A failing engine reports a reason, never an opaque boolean.
        assert detail
        assert "MediaPipe" in detail or "推理" in detail


def test_receipt_contract_check_passes_offline(strict_doctor):
    """The V2 receipt contract must validate without any model or network."""
    ok, detail = strict_doctor._receipt_shape_ok()
    assert ok, detail


def test_check_registry_uses_the_path_check_not_a_source_gate(strict_doctor):
    """The published check name must describe what it really verifies."""
    import inspect

    source = inspect.getsource(strict_doctor.main)
    assert '"interpreter_path_ascii"' in source
    assert '"install_path_ascii"' not in source
    assert '"mediapipe_pose_inference"' in source
