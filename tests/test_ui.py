from copy import deepcopy
from contextlib import nullcontext
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from unittest.mock import patch

from enhancer.ui_config import validate,compile_config,materialize_snapshot
from enhancer.ui_server import Backend,make_handler
from enhancer.model import Profile
from enhancer.report import load_scan
from enhancer.batch import save
from enhancer.navigation import row_major_points
from enhancer.equip import quick_reject
from test_planner import ROOT


def config():
    return {'name':'测试配置','mode':'single','equipment':'borrow','allocation':'priority',
            'demands':[{'id':'odetta','profile':deepcopy(Profile.load(ROOT/'profiles/奥黛塔.json').data)}],
            'scenarios':None}


class UIConfigTests(unittest.TestCase):
    def test_worker_failure_leaves_a_resumable_manifest(self):
        from enhancer import ui_worker
        with TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'runtime').mkdir()
            save(root/'runtime/active-batch.json',{'status':'running','current':'scan:1'})
            req=root/'request.json';save(req,{'kind':'resume','output':str(root/'result.json')})
            with patch.object(ui_worker,'ROOT',root),patch.object(ui_worker.sys,'argv',['worker',str(req)]),\
                 patch('enhancer.game_lease.GameLease',return_value=nullcontext()),\
                 patch('enhancer.__main__.ensure_controller'),patch('enhancer.batch.main',side_effect=RuntimeError('read failed')):
                with self.assertRaises(RuntimeError):ui_worker.main()
            self.assertEqual(json.loads((root/'runtime/active-batch.json').read_text())['status'],'needs-attention')

    def test_busy_game_lease_rejects_new_job_without_changing_existing_batch(self):
        from enhancer import ui_worker
        with TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'runtime').mkdir()
            save(root/'runtime/active-batch.json',{'status':'running','current':'previous-job'})
            req=root/'request.json';save(req,{'kind':'resume','output':str(root/'result.json')})
            with patch.object(ui_worker,'ROOT',root),patch.object(ui_worker.sys,'argv',['worker',str(req)]),\
                 patch('enhancer.game_lease.GameLease',side_effect=RuntimeError('another worker is active')),\
                 patch.object(ui_worker,'_main') as action:
                with self.assertRaisesRegex(RuntimeError,'another worker'):ui_worker.main()
                action.assert_not_called()
            self.assertEqual(json.loads((root/'runtime/active-batch.json').read_text())['status'],'running')
            self.assertEqual(json.loads((root/'result.json').read_text())['status'],'failed')
    def test_compile_roundtrip_and_numeric_hyperparameters(self):
        d=config();d['demands'][0]['profile']['artifact_crit_rate_cap']=47
        with TemporaryDirectory() as tmp:
            compile_config(d,tmp)
            from enhancer.campaign import Campaign
            c=Campaign.load(Path(tmp)/'campaign.json')
        self.assertEqual(float(c.demands[0].profile.threshold),.05)
        self.assertEqual(c.demands[0].profile.data['artifact_crit_rate_cap'],47)
        self.assertEqual(c.demands[0].profile.weight('enerRech_'),0)
        self.assertEqual(c.allocation,'priority')

    def test_reject_invalid_values_and_unsupported_presets(self):
        for update in ({'threshold':float('nan')},{'artifact_crit_rate_cap':-1},{'set_requirement':2},
                       {'reserved_ids':['old:1']},{'weights':{'atk':-1}}):
            d=config();d['demands'][0]['profile'].update(update)
            with self.assertRaises(ValueError):validate(d)
        d=config();d['demands'][0]['id']='../../outside'
        with self.assertRaises(ValueError):validate(d)

    def test_scenario_must_cover_every_demand(self):
        d=config();d['mode']='multi';second=deepcopy(d['demands'][0]);second['id']='second';d['demands'].append(second)
        d['scenarios']=[{'name':'一队','demands':['odetta']}]
        with self.assertRaisesRegex(ValueError,'每个需求'):validate(d)
        d['scenarios'][0]['demands'].append('second')
        self.assertEqual(len(validate(d)['demands']),2)

    def test_reused_scan_includes_confirmed_updates_and_original_stays_immutable(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);scan=root/'old';scan.mkdir()
            raw=[{'index':1,'name':'flower','setKey':'WanderersTroupe','slotKey':'flower','mainStatKey':'hp',
                  'level':0,'rarity':5,'equip_raw':'角色甲已装备','lock':True,'special':False,
                  'substats':[{'key':'critRate_','value':3.1}]}]
            save(scan/'enhancer-artifacts.json',raw);save(scan/'scan-count.json',{'requested':1})
            save(root/'updates.json',{'old:1':{'level':20,'equipped':'角色乙','substats':[{'key':'critRate_','value':14.0}]}})
            output=materialize_snapshot(scan,root/'updates.json',root/'new')
            new,_,coverage=load_scan(output)
            self.assertTrue(coverage['complete']);self.assertEqual(new[0].level,20);self.assertEqual(new[0].equipped,'角色乙')
            self.assertEqual(new[0].id,'new:1');self.assertEqual(load_scan(scan)[0][0].level,0)

    def test_navigation_row_boundary_does_not_reorder_columns(self):
        self.assertEqual(row_major_points([(300,579),(100,581),(100,749),(300,747)]),
                         [(100,581),(300,579),(100,749),(300,747)])

    def test_quick_reject_only_excludes_readable_mismatches(self):
        target={'slot':'goblet','main':'atk_','level':20}
        self.assertTrue(quick_reject({'level':'+0','main':'攻击力'},target))
        self.assertFalse(quick_reject({'level':'?','main':'?'},target))
        self.assertFalse(quick_reject({'level':'6','main':'?'},target))
        self.assertFalse(quick_reject({'level':'+20','main':'攻击力，'},target))


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.backend=Backend(Path(self.temp.name))
        self.server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.backend))
        self.url='http://127.0.0.1:'+str(self.server.server_port)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()

    def request(self,path,data=None,headers=None):
        headers=headers or {}
        req=Request(self.url+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
        with urlopen(req,timeout=5) as r:return json.loads(r.read())

    def test_http_config_save_load_is_real_and_does_not_start_game(self):
        token=self.request('/api/bootstrap')['token']
        with patch('enhancer.ui_server.subprocess.Popen') as process:
            saved=self.request('/api/config',{'config':config()},{'X-Local-Token':token})
            loaded=self.request('/api/config/'+saved['id'])
            self.assertEqual(loaded['demands'][0]['profile']['character'],'奥黛塔');process.assert_not_called()

    def test_foreign_origin_or_missing_token_cannot_start_job(self):
        for headers in ({},{'X-Local-Token':self.backend.token,'Origin':'https://example.com'}):
            with patch('enhancer.ui_server.subprocess.Popen') as process:
                with self.assertRaises(HTTPError) as error:self.request('/api/jobs',{'kind':'scan'},headers)
                self.assertEqual(error.exception.code,403);process.assert_not_called()

    def test_unknown_job_and_config_traversal_rejected(self):
        with self.assertRaises(ValueError):self.backend.config_path('../outside')
        with patch('enhancer.ui_server.subprocess.Popen') as process:
            with self.assertRaises(ValueError):self.backend.submit({'kind':'shell','command':'anything'})
            process.assert_not_called()

    def test_no_borrow_rejects_stale_ownership_before_any_game_input(self):
        d=config();d['equipment']='protected';saved=self.backend.save_config({'config':d})
        with patch.object(self.backend,'snapshot',return_value={'ownership_stale':True}),patch('enhancer.ui_server.subprocess.Popen') as process:
            with self.assertRaisesRegex(ValueError,'归属可能过期'):
                self.backend.submit({'kind':'start','config_id':saved['id'],'fresh':False,'snapshot_id':'old'})
            process.assert_not_called()

    def test_running_job_uses_frozen_config_even_when_user_edits_saved_config(self):
        d=config();saved=self.backend.save_config({'config':d})
        fake=SimpleNamespace(stdout=[],poll=lambda:None)
        with patch.object(self.backend,'snapshot',return_value={'ownership_stale':False,'path':'scan'}),\
             patch('enhancer.ui_server.subprocess.Popen',return_value=fake),patch('enhancer.ui_server.threading.Thread'):
            self.backend.submit({'kind':'preview','config_id':saved['id'],'snapshot_id':'snapshot'})
        frozen=Path(self.backend.job['request']['config'])/'config.json'
        d['demands'][0]['profile']['artifact_crit_rate_cap']=62
        self.backend.save_config({'id':saved['id'],'config':d})
        self.assertEqual(json.loads(frozen.read_text(encoding='utf-8'))['demands'][0]['profile']['artifact_crit_rate_cap'],47)
