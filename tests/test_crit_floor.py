"""Hard CR minimum: exhaustive reference checks, including zero scoring weight."""
from dataclasses import replace
from fractions import Fraction as F
from itertools import product
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
import numpy as np

from enhancer.model import Profile,Stat,ROLLS,SLOTS
from enhancer.capped import CappedInventory,components,main_crit,energy_bounds,build_score,evaluate_capped,Envelope,Point
from enhancer.bootstrap import plan
from enhancer.longterm import Inventory,project
from enhancer.elixir import constrained_thresholds
from test_planner import ROOT,piece


class CritFloorTests(unittest.TestCase):
    def setUp(self):
        self.p=Profile.load(ROOT/'profiles/木偶.json')
        self.p.data['artifact_energy_recharge_min']=0
        self.p.data['weights']['enerRech_']=0

    def pool(self):
        # Trade CR for score/ER, with a legal off-set alternative and CR main.
        self.p.data['main_stats']['circlet']=['critRate_','critDMG_']
        out=[]
        for slot in SLOTS:
            for i in range(3):
                a=piece(f'{slot}:{i}',slot,sets=self.p.data['set_key'] if i<2 else 'other')
                out.append(replace(a,stats=(Stat('critRate_',ROLLS['critRate_'][1]*(i+1),exact=True),
                    Stat('enerRech_',ROLLS['enerRech_'][1]*(3-i),exact=True),
                    Stat('eleMas',ROLLS['eleMas'][1]*(3-i),exact=True),Stat('def',ROLLS['def'][0],exact=True))))
        head=piece('cr-head','circlet')
        out.append(replace(head,main='critRate_',stats=tuple(Stat('hp',F(209),exact=True) if s.key=='critRate_' else s for s in head.stats)))
        return out

    def test_matches_brute_force_with_cap_floor_main_and_zero_weight(self):
        pool=self.pool()
        for cap,floor,er,weight in product((0,25,70),(0,35,55,100),(0,45),(0,1)):
            with self.subTest(cap=cap,floor=floor,er=er,weight=weight):
                self.p.data.update(artifact_crit_rate_cap=cap,artifact_crit_rate_min=floor,artifact_energy_recharge_min=er)
                self.p.data['weights']['critRate_']=weight
                legal=[b for b in product(*[[a for a in pool if a.slot==s] for s in SLOTS])
                       if sum(a.set_key==self.p.data['set_key'] for a in b)>=4
                       and sum(components(a,self.p)[0].lo+main_crit(a) for a in b)>=floor
                       and sum(energy_bounds(a).lo for a in b)>=er]
                inv=CappedInventory(pool,self.p)
                expected=max((build_score(b,self.p).lo for b in legal),default=None)
                self.assertEqual(inv.baseline.lo if inv.baseline else None,expected)
                base=project(pool,self.p,'lo');dim=4 if er else 3
                numeric=Inventory(base,{k:np.empty((0,dim)) for k in base},cap,float(self.p.weight('critRate_')/F('3.305')),er,floor)
                result=numeric.score(numeric.frontier())
                if expected is None:self.assertEqual(result,-np.inf)
                else:self.assertAlmostEqual(result,float(expected),9)

    def test_envelope_range_query_matches_exhaustive(self):
        points=tuple(Point(F(c),F(80-c*c//4),main_crit=F(m),energy=F(e))
                     for c in range(0,20,2) for m in (0,31) for e in (0,10))
        for cap,floor in ((0,40),(20,40),(45,30)):
            env=Envelope(points,F(cap),F('.3'),F(15),F(floor))
            for cr,main,er in product(range(0,30,3),(0,31),(0,5,15)):
                vals=[p.other+F('.3')*min(p.crit+cr,max(F(0),F(cap)-p.main_crit-main)) for p in points
                      if p.crit+cr+p.main_crit+main>=floor and p.energy+er>=15]
                self.assertEqual(env.query(F(cr),F(main),F(er)),max(vals) if vals else None)

    def test_future_cr_roll_can_make_zero_weight_candidate_feasible(self):
        self.p.data.update(artifact_crit_rate_min=15.55,artifact_crit_rate_cap=0)
        self.p.data['weights'].update(critRate_=0,eleMas=1)
        pool=[piece(s,s) for s in SLOTS]
        a=replace(piece('new','flower',level=16),stats=(Stat('critRate_',F('2.72'),exact=True),
            Stat('enerRech_',F('5.83'),exact=True),Stat('eleMas',F('42'),exact=True),Stat('def',F('16.2'),exact=True)))
        inv=CappedInventory(pool,self.p);wins=0
        for i,stat in enumerate(a.stats):
            for roll in ROLLS[stat.key]:
                stats=list(a.stats);stats[i]=replace(stat,value=stat.value+roll)
                outcome=replace(a,level=20,stats=tuple(stats))
                wins+=CappedInventory(pool+[outcome],self.p).baseline.lo>inv.baseline.lo
        self.assertEqual(wins,4)
        self.assertEqual(evaluate_capped(a,pool,self.p,inv)['probability_lower'],wins/16)

    def test_resource_thresholds_reject_high_score_below_floor(self):
        env=Envelope((Point(F(10),F(0)),),F(0),F(0),crit_floor=F('13.3'))
        cuts,prob=constrained_thresholds(env,F(0),F(0),1)
        self.assertEqual(float(prob[np.isfinite(cuts)].sum()),.5)
        from enhancer.elixir_plans import score
        self.p.data.update(artifact_crit_rate_min=15,artifact_crit_rate_cap=0)
        inv=CappedInventory([piece(s,s) for s in SLOTS],self.p)
        vals=score((Point(F(10),F(0)),),np.array([4.,5.]),np.array([99.,99.]),0,inv)
        self.assertEqual(vals.tolist(),[-np.inf,99.])

    def test_two_rolls_joint_cr_er_feasibility_matches_enumeration(self):
        self.p.data.update(artifact_crit_rate_min=15.55,artifact_energy_recharge_min=25.9,artifact_crit_rate_cap=0)
        self.p.data['weights'].update(critRate_=0,enerRech_=0,eleMas=1)
        pool=[piece(s,s) for s in SLOTS]
        a=replace(piece('new','flower',level=12),stats=(Stat('critRate_',F('2.72'),exact=True),
            Stat('enerRech_',F('4.53'),exact=True),Stat('eleMas',F('42'),exact=True),Stat('def',F('16.2'),exact=True)))
        inv=CappedInventory(pool,self.p);wins=0
        steps=[(i,r) for i,s in enumerate(a.stats) for r in ROLLS[s.key]]
        for first,second in product(steps,repeat=2):
            stats=list(a.stats)
            for i,roll in (first,second):stats[i]=replace(stats[i],value=stats[i].value+roll)
            terminal=replace(a,level=20,stats=tuple(stats))
            wins+=CappedInventory(pool+[terminal],self.p).baseline.lo>inv.baseline.lo
        self.assertEqual(wins,32)
        self.assertEqual(evaluate_capped(a,pool,self.p,inv)['probability_lower'],wins/256)

    def test_bootstrap_respects_both_floors(self):
        self.p.data.update(artifact_crit_rate_min=200,artifact_energy_recharge_min=20)
        pool=[piece(s,s,level=16 if s=='flower' else 20) for s in SLOTS]
        self.assertIsNone(plan(pool,self.p,samples=128,time_limit=10))
        self.p.data['artifact_crit_rate_min']=15
        result=plan(pool,self.p,samples=128,time_limit=10)
        self.assertEqual(result['feasibility_probability_estimate'],1)

    def test_default_zero_validation_and_database_roundtrip(self):
        from enhancer.ui_config import validate
        from enhancer.ui_server import Backend,public_report
        from test_ui import config
        d=config();self.assertEqual(validate(d)['demands'][0]['profile']['artifact_crit_rate_min'],0)
        p=d['demands'][0]['profile'];p.update(artifact_crit_rate_min=50,artifact_crit_rate_cap=40)
        with TemporaryDirectory() as tmp:
            b=Backend(Path(tmp));saved=b.save_config({'config':d})
            self.assertEqual(Backend(Path(tmp)).store.config(saved['id'])['demands'][0]['profile']['artifact_crit_rate_min'],50)
        for bad in (-1,201,float('nan'),True):
            p['artifact_crit_rate_min']=bad
            with self.assertRaises(ValueError):validate(d)
        result=public_report({'demands':[{'id':'test','artifact_crit_rate_min':50,'artifact_crit_rate_total':51}]})
        self.assertEqual(result['demands'][0]['artifact_crit_rate_total'],51)


if __name__=='__main__':unittest.main()
