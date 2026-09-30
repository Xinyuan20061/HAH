# -*- coding: utf-8 -*-
"""Motion catalog regression tests (contract §8 / spec §6.1).

Guards:
  1. ids unique, name_zh non-empty, aliases non-empty.
  2. every kinetics_labels entry exists in kinetics400_labels.txt and its
     class_index matches the real line order (line_no - 1).
  3. catalog_version is readable from the accessor.
  4. the three generated artifacts (backend py / worker py / miniprogram js)
     carry identical content for the shared projection.
  5. the six legacy actions have repetition_counter / quality_scorer either
     null (pending G-package validation) or already registered (a version str).

Run: backend/.venv/Scripts/python.exe -m pytest tests/test_motion_catalog.py -q
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from app.services.motion import catalog as catalog_api
from app.services.motion import catalog_data as backend_data

REPO_ROOT = Path(__file__).resolve().parents[2]
LABELS_FILE = REPO_ROOT / "ai-worker" / "healthmate_worker" / "models" / "kinetics400_labels.txt"
WORKER_DATA_FILE = REPO_ROOT / "ai-worker" / "healthmate_worker" / "catalog_data.py"
MINI_DATA_FILE = REPO_ROOT / "miniprogram" / "utils" / "motionCatalogData.js"

SIX_LEGACY_IDS = {
    "squat", "pushup", "lunge", "leg_abduction", "arm_abduction", "arm_vw",
}


def _load_labels() -> dict[str, int]:
    """Map exact Kinetics-400 label -> class_index (line number - 1)."""
    labels: dict[str, int] = {}
    for idx, line in enumerate(LABELS_FILE.read_text(encoding="utf-8").splitlines()):
        label = line.strip()
        if label:
            labels[label] = idx
    return labels


def _load_worker_data():
    spec = importlib.util.spec_from_file_location("hm_worker_catalog_data", WORKER_DATA_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_js_actions() -> list[dict]:
    text = MINI_DATA_FILE.read_text(encoding="utf-8")
    start = text.index("const ACTIONS = ") + len("const ACTIONS = ")
    end = text.index(";\nconst KINETICS_TO_ID")
    return json.loads(text[start:end])


def test_ids_unique_and_required_fields_nonempty():
    ids = [a["id"] for a in backend_data.ACTIONS]
    assert len(ids) == len(set(ids)), "duplicate action ids"
    for a in backend_data.ACTIONS:
        assert a["name_zh"], f"{a['id']}: name_zh empty"
        assert a["aliases"], f"{a['id']}: aliases empty"


def test_kinetics_labels_exist_and_index_correct():
    labels = _load_labels()
    for a in backend_data.ACTIONS:
        for k in a.get("kinetics_labels") or []:
            label, idx = k["label"], k["class_index"]
            assert label in labels, f"{a['id']}: kinetics label {label!r} not in 400-class table"
            assert labels[label] == idx, (
                f"{a['id']}: label {label!r} declared class_index={idx} "
                f"but real index={labels[label]}"
            )


def test_catalog_version_readable():
    version = catalog_api.catalog_version()
    assert isinstance(version, str) and version, "catalog_version empty"
    assert version == backend_data.CATALOG_VERSION


def test_three_artifacts_consistent():
    worker = _load_worker_data()
    js_actions = _load_js_actions()

    assert backend_data.CATALOG_VERSION == worker.CATALOG_VERSION
    assert backend_data.CATALOG_VERSION == json.loads(
        MINI_DATA_FILE.read_text(encoding="utf-8").split("const CATALOG_VERSION = ", 1)[1].split(";", 1)[0]
    )
    # backend and worker python modules must be byte-identical data
    assert backend_data.ACTIONS == worker.ACTIONS
    assert backend_data.KINETICS_TO_ID == worker.KINETICS_TO_ID
    # miniprogram JS must carry the same action list
    assert js_actions == backend_data.ACTIONS


def test_six_legacy_capabilities_pending_or_registered():
    for legacy_id in SIX_LEGACY_IDS:
        a = catalog_api.get_action(legacy_id)
        assert a is not None, f"legacy action {legacy_id} missing from catalog"
        caps = a["capabilities"]
        for key in ("repetition_counter", "quality_scorer"):
            value = caps.get(key, "MISSING")
            assert value is None or isinstance(value, str), (
                f"{legacy_id}.{key} must be null (pending G) or a registered version string, got {value!r}"
            )
        # recognition / timeline / coaching always available
        assert caps["visual_recognition"] is True
        assert caps["timeline"] is True
        assert caps["technique_explanation"] is True


def test_accessor_helpers():
    assert catalog_api.map_kinetics_label("squat") == "squat"
    assert catalog_api.map_kinetics_label("front raises") == "front_raise"
    assert catalog_api.map_kinetics_label("deadlifting") == "deadlift"
    assert catalog_api.map_kinetics_label("bench pressing") == "bench_press"
    assert catalog_api.map_kinetics_label("not a real label") is None
    assert catalog_api.map_kinetics_label("") is None
    assert catalog_api.get_action("bicep_curl")["name_zh"] == "哑铃弯举"
    assert catalog_api.get_action("does_not_exist") is None
    assert catalog_api.knowledge("squat") == ["squat_setup", "squat_depth", "squat_balance"]
    assert catalog_api.knowledge("nope") == []
    caps = catalog_api.list_capabilities()
    assert {c["id"] for c in caps} == {a["id"] for a in backend_data.ACTIONS}
