import unittest
from dataclasses import replace
from enhancer.model import Stat
from enhancer.batch import apply_updates
from test_planner import piece
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from types import SimpleNamespace
import json
from enhancer import batch

class BatchTests(unittest.TestCase):
    def test_replan_cache_only_reuses_unchanged_candidate_and_mature_frontier(self):
        from enhancer.model import Profile,SLOTS
        p=Profile.load(Path(__file__).resolve().parents[1]/'profiles/木偶.json')
        p.data['artifact_crit_rate_cap']=200
        mature=[piece(s,s,hits=2) for s in SLOTS]
        candidates=[piece('a',level=0),piece('b',level=0)]
        pool=mature+candidates;names={a.id:a.id for a in pool};cache={}
        def evaluate(a,*args):return {'id':a.id,'action':'enhance','probability_lower':.5}
        with patch.object(batch,'evaluate',side_effect=evaluate) as calc:
            batch.replan(pool,p,names,cache);self.assertEqual(calc.call_count,2)
            batch.replan(pool,p,names,cache);self.assertEqual(calc.call_count,2)
            changed=mature+[replace(candidates[0],level=4),candidates[1]]
            batch.replan(changed,p,names,cache);self.assertEqual(calc.call_count,3)
            better=piece('strong','flower',hits=6);names['strong']='strong'
            batch.replan(changed+[better],p,names,cache);self.assertEqual(calc.call_count,5)
            p.data['weights']['enerRech_']=.5
            batch.replan(changed+[better],p,names,cache);self.assertEqual(calc.call_count,7)

    def test_restart_reconciles_pending_child_before_replanning(self):
        with TemporaryDirectory() as temp:
            root=Path(temp); runtime=root/'runtime'; runtime.mkdir()
            directory=runtime/'batch'; directory.mkdir(); run=directory/'item-1'; run.mkdir()
            scan=root/'scan'; scan.mkdir(); (scan/'enhancer-artifacts.json').write_text('[]')
            artifact=piece('scan:1',level=0)
            profile=Path(__file__).resolve().parents[1]/'profiles/木偶.json'
            batch.save(runtime/'active-batch.json',{'directory':str(directory),'scan_directory':str(scan),
                       'profile':str(profile),'ownership':'borrow','runs':[str(run)],'status':'needs-attention','current':'scan:1'})
            batch.save(runtime/'active-run.json',{'run_directory':str(run),'target_id':'scan:1','profile':str(profile),'ownership':'borrow'})
            batch.save(directory/'inventory-updates.json',{})
            batch.save(run/'pending.json',{'target_id':'scan:1','id':'already-clicked'})
            def reconcile(*args,**kwargs):
                self.assertTrue((run/'pending.json').exists())
                batch.save(run/'target-state.json',{'index':1,'level':20,
                           'substats':[{'key':s.key,'value':float(s.value)} for s in artifact.stats]})
                (run/'pending.json').unlink()
                return SimpleNamespace(returncode=0)
            def plan(pool,*args,**kwargs):
                self.assertEqual(pool[0].level,20)
                return []
            class Nav:
                def observe(self,*args):return {'text':{'title':'one'}}
                def _navigation_click(self,*args):pass
                def ensure_bag(self):pass
                def yas(self):return self
                def close(self):pass
            with patch.object(batch,'ROOT',root),patch.object(batch,'Navigation',Nav),\
                 patch.object(batch,'load_scan',return_value=([artifact],{'scan:1':'one'},{'complete':True})),\
                 patch.object(batch,'replan',side_effect=plan),patch.object(batch,'summaries',return_value={}),\
                 patch.object(batch,'write_results'),patch.object(batch.subprocess,'run',side_effect=reconcile) as child:
                batch.main()
            self.assertEqual(child.call_count,1)
            self.assertEqual(json.loads((runtime/'active-batch.json').read_text(encoding='utf-8'))['status'],'finished')

    def test_special_guarantees_only_pruned_when_no_possible_completion_wins(self):
        from enhancer.model import Profile
        profile=Profile.load(Path(__file__).resolve().parents[1]/'profiles/木偶.json')
        a=replace(piece('special',level=12),special='defined')
        for upper,expected in [(0,'retain'),(.01,'reread')]:
            responses=[{'id':a.id,'action':'reread'},
                       {'id':a.id,'action':'retain','probability_lower':0,'probability_upper':upper}]
            with patch.object(batch,'CappedInventory'),patch.object(batch,'evaluate',side_effect=responses):
                decisions=batch.replan([a],profile,{a.id:'special'})
            self.assertEqual(decisions[0]['action'],expected)

    def test_yas_float_noise_is_not_an_unrecorded_roll(self):
        self.assertEqual(Stat.from_dict({'key':'critRate_','value':3.5000000000000004}),
                         Stat.from_dict({'key':'critRate_','value':3.5}))

    def test_updates_preserve_other_items_and_owner(self):
        a=replace(piece('a','flower'),equipped='桑多涅')
        b=piece('b','plume')
        result=apply_updates([a,b],{'a':{'level':16,'substats':[{'key':s.key,'value':float(s.value),'exact':s.exact} for s in a.stats]}})
        self.assertEqual(result[0].level,16)
        self.assertEqual(result[0].equipped,'桑多涅')
        self.assertEqual(result[1],b)

    def test_batch_runs_every_candidate_and_replans_without_external_decisions(self):
        with TemporaryDirectory() as temp:
            root=Path(temp); runtime=root/'runtime'; runtime.mkdir()
            directory=runtime/'batch'; directory.mkdir()
            scan=root/'scan'; scan.mkdir(); (scan/'enhancer-artifacts.json').write_text('[]')
            original=[piece('scan:1',level=0),piece('scan:2',level=0)]
            names={'scan:1':'one','scan:2':'two'}
            profile=Path(__file__).resolve().parents[1]/'profiles/木偶.json'
            batch.save(runtime/'active-batch.json',{'directory':str(directory),'scan_directory':str(scan),
                       'profile':str(profile),'ownership':'borrow','runs':[],'status':'prepared'})
            batch.save(directory/'inventory-updates.json',{})
            calls=[]
            def plan(pool,p,names,**kwargs):
                return [{'id':a.id,'name':names[a.id],'level':a.level,'action':'enhance','probability_lower':.5}
                        for a in pool if a.level<20]
            def execute(*args,**kwargs):
                active=json.loads((runtime/'active-run.json').read_text(encoding='utf-8'))
                calls.append(active['target_id'])
                artifact=next(a for a in original if a.id==active['target_id'])
                batch.save(Path(active['run_directory'])/'target-state.json',{'index':int(artifact.id.split(':')[-1]),
                           'level':20,'substats':[{'key':s.key,'value':float(s.value)} for s in artifact.stats]})
                return SimpleNamespace(returncode=0)
            class Nav:
                def observe(self,*args):return {'text':{'title':'one two'}}
                def _navigation_click(self,*args):pass
                def ensure_bag(self):pass
                def yas(self):return self
                def close(self):pass
            with patch.object(batch,'ROOT',root),patch.object(batch,'Navigation',Nav),\
                 patch.object(batch,'load_scan',return_value=(original,names,{'complete':True})),\
                 patch.object(batch,'replan',side_effect=plan),patch.object(batch,'summaries',return_value={}),\
                 patch.object(batch,'write_results'),patch.object(batch.subprocess,'run',side_effect=execute):
                batch.main()
            self.assertEqual(calls,['scan:1','scan:2'])
            self.assertEqual(json.loads((runtime/'active-batch.json').read_text(encoding='utf-8'))['status'],'finished')
