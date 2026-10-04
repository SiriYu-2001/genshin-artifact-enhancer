from dataclasses import replace
from fractions import Fraction as F
import itertools
import random
import unittest
from unittest.mock import patch

from enhancer.capped import CappedInventory, Envelope, Point, build_score, evaluate_capped, main_crit
from enhancer.model import Profile, ROLLS, SLOTS, Stat
from test_planner import ROOT, piece


class CapTests(unittest.TestCase):
    def setUp(self):
        self.p = Profile.load(ROOT / "profiles/木偶.json")

    def test_main_crit_uses_cap_without_scoring_main_stat(self):
        p = self.p
        p.data["main_stats"]["circlet"] = ["critDMG_", "critRate_"]
        pieces = [piece(s, s, hits=3) for s in SLOTS]
        head = pieces[-1]
        head = replace(head, main="critRate_", stats=tuple(
            Stat("hp", ROLLS["hp"][1], exact=True) if s.key == "critRate_" else s for s in head.stats))
        pieces[-1] = head
        self.assertEqual(main_crit(head), F("31.1"))
        actual = build_score(pieces, p).lo
        cr = sum(s.value for a in pieces for s in a.stats if s.key == "critRate_")
        uncapped = sum(p.score(a).lo for a in pieces)
        from enhancer.model import MEANS
        self.assertEqual(actual, uncapped - max(F(0), cr - F("13.7")) / MEANS["critRate_"])
        self.assertLess(actual, uncapped)

    def test_frontier_matches_exhaustive_builds_with_main_crit(self):
        rng = random.Random(448)
        self.p.data["main_stats"]["circlet"] = ["critDMG_", "critRate_"]
        self.p.data["artifact_crit_rate_cap"] = 20
        for trial in range(12):
            pool = [piece(f"{trial}:{s}:{i}", s, hits=rng.randint(1, 6),
                          sets=self.p.data["set_key"] if i < 2 else "other") for s in SLOTS for i in range(3)]
            a = pool[-1]
            pool[-1] = replace(a, main="critRate_", stats=tuple(
                Stat("hp", ROLLS["hp"][1], exact=True) if x.key == "critRate_" else x for x in a.stats))
            scores = [build_score(c, self.p).lo for c in itertools.product(*[[a for a in pool if a.slot == s] for s in SLOTS])
                      if sum(a.set_key == self.p.data["set_key"] for a in c) >= 4]
            self.assertEqual(CappedInventory(pool, self.p).baseline.lo, max(scores))

    def test_envelope_matches_direct_evaluation(self):
        points = (Point(F(2), F(6)), Point(F(10), F(4)), Point(F(0), F(8), (), F("31.1")))
        env = Envelope(points, F("44.8"), F(1))
        for candidate_cr in (F(0), F(10), F(30), F(50)):
            for candidate_main in (F(0), F("31.1")):
                expected = max(p.other + min(p.crit + candidate_cr, max(F(0), F("44.8") - p.main_crit - candidate_main)) for p in points)
                self.assertEqual(env.query(candidate_cr, candidate_main), expected)

    def test_capped_probability_matches_all_sixteen_last_rolls(self):
        self.p.data["artifact_crit_rate_cap"] = 20
        pool = [piece(s, s, hits=2) for s in SLOTS]
        candidate = piece("candidate", level=16, hits=2)
        prepared = CappedInventory(pool, self.p)
        success = 0
        for index, stat in enumerate(candidate.stats):
            for roll in ROLLS[stat.key]:
                stats = list(candidate.stats)
                stats[index] = replace(stat, value=stat.value + roll)
                final = replace(candidate, level=20, stats=tuple(stats))
                improved = CappedInventory(pool + [final], self.p).baseline.lo > prepared.baseline.lo
                success += improved
        result = evaluate_capped(candidate, pool, self.p, prepared)
        self.assertEqual(result["probability_lower"], success / 16)
        self.assertEqual(result["probability_upper"], success / 16)

    def test_impossible_completion_is_pruned_without_distribution_but_zero_threshold_preserved(self):
        self.p.data['artifact_crit_rate_cap']=200
        pool=[piece(s,s,hits=6) for s in SLOTS]
        candidate=piece('weak',level=16,hits=1)
        with patch('enhancer.capped.joint_distribution',side_effect=AssertionError('No enumeration needed')):
            result=evaluate_capped(candidate,pool,self.p)
            self.assertTrue(result['pruned_by_upper_bound'])
            self.assertEqual(result['probability_upper'],0)
            self.assertEqual(result['action'],'retain')
            self.p.data['threshold']=0
            self.assertEqual(evaluate_capped(candidate,pool,self.p)['action'],'enhance')

    def test_probability_bound_matches_bruteforce_across_caps_and_two_remaining_rolls(self):
        for cap in (5,20,47):
            self.p.data['artifact_crit_rate_cap']=cap
            pool=[piece(s,s,hits=3) for s in SLOTS]
            candidate=piece('candidate',level=12,hits=2)
            prepared=CappedInventory(pool,self.p)
            complement=prepared.complement(candidate,'lo')
            from enhancer.capped import components
            cr,other=components(candidate,self.p)
            from enhancer.model import MEANS
            steps=[(v,F(0)) if s.key=='critRate_' else (F(0),self.p.weight(s.key)*v/MEANS[s.key])
                   for s in candidate.stats for v in ROLLS[s.key]]
            wins=sum(other.lo+a[1]+b[1]+complement.query(cr.lo+a[0]+b[0])>prepared.baseline.lo
                     for a,b in itertools.product(steps,repeat=2))
            result=evaluate_capped(candidate,pool,self.p,prepared)
            self.assertEqual(result['probability_lower'],wins/256)
            self.assertEqual(result['probability_upper'],wins/256)


if __name__ == "__main__":
    unittest.main()
