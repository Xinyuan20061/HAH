"""Invariant tests for the accompanying algorithm reference."""

import math
import unittest
from dataclasses import replace

from personal_policy_learning_reference import (
    BetaBelief, Candidate, Episode, Ledger, Point, Protocol, Scope,
    adjudicate, execution_label, rank_candidates,
)


class PolicyLearningTests(unittest.TestCase):
    def setUp(self):
        self.scope = Scope(1, "short", "1", "metric-v1", "tight")
        self.protocol = Protocol(self.scope)
        self.baseline = tuple(Point(i, 2.0, f"b:{i}", 1, "metric-v1") for i in range(7))
        self.followup = tuple(Point(i, 4.0, f"f:{i}", 1, "metric-v1") for i in range(7))
        self.episode = Episode(
            "episode-1", 1, self.protocol, (True,) * 7, self.baseline, self.followup,
            baseline_context="tight", followup_context="tight",
        )

    def test_information_gain_has_known_uniform_value(self):
        self.assertAlmostEqual(BetaBelief().information_gain(), math.log(2) - 0.5, places=7)
        self.assertLess(BetaBelief(50, 50).information_gain(), BetaBelief().information_gain())
        self.assertGreater(BetaBelief(50, 50).information_gain(), 0)

    def test_missing_execution_is_not_zero(self):
        self.assertIsNone(execution_label((True, True, None), 0.8))
        self.assertEqual(execution_label((True, True, None), 0.6), 1)
        self.assertEqual(execution_label((False, False, None), 0.6), 0)

    def test_gate_changes_execution_but_not_support_for_missing_data(self):
        item = adjudicate(replace(self.episode, followup=self.followup[:2]))
        self.assertEqual(item.execution_label, 1)
        self.assertIsNone(item.support_label)
        self.assertEqual(item.availability_label, 0)
        ledger = Ledger()
        ledger.put(item)
        beliefs = ledger.beliefs(self.scope)
        self.assertEqual(beliefs.execution, BetaBelief(2, 1))
        self.assertEqual(beliefs.support, BetaBelief(1, 1))
        self.assertEqual(beliefs.availability, BetaBelief(1, 2))

    def test_target_not_met_does_not_credit_support(self):
        item = adjudicate(replace(self.episode, followup=tuple(
            replace(x, value=2.0) for x in self.followup
        )))
        self.assertEqual(item.execution_label, 1)
        self.assertEqual(item.support_label, 0)
        ledger = Ledger()
        ledger.put(item)
        self.assertEqual(ledger.beliefs(self.scope).support, BetaBelief(1, 2))

    def test_positive_episode_and_retry_are_idempotent(self):
        ledger = Ledger()
        item = adjudicate(self.episode)
        self.assertTrue(ledger.put(item))
        self.assertFalse(ledger.put(item))
        self.assertEqual(ledger.beliefs(self.scope).support, BetaBelief(2, 1))

    def test_correction_replaces_prior_contribution(self):
        ledger = Ledger()
        ledger.put(adjudicate(self.episode))
        corrected = replace(self.episode, revision=2, followup=tuple(
            replace(x, value=2.0, source_revision=2) for x in self.followup
        ))
        ledger.put(adjudicate(corrected))
        self.assertEqual(ledger.beliefs(self.scope).support, BetaBelief(1, 2))
        with self.assertRaisesRegex(ValueError, "STALE_REVISION"):
            ledger.put(adjudicate(self.episode))

    def test_same_revision_different_payload_is_rejected(self):
        ledger = Ledger()
        item = adjudicate(self.episode)
        ledger.put(item)
        with self.assertRaisesRegex(ValueError, "REVISION_PAYLOAD_CONFLICT"):
            ledger.put(replace(item, support_label=0))

    def test_removal_restores_prior(self):
        ledger = Ledger()
        ledger.put(adjudicate(self.episode))
        self.assertTrue(ledger.remove(self.scope, self.episode.episode_id))
        self.assertEqual(ledger.beliefs(self.scope).support, BetaBelief())

    def test_confounded_or_context_changed_episode_abstains(self):
        for patch in (
            {"confounders": ("travel",)},
            {"baseline_context": "open"},
            {"followup_context": "open"},
            {"changed_variables": ("session_minutes", "intensity")},
        ):
            with self.subTest(patch=patch):
                item = adjudicate(replace(self.episode, **patch))
                self.assertIsNone(item.support_label)
                self.assertEqual(item.conclusion, "incomparable")

    def test_model_inferred_or_wrong_version_points_cannot_qualify(self):
        for points in (
            tuple(replace(x, confirmed=False) for x in self.followup),
            tuple(replace(x, metric_version="metric-v2") for x in self.followup),
        ):
            self.assertIsNone(adjudicate(replace(self.episode, followup=points)).support_label)

    def test_pending_episode_updates_no_endpoint(self):
        item = adjudicate(replace(self.episode, window_closed=False))
        self.assertEqual((item.execution_label, item.support_label, item.availability_label),
                         (None, None, None))

    def test_ambiguity_is_not_positive_or_negative(self):
        item = adjudicate(replace(self.episode, followup=tuple(
            replace(x, value=3.0) for x in self.followup
        )))
        self.assertEqual(item.conclusion, "ambiguous")
        self.assertIsNone(item.support_label)
        self.assertEqual(item.availability_label, 1)

    def test_exposure_failure_is_not_effect_failure(self):
        item = adjudicate(replace(self.episode, execution=(False,) * 7))
        self.assertEqual(item.execution_label, 0)
        self.assertIsNone(item.support_label)
        self.assertIsNone(item.availability_label)

    def test_duplicate_days_and_shared_source_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "DUPLICATE_DAY"):
            adjudicate(replace(self.episode, followup=self.followup + (self.followup[0],)))
        item = adjudicate(replace(self.episode, followup=self.baseline))
        self.assertIn("baseline_followup_overlap", item.reasons)
        self.assertIsNone(item.support_label)
        with self.assertRaisesRegex(ValueError, "DUPLICATE_SOURCE"):
            adjudicate(replace(self.episode, followup=tuple(
                replace(x, source_ref="same-row") for x in self.followup
            )))

    def test_episode_cannot_move_to_different_strategy_or_context(self):
        ledger = Ledger()
        original = adjudicate(self.episode)
        ledger.put(original)
        with self.assertRaisesRegex(ValueError, "IMMUTABLE_EPISODE_SCOPE"):
            ledger.put(replace(original, scope=replace(self.scope, strategy_id="long")))
        item = adjudicate(replace(self.episode, followup_context="different"))
        self.assertEqual((item.execution_label, item.support_label, item.availability_label),
                         (None, None, None))

    def test_scope_isolates_users_contexts_and_versions(self):
        ledger = Ledger()
        ledger.put(adjudicate(self.episode))
        for patch in ({"user_id": 2}, {"context_key": "open"}, {"protocol_version": "2"},
                      {"metric_version": "metric-v2"}):
            self.assertEqual(ledger.beliefs(replace(self.scope, **patch)).support, BetaBelief())

    def test_hard_constraints_win_even_with_positive_history(self):
        ledger = Ledger()
        for i in range(12):
            ledger.put(adjudicate(replace(self.episode, episode_id=f"e-{i}")))
        result = rank_candidates(
            ledger, [Candidate("good-history-but-blocked", self.scope, 0, safe=False)],
            user_id=1, context_key="tight", exploration_consented=True,
        )
        self.assertIsNone(result["selected"])
        self.assertEqual(result["filtered"][0]["reasons"], ["hard_constraint"])

    def test_unknown_capability_and_foreign_context_are_filtered(self):
        result = rank_candidates(Ledger(), [
            Candidate("offline", self.scope, 0, capability_available=False),
            Candidate("foreign", replace(self.scope, user_id=2), 0),
        ], user_id=1, context_key="tight")
        self.assertFalse(result["ranked"])

    def test_no_exploration_without_explicit_consent(self):
        result = rank_candidates(Ledger(), [Candidate("short", self.scope, 0.1)],
                                 user_id=1, context_key="tight")
        self.assertEqual(result["ranked"][0]["breakdown"]["information_bonus"], 0)
        self.assertFalse(result["ranked"][0]["personalised"])

    def test_non_finite_observation_is_rejected(self):
        for value in (math.nan, math.inf, -math.inf, True):
            with self.assertRaises(ValueError):
                Point(0, value, "source:1", 1, "metric-v1")

    def test_shuffled_candidates_have_same_decision(self):
        a, b = Candidate("a", self.scope, 0.1), Candidate("b", self.scope, 0.1)
        first = rank_candidates(Ledger(), [a, b], user_id=1, context_key="tight")
        second = rank_candidates(Ledger(), [b, a], user_id=1, context_key="tight")
        self.assertEqual(first, second)
        self.assertEqual(first["selection_propensity"], 1.0)

    def test_adverse_event_stops_support_learning(self):
        item = adjudicate(replace(self.episode, adverse_event=True))
        self.assertEqual(item.conclusion, "stopped")
        self.assertIsNone(item.support_label)
        self.assertIsNone(item.availability_label)


if __name__ == "__main__":
    unittest.main()
