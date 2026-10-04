import itertools
import random
import unittest
from dataclasses import replace
from fractions import Fraction as F
from pathlib import Path

from enhancer.model import Artifact, Bounds, InventoryEvaluation, MEANS, Profile, ROLLS, SLOTS, Stat, best_build, evaluate, gain_distribution
from enhancer.execution import GuardError, Material, Progress, SerialBatch, compare_progress, validate_material_batch
from enhancer.ledger import Balances, resource_delta

ROOT = Path(__file__).resolve().parents[1]


def piece(id, slot="flower", level=20, sets="DisenchantmentInDeepShadow", hits=1, pending=False):
    main = {"flower": "hp", "plume": "atk", "sands": "atk_", "goblet": "atk_", "circlet": "critDMG_"}[slot]
    # Fixtures use exact internal values to isolate probability/build mathematics.
    keys = ["critRate_", "enerRech_", "eleMas", "def"]
    stats = tuple(Stat(k, ROLLS[k][1] * (hits if i == 0 else 1), pending and i == 3, True)
                  for i, k in enumerate(keys))
    return Artifact(id, sets, slot, main, level, stats)


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.p = Profile.load(ROOT / "profiles/木偶.json")
        self.p.data["artifact_crit_rate_cap"] = None  # Separate uncapped reference-model tests.

    def test_mean_roll_weights_and_tiers(self):
        self.assertEqual(self.p.weight("atk"), F(1, 4))
        self.assertEqual(MEANS["critRate_"], F("3.305"))
        for key in ROLLS:
            self.assertEqual(sum(self.p.weight(key) * v / MEANS[key] for v in ROLLS[key]) / 4,
                             self.p.weight(key))
        self.assertLess(ROLLS["critRate_"][0] / MEANS["critRate_"], 1)
        self.assertGreater(ROLLS["critRate_"][-1] / MEANS["critRate_"], 1)

    def test_pending_real_level_and_rolls(self):
        a = piece("p", level=0, pending=True)
        self.assertEqual(a.level, 0)
        self.assertEqual(a.random_rolls_left, 4)
        self.assertEqual(replace(a, level=3).random_rolls_left, 4)
        self.assertEqual(piece("four", level=0).random_rolls_left, 5)
        self.assertEqual(piece("four", level=9).random_rolls_left, 3)

    def test_exact_distribution_against_enumeration(self):
        keys = ("critRate_", "critDMG_", "atk", "def")
        weights = tuple(self.p.weight(k) for k in keys)
        dist, denominator = gain_distribution(keys, weights, 2)
        rolls = [w * v / MEANS[k] for k, w in zip(keys, weights) for v in ROLLS[k]]
        threshold = F("1.1")
        brute = sum(a + b > threshold for a in rolls for b in rolls)
        self.assertEqual(sum(n for score, n in dist if score > threshold), brute)
        self.assertEqual(sum(n for _, n in dist), denominator)
        self.assertEqual(denominator, 256)

    def test_four_plus_one_matches_full_cartesian_search(self):
        rng = random.Random(614245)
        for trial in range(40):
            pool = [piece(f"{trial}:{slot}:{j}", slot, sets=self.p.data["set_key"] if j == 0 or rng.random() < .6 else "other",
                          hits=rng.randint(1, 5)) for slot in SLOTS for j in range(3)]
            combinations = itertools.product(*[[a for a in pool if a.slot == s] for s in SLOTS])
            legal = [sum(self.p.score(a).lo for a in c) for c in combinations
                     if sum(a.set_key == self.p.data["set_key"] for a in c) >= 4]
            self.assertEqual(best_build(pool, self.p).lo, max(legal))
            prepared = InventoryEvaluation(pool, self.p)
            self.assertEqual(prepared.baseline, best_build(pool, self.p))
            for forced in (pool[0], pool[4], pool[9]):
                self.assertEqual(prepared.complement(forced), best_build(pool, self.p, forced))

    def test_candidate_offpiece_can_move_offslot(self):
        pool = [piece(s, s, hits=2) for s in SLOTS]
        pool.append(piece("offflower", "flower", sets="other", hits=4))
        a = piece("candidate", "goblet", level=16, sets="other", hits=3)
        complement = best_build(pool, self.p, a)
        expected = sum(self.p.score(x).lo for x in pool[:5] if x.slot != "goblet")
        self.assertEqual(complement.lo, expected)  # Cannot keep the off-set flower.

    def test_strict_improvement_and_reservations(self):
        pool = [piece(s, s, hits=6) for s in SLOTS]
        a = piece("weak", level=16, hits=1)
        self.assertEqual(evaluate(a, pool, self.p)["action"], "retain")
        self.assertNotEqual(evaluate(replace(a, equipped="other character"), pool, self.p)["action"], "skip")
        self.p.data["allowed_equipped_characters"] = ["木偶"]
        self.assertEqual(evaluate(replace(a, equipped="other character"), pool, self.p)["action"], "skip")
        self.assertEqual(evaluate(replace(a, special="defined"), pool, self.p)["action"], "reread")


class ExecutionTests(unittest.TestCase):
    def test_resource_accounting_uses_observed_balances(self):
        before = Balances(100000, {"祝圣精华": 100}, {3: 20, 4: 10, 5: 2683})
        after = Balances(95000, {"祝圣精华": 95}, {3: 20, 4: 10, 5: 2683})
        delta = resource_delta(before, after)
        self.assertEqual(delta["mora"], 5000)
        self.assertEqual(delta["exp_items"]["祝圣精华"], 5)
        self.assertEqual(delta["artifact_materials"][5], 0)
        with self.assertRaises(GuardError):
            resource_delta(before, replace(after, artifact_counts={3: 20, 4: 10, 5: 2682}))

    def test_partial_batches_and_overshoot(self):
        for a, b, expected in [(Progress("a", 0, 0), Progress("a", 3, 3970), "add_materials"),
                               (Progress("a", 3, 3970), Progress("a", 4, 80), "reevaluate"),
                               (Progress("a", 7, 0), Progress("a", 9, 0), "reevaluate"),
                               (Progress("a", 2, 50), Progress("a", 2, 60), "add_materials"),
                               (Progress("a", 2, 50), Progress("a", 2, None), "reread")]:
            self.assertEqual(compare_progress(a, b), expected)

    def test_reject_five_star_and_unknown_in_any_position(self):
        valid = Material("artifact", 3, "fodder", 1, True)
        for bad in [replace(valid, rarity=5), replace(valid, rarity=None), replace(valid, kind="unknown"),
                    replace(valid, observed=False), Material("exp_item", None, "启圣之尘", 1, True)]:
            for index in range(3):
                batch = [valid] * 3
                batch[index] = bad
                with self.assertRaises(GuardError):
                    validate_material_batch(batch, expected_slots=3, complete=True, fresh=True, target_verified=True)

    def test_timeout_never_repeats_consumption(self):
        runner = SerialBatch()
        mat = [Material("artifact", 3, "fodder", 1, True)]
        evidence = dict(expected_slots=1, complete=True, fresh=True, target_verified=True)
        runner.submit("one", mat, **evidence)
        before = Progress("a", 3, 3970)
        self.assertEqual(runner.observe("one", before, Progress("a", 3, None)), "reread")
        with self.assertRaises(GuardError):
            runner.submit("two", mat, **evidence)
        self.assertEqual(runner.observe("one", before, Progress("a", 4, 80)), "reevaluate")
        with self.assertRaises(GuardError):
            runner.submit("one", mat, **evidence)


if __name__ == "__main__":
    unittest.main()
