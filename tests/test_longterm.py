import itertools
import unittest
from dataclasses import replace
from fractions import Fraction as F
import numpy as np

from enhancer.longterm import (Inventory,project,pareto,sample_natural,MAIN_PROBS,
                               settings,summary,calculate,dust_samples)
from enhancer.model import Profile,SLOTS,ROLLS,Stat
from enhancer.capped import CappedInventory,main_crit
from test_planner import piece,ROOT


class LongtermTests(unittest.TestCase):
    def setUp(self):
        self.p=Profile.load(ROOT/'profiles/木偶.json')
        self.pool=[piece(s,s,hits=5) for s in SLOTS]

    def test_numeric_optimizer_matches_rational_with_cap_offset_main_and_conflicts(self):
        self.p.data['main_stats']['circlet']=['critDMG_','critRate_']
        self.p.data['artifact_crit_rate_cap']=27
        pool=[piece(f'{s}:{i}',s,hits=i+2,sets=self.p.data['set_key'] if i<2 else 'other') for s in SLOTS for i in range(3)]
        head=pool[-1]
        pool[-1]=replace(head,main='critRate_',stats=tuple(Stat('hp',ROLLS['hp'][1],exact=True) if x.key=='critRate_' else x for x in head.stats))
        for bound in ('lo','hi'):
            base=project(pool,self.p,bound);empty={k:np.empty((0,3)) for k in base}
            inv=Inventory(base,empty,27,float(self.p.weight('critRate_')/F('3.305')))
            rational=CappedInventory(pool,self.p)
            self.assertAlmostEqual(inv.score(inv.frontier()),float(getattr(rational.baseline,bound)),10)
            for a in pool:
                for cr in (0,12,25):
                    expected=rational.complement(a,bound).query(F(cr),main_crit(a))+F(4)
                    self.assertAlmostEqual(inv.candidate(a.slot,a.set_key==self.p.data['set_key'],cr,4,float(main_crit(a))),float(expected),10)

    def test_main_probabilities_and_natural_source(self):
        for p in MAIN_PROBS.values():self.assertAlmostEqual(sum(p.values()),1)
        self.assertEqual(MAIN_PROBS['goblet']['atk_'],.1925)
        slots,targets,rows,legal=sample_natural(self.p,100000,np.random.default_rng(3))
        self.assertLess(abs(targets.mean()-.5),.01)
        expected=np.mean([sum(MAIN_PROBS[s][k] for k in self.p.data['main_stats'][s]) for s in SLOTS])
        self.assertLess(abs(legal.mean()-expected),.01)
        self.assertTrue(np.all(rows>=0))

    def test_new_frontier_excludes_unchanged_build_and_matches_exhaustive(self):
        cap=10;weight=.4
        base={(s,t):np.array([[3.,4.,0.]]) for s in SLOTS for t in (False,True)}
        future={(s,t):np.array([[2.,3.,0.]]) for s in SLOTS for t in (False,True)}
        future['circlet',False]=np.array([[0.,8.,31.1]])
        inv=Inventory(base,future,cap,weight)
        expected=-np.inf
        for off in SLOTS:
            for chosen in itertools.product((0,1),repeat=5):
                if not any(chosen):continue
                row=sum((future if is_new else base)[s,s!=off][0] for s,is_new in zip(SLOTS,chosen))
                expected=max(expected,row[1]+weight*min(row[0],max(0,cap-row[2])))
        self.assertAlmostEqual(inv.score(inv.new_frontier()),expected)
        # Dominated drops cannot turn rounding width of unchanged gear into a win.
        future={(s,t):np.array([[0.,0.,0.]]) for s in SLOTS for t in (False,True)}
        inv=Inventory(base,future,cap,weight)
        self.assertLess(inv.score(inv.new_frontier()),inv.score(inv.frontier()))

    def test_forecast_settings_roundtrip_local_database(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from enhancer.ui_server import Backend
        from test_ui import config
        d=config()
        for k in ('elixir','dust'):d[k]={'longterm':{'enabled':True,'days':60,'daily_resin':120,'samples':128}}
        with TemporaryDirectory() as tmp:
            backend=Backend(Path(tmp));saved=backend.save_config({'config':d})
            loaded=Backend(Path(tmp)).store.config(saved['id'])
            for k in ('elixir','dust'):
                self.assertEqual(loaded[k]['longterm'],settings(d[k]['longterm']))

    def test_dust_late_guarantee_and_fixed_base_bounds(self):
        a=piece('a',hits=6)
        out=dust_samples(self.p,a,['critRate_','enerRech_'],{},4,10000,42)
        self.assertEqual(len(out),1)
        lo,hi=out[0]
        self.assertTrue(np.all(lo[0]<=hi[0]))
        self.assertTrue(np.all(lo[1]<=hi[1]))

    def test_zero_hits_report_nonzero_upper_confidence(self):
        r=summary(np.zeros(512),True)
        self.assertEqual(r['mean'],0)
        self.assertGreater(r['mc95'][1],0)

    def test_zero_horizon_reproducible_and_cost_scaled_no_actions(self):
        r={'kind':'elixir','single_actions':[],'remaining':0,'options':{'budget':0}}
        o=settings({'enabled':True,'days':0,'samples':128})
        a=calculate(self.pool,self.p,r,o);b=calculate(self.pool,self.p,r,o)
        self.assertEqual(a,b)
        self.assertEqual(a['horizons'][0]['natural_gain']['estimate'],[0,0])
        self.assertEqual(a['horizons'][0]['expected_drops'],0)
        self.assertEqual(a['shortlist_count'],0)

    def test_one_action_positive_natural_progress_and_consistent_bounds(self):
        a={'slot':'flower','main':'hp','selected':['critRate_','critDMG_'],'cost':1,'p_four_assumed':1/3,
           'expected_gain_lower':1,'expected_gain_upper':2,'probability_lower':.4}
        r={'kind':'elixir','single_actions':[a],'remaining':1,'options':{'budget':1}}
        out=calculate(self.pool,self.p,r,settings({'enabled':True,'days':2,'daily_resin':20,'samples':128}))
        self.assertEqual([h['days'] for h in out['horizons']],[0,1,2])
        for h in out['horizons']:
            rec=h['rankings']['expected_gain'][0]
            self.assertEqual(rec['expected_gain'],rec['efficiency'])
            self.assertLessEqual(*rec['expected_gain']['estimate'])
        self.assertGreaterEqual(out['horizons'][-1]['natural_gain']['estimate'][0],0)

    def test_old_dust_and_new_never_coexist(self):
        base=project(self.pool,self.p,'lo');empty={k:np.empty((0,3)) for k in base}
        inv=Inventory(base,empty,44.8,1/3.305)
        # With zero stats, forced replacement scores four pieces, not five plus a new one.
        a=piece('weak')
        expected=CappedInventory(self.pool,self.p).complement(a,'lo').query(F(0))
        self.assertAlmostEqual(inv.candidate('flower',True,0,0,0),float(expected))

    def test_invalid_parameters_and_optional_defaults(self):
        self.assertFalse(settings()['enabled'])
        for x in ({'days':-1},{'daily_resin':True},{'samples':0},{'samples':512.0},{'five_star_per_20':float('nan')},{'unknown':1}):
            with self.assertRaises(ValueError):settings(x)


if __name__=='__main__':unittest.main()
