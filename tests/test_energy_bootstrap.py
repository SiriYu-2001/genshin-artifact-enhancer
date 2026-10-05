from dataclasses import replace
from fractions import Fraction as F
from itertools import product
from math import comb
import unittest
import numpy as np

from enhancer.model import Profile,Stat,ROLLS,SLOTS
from enhancer.capped import CappedInventory,energy_bounds,main_energy,build_score,evaluate_capped
from enhancer.bootstrap import plan,terminal_samples
from enhancer.elixir import upgrade_counts,constrained_thresholds,tail_metrics
from test_planner import ROOT,piece


class EnergyBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.p=Profile.load(ROOT/'profiles/木偶.json')
        self.p.data['weights']['enerRech_']=0
        self.p.data['artifact_energy_recharge_min']=25

    def energy_piece(self,id,slot,hits=1,crit=1,level=20):
        a=piece(id,slot,hits=crit,level=level)
        return replace(a,stats=(Stat('critRate_',ROLLS['critRate_'][1]*crit,exact=True),
            Stat('eleMas',ROLLS['eleMas'][1]*crit,exact=True),
            Stat('enerRech_',ROLLS['enerRech_'][1]*hits,exact=True),Stat('def',ROLLS['def'][0],exact=True)))

    def test_hard_floor_matches_brute_force_even_when_er_weight_is_zero(self):
        pool=[self.energy_piece(s+str(i),s,hits=1+i*2,crit=3-i) for s in SLOTS for i in range(2)]
        inv=CappedInventory(pool,self.p)
        legal=[b for b in product(*[[a for a in pool if a.slot==s] for s in SLOTS]) if sum(energy_bounds(a).lo for a in b)>=25]
        expected=max(build_score(b,self.p).lo for b in legal)
        self.assertEqual(inv.baseline.lo,expected)
        chosen=[next(a for a in pool if a.id==id) for id in inv.best_ids]
        self.assertGreaterEqual(sum(energy_bounds(a).lo for a in chosen),25)

    def test_er_sands_count_main_stat_and_no_base_100(self):
        a=replace(self.energy_piece('s','sands'),main='enerRech_',stats=(Stat('critRate_',F('3.11'),exact=True),))
        self.assertAlmostEqual(float(main_energy(a)),51.8)
        self.assertEqual(energy_bounds(a).lo,main_energy(a))

    def test_one_remaining_roll_probability_matches_all_outcomes(self):
        pool=[self.energy_piece(s,s,crit=2) for s in SLOTS]
        self.p.data['artifact_energy_recharge_min']=float(sum(energy_bounds(a).lo for a in pool))
        a=self.energy_piece('new','flower',crit=2,level=16)
        inv=CappedInventory(pool,self.p);wins=0
        for i,stat in enumerate(a.stats):
            for roll in ROLLS[stat.key]:
                stats=list(a.stats);stats[i]=replace(stat,value=stat.value+roll)
                candidate=replace(a,level=20,stats=tuple(stats))
                wins+=CappedInventory(pool+[candidate],self.p).baseline.lo>inv.baseline.lo
        self.assertEqual(evaluate_capped(a,pool,self.p,inv)['probability_lower'],wins/16)

    def test_no_feasible_build_is_not_a_zero_baseline(self):
        self.p.data['artifact_energy_recharge_min']=250
        self.assertIsNone(CappedInventory([self.energy_piece(s,s) for s in SLOTS],self.p).baseline)

    def test_bootstrap_from_missing_level20_piece(self):
        self.p.data['artifact_energy_recharge_min']=0
        pool=[piece(s,s,hits=1,level=0 if s=='circlet' else 20) for s in SLOTS]
        self.assertIsNone(CappedInventory(pool,self.p).baseline)
        proposed=plan(pool,self.p,samples=128,time_limit=10)
        self.assertEqual(proposed['pending_items'],['circlet'])
        self.assertGreater(proposed['expected_score_lower_model'],0)
        self.assertEqual(proposed['feasibility_probability_estimate'],1)

    def test_bootstrap_does_not_invent_missing_slot(self):
        self.assertIsNone(plan([piece('f','flower',level=0)],self.p,samples=128))

    def test_starter_is_finished_before_chasing_a_different_unleveled_piece(self):
        self.p.data['artifact_energy_recharge_min']=0
        pool=[piece(s,s,level=0 if s in ('flower','sands') else 20) for s in SLOTS]
        initial=plan(pool,self.p,samples=128,time_limit=10)
        flower=pool[0]
        pool[0]=replace(flower,level=20,stats=tuple(replace(s,value=s.value+5*ROLLS['def'][1]) if s.key=='def' else s for s in flower.stats))
        pool.append(piece('another-flower','flower',level=0))
        continued=plan(pool,self.p,samples=128,committed=initial)
        self.assertEqual(continued['items'],initial['items'])
        self.assertEqual(continued['pending_items'],['sands'])

    def test_sampling_respects_deterministic_fourth_stat_activation(self):
        a=piece('f','flower',level=0)
        a=replace(a,stats=tuple(replace(s,pending=i==3) for i,s in enumerate(a.stats)))
        x=terminal_samples(a,self.p,128)
        self.assertEqual(x.shape,(128,3))
        self.assertTrue(np.array_equal(x,terminal_samples(a,self.p,128)))

    def test_infeasible_threshold_does_not_produce_nan_gain(self):
        p,g=tail_metrics(np.array([0.,1.]),np.array([1.,.5,0.]),np.array([.5,.5,0.]),np.array([float('inf')]))
        self.assertEqual(p.tolist(),[0.]);self.assertEqual(g.tolist(),[0.])

    def test_longterm_optimizer_uses_the_same_hard_constraint(self):
        from enhancer.longterm import project,Inventory
        pool=[self.energy_piece(s+str(i),s,hits=1+i*2,crit=3-i) for s in SLOTS for i in range(2)]
        base=project(pool,self.p,'lo');empty={k:np.empty((0,4)) for k in base}
        inv=Inventory(base,empty,44.8,float(self.p.weight('critRate_')/F('3.305')),25)
        self.assertAlmostEqual(inv.score(inv.frontier()),float(CappedInventory(pool,self.p).baseline.lo))
        for er in (F(0),F(10),F(25)):
            exact=CappedInventory(pool,self.p).complement(pool[0],'lo').query(F(10),F(0),er)
            expected=-np.inf if exact is None else float(exact)+100
            self.assertAlmostEqual(inv.candidate('flower',True,10,100,0,float(er)),expected)

    def test_energy_threshold_couples_feasibility_and_expected_gain(self):
        from enhancer.capped import Envelope,Point
        from enhancer.elixir import constrained_thresholds
        env=Envelope((Point(F(0),F(0),energy=F(0)),),F(100),F(0),F('5.5'))
        cuts,prob=constrained_thresholds(env,F(0),F(0),0,1,energy_weight=F(0))
        self.assertEqual(sum(prob[np.isfinite(cuts)]),.5)

    def test_floor_persists_in_config_and_zero_is_backward_compatible(self):
        from test_ui import config
        from enhancer.ui_config import validate
        d=config();d['demands'][0]['profile']['artifact_energy_recharge_min']=63.7
        self.assertEqual(validate(d)['demands'][0]['profile']['artifact_energy_recharge_min'],63.7)
        d['demands'][0]['profile']['artifact_energy_recharge_min']=-1
        with self.assertRaises(ValueError):validate(d)

    def test_dust_lower_tail_clamping_and_unchanged_perfect_probability(self):
        for n in (4,5):
            for guarantee in (2,3,4):
                dist={h:sum(p for c,p in upgrade_counts(n,guarantee) if c[0]+c[1]==h) for h in range(n+1)}
                for h in range(n+1):
                    expected=F(0) if h<guarantee else sum(F(comb(n,k),2**n) for k in range(guarantee+1)) if h==guarantee else F(comb(n,h),2**n)
                    self.assertEqual(dist[h],expected)
                if n==5:self.assertEqual(dist[5],F(1,32))
        self.assertEqual(sum(p for c,p in upgrade_counts(4,4) if c[0]+c[1]==4),1)

if __name__=='__main__':unittest.main()
