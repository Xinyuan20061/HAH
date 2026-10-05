"""Request contract checks against the workspace's installed Pydantic v2."""

import unittest
from pydantic import ValidationError

from low_burden_evidence_contracts import (
    AnswerRequest, ObservationRepairRequest, StartAcquisitionRequest,
)


class ContractTests(unittest.TestCase):
    def base(self):
        return {"expected_session_version": 2, "expected_episode_version": 6}

    def test_confirmed_execution_answer(self):
        result = AnswerRequest(**self.base(), response="answered", confirmation=True,
                               execution_value="completed")
        self.assertEqual(result.execution_value, "completed")

    def test_zero_burden_is_valid_and_not_missing(self):
        result = AnswerRequest(**self.base(), response="answered", confirmation=True,
                               burden_value=0.0, observed_at="2026-10-05T01:00:00Z")
        self.assertEqual(result.burden_value, 0.0)

    def test_declined_cannot_carry_evidence(self):
        with self.assertRaises(ValidationError):
            AnswerRequest(**self.base(), response="declined", burden_value=1.0,
                          observed_at="2026-10-05T01:00:00Z")

    def test_answer_requires_confirmation_and_one_value(self):
        for values in (
            {"execution_value": "completed"},
            {"confirmation": True},
            {"confirmation": True, "execution_value": "completed", "burden_value": 1.0},
        ):
            with self.assertRaises(ValidationError):
                AnswerRequest(**self.base(), response="answered", **values)

    def test_naive_datetime_nan_and_bool_number_rejected(self):
        for values in (
            {"burden_value": 1.0, "observed_at": "2026-10-05T01:00:00"},
            {"burden_value": float("nan"), "observed_at": "2026-10-05T01:00:00Z"},
            {"burden_value": True, "observed_at": "2026-10-05T01:00:00Z"},
        ):
            with self.assertRaises(ValidationError):
                AnswerRequest(**self.base(), response="answered", confirmation=True, **values)

    def test_client_cannot_supply_slot_or_timeout_outcome(self):
        with self.assertRaises(ValidationError):
            AnswerRequest(**self.base(), response="unknown", slot=1)
        with self.assertRaises(ValidationError):
            AnswerRequest(**self.base(), response="no_response")

    def test_consent_and_strict_versions_required(self):
        with self.assertRaises(ValidationError):
            StartAcquisitionRequest(expected_episode_version=1, consent_to_questions=False)
        with self.assertRaises(ValidationError):
            StartAcquisitionRequest(expected_episode_version=True, consent_to_questions=True)

    def test_self_report_repair_has_distinct_observation_and_source_versions(self):
        result = ObservationRepairRequest(
            expected_episode_version=9, observation_ref_id=1,
            expected_observation_revision=2, expected_source_revision=1,
            expected_observation_hash="a" * 64, confirmation=True,
            burden_value=2.0, observed_at="2026-10-05T01:00:00Z",
        )
        self.assertEqual(result.expected_observation_revision, 2)
        self.assertEqual(result.expected_source_revision, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
