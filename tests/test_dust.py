from dataclasses import replace
from fractions import Fraction as F
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest

from enhancer.dust import transition,infer_bases,DustAdvisor,COST
from enhancer.dust_report import settings
from enhancer.elixir import upgrade_counts
from enhancer.model import Artifact,Stat,Profile,SLOTS,ROLLS
from enhancer.ui_config import compile_config
from enhancer.ui_server import Backend,public_report
from test_planner import piece,ROOT
from test_ui import config


def example_piece():
    return Artifact('example','ObsidianCodex','circlet','critDMG_',20,
        tuple(Stat.from_dict({'key':k,'value':v}) for k,v in [('enerRech_',16.8),('atk_',21.0),('def',19),('critRate_',3.1)]))


class DustTests(unittest.TestCase):
    def test_screenshot_is_ordinary_then_decreed(self):
        a=transition(2,2,2);self.assertEqual(a,{'guarantee':2,'points':4,'phase':2,'triggered':False})
        b=transition(a['points'],a['phase'],2);self.assertEqual(b,{'guarantee':4,'points':0,'phase':0,'triggered':True})

    def test_global_cycle_and_overflow(self):
        points=phase=0;guarantees=[]
        for _ in range(18):
            step=transition(points,phase,1);points,phase=step['points'],step['phase']
            if step['triggered']:guarantees.append(step['guarantee'])
        self.assertEqual(guarantees,[3,3,4]);self.assertEqual((points,phase),(0,0))
        self.assertEqual(transition(5,1,2),{'guarantee':3,'points':1,'phase':2,'triggered':True})
        self.assertEqual([COST[s] for s in SLOTS],[1,1,2,2,2])

    def test_four_hit_pity_is_not_fifty_percent_five_hits(self):
        outcomes=upgrade_counts(5,4)
        self.assertEqual(sum(p for c,p in outcomes if c[0]+c[1]==5),F(1,32))
        self.assertEqual(sum(p for c,p in outcomes if c[0]+c[1]==4),F(31,32))

    def test_count_inference_uses_joint_history_and_preserves_unknown_bases(self):
        cases=infer_bases(example_piece())
        self.assertEqual([n for n,b in cases],[5])
        b=cases[0][1];self.assertEqual(b['critRate_'].lo,F('3.11'));self.assertEqual(b['critRate_'].hi,F('3.11'))
        self.assertLess(b['enerRech_'].lo,b['enerRech_'].hi)
        with self.assertRaises(ValueError):infer_bases(example_piece(),{'upgrade_rolls':4})
        with self.assertRaises(ValueError):infer_bases(example_piece(),{'initial_values':{'critRate_':3.89}})

    def test_verified_initials_and_defined_pairs(self):
        a=example_piece();meta={'upgrade_rolls':5,'initial_values':{'enerRech_':4.53,'atk_':5.25,'def':18.52,'critRate_':3.11}}
        self.assertTrue(all(b.lo==b.hi for b in infer_bases(a,meta)[0][1].values()))
        p=Profile.load(ROOT/'profiles/木偶.json');pool=[piece(s,s,hits=2) for s in SLOTS]+[a]
        advisor=DustAdvisor(pool,p)
        with self.assertRaisesRegex(ValueError,'锁定词条'):advisor.evaluate(replace(a,special='defined'))
        answer=advisor.evaluate(replace(a,special='defined'),dict(meta,defined_pair=['critRate_','atk_']))
        self.assertEqual(len(answer['actions']),1);self.assertTrue(answer['initial_exact'])
        ordinary=advisor.evaluate(a,meta);self.assertEqual(len(ordinary['actions']),6)
        for pair in ordinary['actions']:
            for stats in pair['guarantees'].values():
                self.assertGreaterEqual(stats['expected_gain_lower'],0)
                self.assertLessEqual(stats['probability_lower'],stats['probability_upper']+1e-12)

    def test_no_mean_base_or_type_prior_is_filled_in(self):
        cases=infer_bases(example_piece());self.assertEqual(cases[0][1]['enerRech_'].lo,ROLLS['enerRech_'][0])
        with self.assertRaises(ValueError):infer_bases(example_piece(),{'initial_values':{'enerRech_':5.505}})

    def test_already_achieved_goal_has_zero_progress_value(self):
        p=Profile.load(ROOT/'profiles/木偶.json');p.data['resource_target_score']=0.
        a=example_piece();pool=[piece(s,s,hits=2) for s in SLOTS]+[a]
        answer=DustAdvisor(pool,p).evaluate(a)
        for action in answer['actions']:
            for r in action['guarantees'].values():
                self.assertAlmostEqual(r['target_probability_lower'],1.)
                self.assertAlmostEqual(r['target_gain_lower'],0.)
                self.assertAlmostEqual(r['target_gain_upper'],0.)

    def test_state_and_metadata_validation(self):
        for args in [(6,0,1),(0,3,1),(False,0,1),(0,0,4)]:
            with self.assertRaises(ValueError):transition(*args)
        for item in [{'points':6},{'phase':3},{'budget':-1},{'budget':1.1},{'metadata':{'not-fingerprint':{}}}]:
            with self.assertRaises(ValueError):settings(item)

    def test_settings_persist_in_existing_database(self):
        d=config();d['dust']={'budget':33,'points':2,'phase':2,'objective':'efficiency','respect_priority':True,'metadata':{}}
        with TemporaryDirectory() as tmp:
            backend=Backend(Path(tmp));saved=backend.save_config({'config':d})
            self.assertEqual(Backend(Path(tmp)).store.config(saved['id'])['dust'],settings(d['dust']))

    def test_dust_worker_cannot_enter_game_branch(self):
        from enhancer import ui_worker
        from enhancer.batch import save
        with TemporaryDirectory() as tmp:
            root=Path(tmp);req=root/'request.json';save(req,{'kind':'dust','config':'c','snapshot':{'path':'s'},'demand_id':'d','output':str(root/'result.json')})
            with patch.object(ui_worker.sys,'argv',['worker',str(req)]),patch('enhancer.dust_report.calculate',return_value={'kind':'dust','status':'completed'}) as calc,\
                 patch('enhancer.__main__.ensure_controller') as admin,patch('enhancer.equip.apply') as equip,patch('enhancer.finish_equip.apply_finished') as finish:
                ui_worker.main();calc.assert_called_once();admin.assert_not_called();equip.assert_not_called();finish.assert_not_called()
        self.assertNotIn('actions',public_report({'kind':'dust','status':'completed','actions':['internal']}))


if __name__=='__main__':unittest.main()
