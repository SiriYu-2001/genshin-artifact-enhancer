from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from enhancer.elixir_report import settings,ranking,calculate
from enhancer.ui_config import compile_config,validate
from enhancer.ui_server import Backend,public_report
from enhancer.batch import save
from test_ui import config
from test_planner import piece
from enhancer.model import SLOTS
from fractions import Fraction as F


class ElixirUI(unittest.TestCase):
    def test_calculation_reserves_higher_priority_inventory_and_can_opt_out(self):
        d=config();d['mode']='multi';d['elixir']={'budget':0}
        second=deepcopy(d['demands'][0]);second['id']='second';second['profile']['character']='另一角色';second['profile']['character_aliases']=['另一角色']
        d['demands'].append(second);key=second['profile']['set_key']
        pool=[piece(f'{slot}:{hits}',slot,sets=key,hits=hits) for slot in SLOTS for hits in (1,3)]
        with TemporaryDirectory() as tmp,patch('enhancer.report.load_scan',return_value=(pool,{a.id:a.id for a in pool},{'complete':True})):
            compile_config(d,tmp);r=calculate(tmp,{'path':'scan'},'second')
            self.assertEqual(r['reserved_count'],5);self.assertFalse(r['plans'])
            d['elixir']['respect_priority']=False;compile_config(d,tmp);free=calculate(tmp,{'path':'scan'},'second')
            self.assertEqual(free['reserved_count'],0);self.assertGreater(free['baseline'][0],r['baseline'][0])

    def test_zero_quota_and_one_quota_cannot_offer_two_definitions(self):
        d=config();key=d['demands'][0]['profile']['set_key'];d['elixir']={'budget':2,'remaining_by_set':{key:0}}
        pool=[piece(s,s,sets=key) for s in SLOTS]
        with TemporaryDirectory() as tmp,patch('enhancer.report.load_scan',return_value=(pool,{a.id:a.id for a in pool},{'complete':True})),\
             patch('enhancer.elixir.ElixirAdvisor.evaluate') as evaluate,patch('enhancer.elixir_plans.compare') as pairs:
            compile_config(d,tmp);r=calculate(tmp,{'path':'scan'},'odetta');evaluate.assert_not_called();pairs.assert_not_called()
            self.assertEqual(r['remaining'],0)
            d['elixir']['remaining_by_set'][key]=1;compile_config(d,tmp)
            def action(slot,main,selected,p_four,minimum):
                self.assertEqual(p_four,F(1,3))
                from enhancer.elixir import COST
                return {'slot':slot,'main':main,'selected':list(selected),'cost':COST[slot],
                        'probability_lower':.5,'expected_gain_lower':.2,'expected_gain_per_elixir_lower':.2/COST[slot]}
            evaluate.side_effect=action;r=calculate(tmp,{'path':'scan'},'odetta')
            self.assertTrue(r['plans']);self.assertTrue(all(p['cost']<=2 and p['definitions']==1 for p in r['plans']));pairs.assert_not_called()

    def test_settings_roundtrip_database_and_shared_set_quota(self):
        d=config();key=d['demands'][0]['profile']['set_key']
        d['elixir']={'budget':7,'remaining_by_set':{key:1},'minimum_gain':.25,'objective':'probability','respect_priority':False}
        with TemporaryDirectory() as tmp:
            backend=Backend(Path(tmp));saved=backend.save_config({'config':d})
            reopened=Backend(Path(tmp));loaded=reopened.store.config(saved['id'])
            self.assertEqual(loaded['elixir'],settings(d['elixir']))
            compiled=json.loads((backend.config_path(saved['id'])/'config.json').read_text(encoding='utf-8'))
            self.assertEqual(compiled['elixir']['remaining_by_set'][key],1)

    def test_invalid_budget_quota_and_probability_override_rejected(self):
        for update in ({'budget':1.5},{'budget':True},{'budget':-1},{'minimum_gain':float('nan')},
                       {'remaining_by_set':{'HeartOfTheFurnace':3}},{'objective':'unknown'},
                       {'p_four':.2},{'respect_priority':1}):
            with self.subTest(update=update),self.assertRaises(ValueError):settings(update)

    def test_worker_only_calls_offline_calculation_even_auto_equip_is_enabled(self):
        from enhancer import ui_worker
        with TemporaryDirectory() as tmp:
            directory=Path(tmp);req=directory/'request.json'
            save(req,{'kind':'elixir','config':'config','snapshot':{'path':'scan'},'demand_id':'odetta','output':str(directory/'result.json')})
            with patch.object(ui_worker.sys,'argv',['worker',str(req)]),patch('enhancer.elixir_report.calculate',return_value={'kind':'elixir','status':'completed'}) as compute,\
                 patch('enhancer.__main__.ensure_controller') as admin,patch('enhancer.equip.apply') as equip,patch('enhancer.finish_equip.apply_finished') as finish:
                ui_worker.main();compute.assert_called_once();admin.assert_not_called();equip.assert_not_called();finish.assert_not_called()

    def test_elixir_stop_does_not_set_global_game_stop_signal(self):
        with TemporaryDirectory() as tmp:
            backend=Backend(Path(tmp));backend.process=Mock();backend.process.poll.return_value=None
            backend.job={'kind':'elixir','stop_requested':False}
            backend.stop();backend.process.terminate.assert_called_once()
            self.assertFalse((backend.runtime/'stop.signal').exists())

    def test_stale_elixir_stop_cannot_interrupt_another_job(self):
        with TemporaryDirectory() as tmp:
            backend=Backend(Path(tmp));backend.process=Mock();backend.process.poll.return_value=None
            backend.job={'id':'new','kind':'start','stop_requested':False}
            with self.assertRaisesRegex(ValueError,'任务已变化'):backend.stop({'job_id':'old','kind':'elixir'})
            backend.process.terminate.assert_not_called();self.assertFalse((backend.runtime/'stop.signal').exists())

    def test_submission_freezes_config_and_validates_target(self):
        with TemporaryDirectory() as tmp:
            backend=Backend(Path(tmp));saved=backend.save_config({'config':config()})
            proc=Mock();proc.poll.return_value=None
            with patch.object(backend,'snapshot',return_value={'path':'test-scan','ownership_stale':False}),\
                 patch('enhancer.ui_server.subprocess.Popen',return_value=proc),patch('enhancer.ui_server.threading.Thread'):
                with self.assertRaises(ValueError):backend.submit({'kind':'elixir','config_id':saved['id'],'demand_id':'missing'})
                answer=backend.submit({'kind':'elixir','config_id':saved['id'],'demand_id':'odetta'})
                request=json.loads((backend.directory/'jobs'/answer['id']/'request.json').read_text(encoding='utf-8'))
                self.assertEqual(request['kind'],'elixir');self.assertEqual(request['demand_id'],'odetta')
                self.assertTrue((Path(request['config'])/'config.json').exists())
                self.assertNotEqual(Path(request['config']),backend.config_path(saved['id']))

    def test_protected_stale_snapshot_rejected(self):
        with TemporaryDirectory() as tmp:
            backend=Backend(Path(tmp));d=config();d['equipment']='protected';saved=backend.save_config({'config':d})
            with patch.object(backend,'snapshot',return_value={'ownership_stale':True}),patch('enhancer.ui_server.subprocess.Popen') as proc:
                with self.assertRaisesRegex(ValueError,'归属可能过期'):backend.submit({'kind':'elixir','config_id':saved['id'],'demand_id':'odetta'})
                proc.assert_not_called()

    def test_latest_report_survives_other_jobs_and_backend_restart(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);d=root/'runtime/ui/jobs/old';d.mkdir(parents=True)
            save(d/'job.json',{'id':'old','kind':'elixir','status':'completed','finished':10,'request':{'snapshot':{'label':'scan','date':'today'}}})
            save(d/'result.json',{'kind':'elixir','status':'completed','character':'奥黛塔','p_four':'1/3','rankings':{}})
            backend=Backend(root);r=backend.latest_elixir()
            self.assertEqual(r['p_four'],'1/3');self.assertEqual(r['snapshot']['label'],'scan')
            self.assertNotIn('single_actions',public_report({'kind':'elixir','single_actions':[1]}))

    def test_ranking_uses_selected_objective_not_one_universal_order(self):
        rows=[{'cost':6,'definitions':2,'probability_lower':.999,'expected_gain_lower':1.48,'expected_gain_per_elixir_lower':1.48/6},
              {'cost':7,'definitions':2,'probability_lower':.99,'expected_gain_lower':1.62,'expected_gain_per_elixir_lower':1.62/7}]
        self.assertEqual(ranking(rows,'probability')[0]['cost'],6)
        self.assertEqual(ranking(rows,'expected_gain')[0]['cost'],7)


if __name__=='__main__':unittest.main()
