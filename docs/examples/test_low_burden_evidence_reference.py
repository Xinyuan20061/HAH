"""Independent reference checks. Does not exercise production APIs or DBs."""

from dataclasses import replace
from itertools import product
import unittest

from low_burden_evidence_reference import (
    Branch, CertificateBinding, ExecutionDomain, Query, ResponsePrior,
    choose_next, execution_proof, issue_execution_certificate,
    verify_execution_certificate,
)


class PairAvailabilityDomain:
    """Controlled complementarity case, not a health outcome classifier."""

    def resolved(self, state):
        return all(state)

    def terminal_loss(self, state):
        return 0.0 if self.resolved(state) else 20.0

    def candidates(self, state, asked):
        return tuple(Query(str(i), 1000) for i, known in enumerate(state)
                     if not known and str(i) not in asked)

    def branches(self, state, query):
        rows = list(state)
        rows[int(query.key)] = True
        return (Branch("answered", 0.8, tuple(rows)), Branch("no_response", 0.2, state))


class ReferenceTests(unittest.TestCase):
    def test_all_2187_partial_states_against_completion_oracle(self):
        for partial in product((True, False, None), repeat=7):
            holes = [i for i, value in enumerate(partial) if value is None]
            labels = set()
            for fill in product((True, False), repeat=len(holes)):
                full = list(partial)
                for slot, answer in zip(holes, fill):
                    full[slot] = answer
                labels.add(int(sum(full) / 7 >= 0.7))
            expected = next(iter(labels)) if len(labels) == 1 else None
            proof = execution_proof(partial, 0.7)
            self.assertEqual(proof.label, expected, partial)

    def test_witness_suffices_without_other_known_values(self):
        for values in product((True, False, None), repeat=7):
            proof = execution_proof(values, 0.7)
            if proof.label is None:
                continue
            retained = tuple(value if i in proof.witness_slots else None
                             for i, value in enumerate(values))
            self.assertEqual(execution_proof(retained, 0.7).label, proof.label)

    def test_invalid_bool_like_and_nonfinite_values_rejected(self):
        for bad in ((1, None), (0, None), ("true", None)):
            with self.assertRaises(ValueError):
                execution_proof(bad, 0.7)
        for target in (float("nan"), float("inf"), 0, True):
            with self.assertRaises(ValueError):
                execution_proof((True, None), target)

    def test_stop_when_already_sufficient(self):
        domain = ExecutionDomain(target=0.7, eligible_slots=(5, 6))
        result = choose_next(domain, (True,) * 5 + (None, None),
                             remaining_questions=2, remaining_time_ms=10_000)
        self.assertEqual(result.action, "stop_sufficient")

    def test_only_eligible_due_slots_can_be_selected(self):
        domain = ExecutionDomain(target=0.7, eligible_slots=(6,))
        result = choose_next(domain, (True,) * 4 + (False, None, None),
                             remaining_questions=2, remaining_time_ms=10_000)
        self.assertEqual(result.query_key, "execution:6")

    def test_refusal_never_becomes_negative_evidence(self):
        state = (True,) * 4 + (False, None, None)
        domain = ExecutionDomain(target=0.7, eligible_slots=(5, 6))
        branches = domain.branches(state, Query("execution:5", 3000))
        for branch in branches:
            if branch.outcome in {"unknown", "declined", "no_response"}:
                self.assertEqual(branch.state, state)

    def test_no_response_only_model_defers(self):
        prior = ResponsePrior(answered=0, unknown=0, declined=0, no_response=1)
        domain = ExecutionDomain(target=0.7, eligible_slots=(5, 6), prior=prior)
        result = choose_next(domain, (True,) * 4 + (False, None, None),
                             remaining_questions=2, remaining_time_ms=10_000)
        self.assertEqual(result.action, "defer")

    def test_two_step_detects_complementary_acquisitions(self):
        domain = PairAvailabilityDomain()
        one = choose_next(domain, (False, False), remaining_questions=2,
                          remaining_time_ms=2000, depth=1)
        two = choose_next(domain, (False, False), remaining_questions=2,
                          remaining_time_ms=2000, depth=2)
        self.assertEqual(one.action, "defer")
        self.assertEqual(two.action, "ask")
        self.assertAlmostEqual(two.expected_loss, 9.0)

    def test_budget_exhaustion_defers(self):
        domain = ExecutionDomain(target=0.7, eligible_slots=(5, 6))
        result = choose_next(domain, (True,) * 4 + (False, None, None),
                             remaining_questions=0, remaining_time_ms=10_000)
        self.assertEqual(result.reason, "budget_exhausted")

    def binding(self):
        return CertificateBinding(1, "episode", 6, "protocol", "burden-v1",
                                  "context", "epoch", "rule-v1", "grant",
                                  "evidence", "generation", "knowledge", False)

    def test_certificate_checks_version_expiry_authorization_and_body(self):
        binding = self.binding()
        proof = execution_proof((True,) * 5 + (None, None), 0.7)
        cert = issue_execution_certificate(binding, proof, expires_at_epoch_seconds=1000)
        self.assertEqual(cert.purpose, "execution_progress")
        self.assertTrue(verify_execution_certificate(cert, binding, now_epoch_seconds=999)[0])
        self.assertFalse(verify_execution_certificate(cert, binding, now_epoch_seconds=1000)[0])
        self.assertFalse(verify_execution_certificate(
            cert, replace(binding, episode_version=7), now_epoch_seconds=999)[0])
        self.assertFalse(verify_execution_certificate(
            cert, binding, now_epoch_seconds=999, authorization_valid=False)[0])
        self.assertFalse(verify_execution_certificate(
            cert, binding, now_epoch_seconds=999, pending_source_rebuild=True)[0])
        self.assertFalse(verify_execution_certificate(
            replace(cert, proof=replace(proof, label=0)), binding,
            now_epoch_seconds=999)[0])

    def test_ambiguous_state_cannot_issue_certificate(self):
        proof = execution_proof((True,) * 4 + (False, None, None), 0.7)
        with self.assertRaises(ValueError):
            issue_execution_certificate(self.binding(), proof, expires_at_epoch_seconds=1000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
