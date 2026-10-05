from collections import Counter
from fractions import Fraction as F
import unittest

from enhancer.capped import exact_roll_counts,joint_distribution,energy_joint_distribution
from enhancer.model import ROLLS,MEANS


class ExactRollTests(unittest.TestCase):
    def test_integer_lattice_matches_direct_fraction_enumeration(self):
        keys=('critRate_','enerRech_','atk','critDMG_')
        weights=(F(1),F(0),F(1,4),F(3,4))
        for dimensions in (2,3):
            steps=Counter()
            for k,w in zip(keys,weights):
                for v in ROLLS[k]:
                    point=(v if k=='critRate_' else F(0),F(0) if k=='critRate_' else w*v/MEANS[k])
                    if dimensions==3:point=(point[0],v if k=='enerRech_' else F(0),point[1])
                    steps[point]+=1
            for rolls in (0,1,2,3):
                direct=Counter({(F(0),)*dimensions:1})
                for _ in range(rolls):
                    nxt=Counter()
                    for a,n in direct.items():
                        for b,m in steps.items():nxt[tuple(x+y for x,y in zip(a,b))]+=n*m
                    direct=nxt
                actual=dict(exact_roll_counts(tuple(sorted(steps.items())),rolls))
                self.assertEqual(actual,direct)
                self.assertEqual(sum(actual.values()),16**rolls)

    def test_substat_permutations_share_distribution_cache(self):
        keys=('critRate_','critDMG_','atk','enerRech_');weights=(F(1),F(1),F(1,4),F(0))
        for fn in (joint_distribution,energy_joint_distribution):
            fn.cache_clear();exact_roll_counts.cache_clear()
            a=fn(keys,weights,3);before=exact_roll_counts.cache_info()
            b=fn(keys[::-1],weights[::-1],3);after=exact_roll_counts.cache_info()
            self.assertEqual(a,b)
            self.assertEqual(after.misses,before.misses)
            self.assertEqual(after.hits,before.hits+1)

if __name__=='__main__':unittest.main()
