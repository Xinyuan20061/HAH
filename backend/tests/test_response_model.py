from app.services.policy_learning.acquisition.response_model import (
    DEFAULT_RESPONSE_MODEL,
    ResponseModelSnapshot,
)

import pytest


def test_default_response_model_is_prior_only_with_zero_samples():
    model = DEFAULT_RESPONSE_MODEL
    assert model.sample_count == 0
    assert model.calibration_state == "prior_only"
    assert model.kind == "declared_prior_or_consented_calibration"
    assert set(model.probabilities) == {"answered", "unknown", "declined", "no_response"}
    assert abs(sum(model.probabilities.values()) - 1.0) < 1e-9


def test_snapshot_is_immutable():
    model = DEFAULT_RESPONSE_MODEL
    with pytest.raises(Exception):
        model.sample_count = 1  # frozen dataclass


def test_snapshot_validates_sample_count_and_calibration_state():
    with pytest.raises(ValueError):
        ResponseModelSnapshot(sample_count=-1)
    with pytest.raises(ValueError):
        ResponseModelSnapshot(calibration_state="made_up")
    with pytest.raises(ValueError):
        ResponseModelSnapshot(probabilities={"answered": 0.5})


def test_to_dict_round_trip_and_version_fields():
    payload = DEFAULT_RESPONSE_MODEL.to_dict()
    assert payload["schema_version"] == "response-model-v2"
    assert payload["version"] == "declared-common-response-v2"
    assert payload["created_at"].endswith("Z")
