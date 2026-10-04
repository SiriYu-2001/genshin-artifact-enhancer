from fractions import Fraction as F
from collections import Counter
import unittest
from unittest.mock import Mock
import numpy as np

from enhancer.elixir import ElixirAdvisor,DEFINITION_FOUR_LINE_PROBABILITY,remaining_types,upgrade_counts,roll_sums,tail_metrics,target_metrics
from enhancer.model import Profile


class ElixirTests(unittest.TestCase):
    def test_target_progress_caps_overshoot_and_attainment_includes_equality(self):
        v=np.array([1.,2.,4.]);p=np.array([.25,.5,.25]);tail=np.r_[np.cumsum(p[::-1])[::-1],0.];moment=np.r_[np.cumsum((v*p)[::-1])[::-1],0.]
        prob,gain=target_metrics(v,tail,moment,np.array([2.]),2.)
        np.testing.assert_allclose(prob,[.25]);np.testing.assert_allclose(gain,[.5])
        _,capped=target_metrics(v,tail,moment,np.array([2.]),1.)
        np.testing.assert_allclose(capped,[.25])
        _,already=target_metrics(v,tail,moment,np.array([2.]),-1.)
        np.testing.assert_allclose(already,[0])
    def test_definition_mixes_one_third_four_liners_not_domain_one_fifth(self):
        self.assertEqual(DEFINITION_FOUR_LINE_PROBABILITY,F(1,3))
        advisor=ElixirAdvisor.__new__(ElixirAdvisor)
        advisor.profile=Profile({'main_stats':{'flower':['hp']}})
        advisor.conditional=Mock(side_effect=[np.array([.1,.2,.3,.4]),np.array([.7,.8,.9,1.])])
        result=advisor.evaluate('flower','hp',('critRate_','critDMG_'))
        self.assertAlmostEqual(result['p_four_assumed'],1/3)
        self.assertAlmostEqual(result['probability_lower'],.3)
        self.assertAlmostEqual(result['expected_gain_lower'],.5)
        self.assertNotAlmostEqual(result['probability_lower'],.8*.1+.2*.7)
        self.assertEqual([c.args[3] for c in advisor.conditional.call_args_list],[4,5])
    def test_unsupported_set_rule_is_not_silently_treated_as_four_piece(self):
        with self.assertRaisesRegex(ValueError,'4\+1'):
            ElixirAdvisor([],Profile({'set_requirement':2,'normalization':'mean_roll'}))

    def test_guarantee_matches_floored_binomial_not_two_free_hits(self):
        for n,expected in [(4,{2:F(11,16),3:F(4,16),4:F(1,16)}),
                           (5,{2:F(16,32),3:F(10,32),4:F(5,32),5:F(1,32)})]:
            actual=Counter()
            for counts,p in upgrade_counts(n):actual[counts[0]+counts[1]]+=p
            self.assertEqual(dict(actual),expected)
            self.assertEqual(sum(actual.values()),1)

    def test_remaining_stats_are_weighted_without_replacement(self):
        rows=remaining_types('atk_',('critRate_','critDMG_'))
        self.assertEqual(sum(p for a,b,p in rows),1)
        self.assertEqual(len(rows),21)
        self.assertTrue(all(a!=b and not {'atk_','critRate_','critDMG_'}&{a,b} for a,b,p in rows))
        weights={frozenset((a,b)):p for a,b,p in rows}
        self.assertGreater(weights[frozenset(('hp','def'))],weights[frozenset(('hp_','def_'))])

    def test_illegal_selected_stats_rejected(self):
        for selected in [('atk_','critRate_'),('critRate_','critRate_'),('unknown','critRate_')]:
            with self.assertRaises(ValueError):remaining_types('atk_',selected)

    def test_roll_sum_preserves_exact_internal_cents_and_mass(self):
        support,den=roll_sums('critRate_',2)
        self.assertEqual(den,16);self.assertEqual(sum(n for x,n in support),16)
        self.assertEqual(support[0][0],544);self.assertEqual(support[-1][0],778)
        self.assertIn((622,3),support)

    def test_expected_gain_is_unconditional_and_never_negative(self):
        v=np.array([1.,2.,4.]);p=np.array([.25,.5,.25])
        tail=np.r_[np.cumsum(p[::-1])[::-1],0.]
        moment=np.r_[np.cumsum((v*p)[::-1])[::-1],0.]
        prob,gain=tail_metrics(v,tail,moment,np.array([2.,5.]))
        np.testing.assert_allclose(prob,[.25,0]);np.testing.assert_allclose(gain,[.5,0])
        prob2,gain2=tail_metrics(v,tail,moment,np.array([2.,5.]),2.)
        np.testing.assert_allclose(prob2,[0,0]);np.testing.assert_allclose(gain2,gain)


if __name__=='__main__':unittest.main()
