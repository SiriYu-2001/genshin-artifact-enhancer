from copy import deepcopy
from dataclasses import replace
from fractions import Fraction as F
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from enhancer.campaign import Campaign,Demand,allocate,compare_claims
from enhancer.model import Profile,Stat,SLOTS,ROLLS
from enhancer import campaign_runtime as runtime
from enhancer.batch import save
from enhancer.campaign_report import write_report
from test_planner import piece,ROOT


def profile(character):
    p=Profile.load(ROOT/'profiles/木偶.json')
    p.data=deepcopy(p.data)
    p.data.update(character=character,character_aliases=[character],weights={'enerRech_':1},artifact_crit_rate_cap=100)
    return p


def artifact(identifier,slot,strength,owner='',level=20):
    a=piece(identifier,slot,level=level)
    return replace(a,equipped=owner,stats=tuple(replace(s,value=ROLLS[s.key][1]*strength)
                                              if s.key=='enerRech_' else s for s in a.stats))


class AllocationTests(unittest.TestCase):
    def pool(self):
        return [artifact(f'{tier}-{slot}',slot,strength) for tier,strength in [('high',5),('mid',3),('low',1)] for slot in SLOTS]

    def campaign(self,equipment='borrow',allocation='priority'):
        return Campaign([Demand(k,profile(k)) for k in 'ABC'],equipment,allocation,None,[])

    def test_each_demand_applies_its_own_configured_threshold(self):
        from enhancer.campaign import plan
        from enhancer.ui_config import compile_config
        from enhancer.ui_server import public_report

        low, high = profile('木偶'), profile('尼可')
        low.data['threshold'] = .05
        high.data['threshold'] = .5
        inventory = [
            artifact(f'{who}-{slot}', slot, 3, who)
            for who in ('木偶', '尼可') for slot in SLOTS
        ]
        inventory += [
            artifact('wood-candidate', 'flower', 2, '木偶', level=16),
            artifact('nicole-candidate', 'flower', 2, '尼可', level=16),
        ]
        names = {a.id: a.id for a in inventory}
        config = {'name':'不同强化阈值', 'mode':'multi', 'equipment':'protected',
                  'allocation':'priority', 'demands':[{'id':'wood','profile':low.data},
                                                     {'id':'nicole','profile':high.data}]}
        with TemporaryDirectory() as temp:
            compile_config(config, Path(temp))
            campaign = Campaign.load(Path(temp)/'campaign.json')
            result = plan(inventory, campaign, names)
        wood, nicole = result['demands']
        self.assertEqual([r['decisions'][0]['probability_lower'] for r in (wood,nicole)], [.125,.125])
        self.assertEqual([r['decisions'][0]['action'] for r in (wood,nicole)], ['enhance','retain'])
        self.assertEqual(result['next']['demand_id'], 'wood')
        self.assertEqual(result['next']['profile']['threshold'], .05)
        self.assertEqual([(r['threshold'],r['eligible_count']) for r in public_report(result)['demands']],
                         [(.05,1),(.5,0)])

    def test_chain_reallocation_is_disjoint_and_releases_previous_claims(self):
        inventory=self.pool(); c=self.campaign(); names={a.id:a.id for a in inventory}
        before=allocate(inventory,c,names)
        for row,tier in zip(before['demands'],['high','mid','low']):
            self.assertEqual({a['id'] for a in row['items']},{f'{tier}-{s}' for s in SLOTS})
        new=artifact('new-flower','flower',6);names[new.id]=new.id
        after=allocate(inventory+[new],c,names)
        self.assertEqual(after['claims']['new-flower'],['A'])
        self.assertEqual(after['claims']['high-flower'],['B'])
        self.assertEqual(after['claims']['mid-flower'],['C'])
        self.assertNotIn('low-flower',after['claims'])
        self.assertFalse(after['conflicts'])
        self.assertEqual(len(compare_claims(before['claims'],after['claims'])),4)

    def test_protected_equipment_alone_does_not_protect_shared_idle_items(self):
        pool=[artifact(f'{who}-{s}',s,2,who) for who in 'AB' for s in SLOTS]
        pool += [artifact(f'idle-{s}',s,5) for s in SLOTS]
        names={a.id:a.id for a in pool}
        c=Campaign([Demand(k,profile(k)) for k in 'AB'],'protected','independent',None,[])
        independent=allocate(pool,c,names)
        self.assertEqual(len(independent['conflicts']),5)
        c.allocation='priority'
        constrained=allocate(pool,c,names)
        self.assertFalse(constrained['conflicts'])
        self.assertEqual({a['equipped'] for a in constrained['demands'][1]['items']},{'B'})
        c.reserved_ids=[f'idle-{s}' for s in SLOTS]
        protected=allocate(pool,c,names)
        self.assertEqual({a['equipped'] for a in protected['demands'][0]['items']},{'A'})

    def test_same_character_alternatives_can_share_and_explicit_scenarios_override(self):
        pool=self.pool(); names={a.id:a.id for a in pool}
        c=Campaign([Demand('a',profile('A')),Demand('alternate',profile('A')),Demand('b',profile('B'))],
                   'borrow','priority',None,[])
        result=allocate(pool,c,names)
        self.assertEqual(result['claims']['high-flower'],['a','alternate'])
        self.assertFalse(result['conflicts'])
        c.scenarios=[['a','alternate','b']]
        result=allocate(pool,c,names)
        self.assertTrue(all(len(owners)==1 for owners in result['claims'].values()))

    def test_scenarios_do_not_create_transitive_conflicts(self):
        c=self.campaign();c.scenarios=[['A','B'],['B','C']]
        self.assertTrue(c.conflicts('A','B'))
        self.assertTrue(c.conflicts('B','C'))
        self.assertFalse(c.conflicts('A','C'))
        pool=self.pool(); result=allocate(pool,c,{a.id:a.id for a in pool})
        self.assertEqual(result['claims']['high-flower'],['A','C'])

    def test_unlisted_characters_yield_in_borrow_mode_and_reordering_changes_winner(self):
        pool=self.pool()
        pool=[replace(a,equipped='unlisted') if a.id.startswith('high-') else a for a in pool]
        c=self.campaign();names={a.id:a.id for a in pool}
        result=allocate(pool,c,names)
        self.assertEqual(result['claims']['high-flower'],['A'])
        self.assertTrue(all(t['outside_list'] for t in result['transfers']))
        self.assertEqual({t['to_demand'] for t in result['transfers']},{'A'})
        c.demands.reverse()
        result=allocate(pool,c,names)
        self.assertEqual(result['claims']['high-flower'],['C'])
        c.equipment='protected'
        result=allocate(pool,c,names)
        self.assertNotIn('high-flower',result['claims'])
        self.assertFalse(result['transfers'])

    def test_two_teams_in_one_scenario_cannot_share_and_snapshot_roundtrips(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp);save(path/'a.json',profile('A').data);save(path/'b.json',profile('B').data)
            data={'version':1,'equipment':'borrow','allocation':'priority',
                  'demands':[{'id':'a','profile':'a.json'},{'id':'b','profile':'b.json'}],
                  'teams':{'first':['a'],'second':['b']},
                  'scenarios':[{'name':'both','teams':['first','second']}]}
            save(path/'config.json',data)
            c=Campaign.load(path/'config.json')
            self.assertTrue(c.conflicts('a','b'))
            copy=Campaign.load(c.snapshot(path/'snapshot'))
            self.assertEqual(copy.scenarios,[['a','b']])
            self.assertEqual(copy.scenario_names,['both'])
            data['scenarios']=[{'name':'one','teams':['first']},{'name':'two','teams':['second']}]
            save(path/'config.json',data);c=Campaign.load(path/'config.json')
            self.assertFalse(c.conflicts('a','b'))

    def test_unbuildable_lower_priority_is_reported_not_silently_successful(self):
        pool=[artifact(s,s,3) for s in SLOTS]
        result=allocate(pool,self.campaign(),{a.id:a.id for a in pool})
        self.assertEqual(result['demands'][1]['status'],'missing_mature_build')
        self.assertIsNone(result['demands'][1]['score'])
        self.assertFalse(result['demands'][1]['items'])

    def test_old_scan_reservations_cannot_silently_disappear(self):
        pool=self.pool();c=self.campaign();c.reserved_ids=['old-scan:1']
        with self.assertRaisesRegex(ValueError,'Reservation IDs'):
            allocate(pool,c,{a.id:a.id for a in pool})

    def test_readable_final_assignment_contains_five_pieces_and_unlisted_transfer(self):
        pool=[replace(a,equipped='outsider') if a.id=='high-flower' else a for a in self.pool()]
        result=allocate(pool,self.campaign(),{a.id:a.id for a in pool})
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'plan.md';write_report(result,path)
            text=path.read_text(encoding='utf-8')
        self.assertIn('名单外',text)
        self.assertIn('outsider',text)
        self.assertIn('high-flower',text)
        self.assertIn('high-circlet',text)
        self.assertIn('没有执行强化或换装',text)

    def test_config_rejects_missing_scenario_demand_and_unsafe_id(self):
        with TemporaryDirectory() as tmp:
            p=Path(tmp);save(p/'profile.json',profile('A').data)
            data={'version':1,'demands':[{'id':'a','profile':'profile.json'},{'id':'b','profile':'profile.json'}],
                  'scenarios':[['a']]}
            save(p/'campaign.json',data)
            with self.assertRaises(ValueError):Campaign.load(p/'campaign.json')
            data['scenarios']=[['a','b']];data['demands'][0]['id']='../unsafe'
            save(p/'campaign.json',data)
            with self.assertRaises(ValueError):Campaign.load(p/'campaign.json')


class CampaignRuntimeTests(unittest.TestCase):
    def test_pending_from_another_demand_cannot_be_overwritten_or_executed(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'runtime').mkdir();run=root/'job';run.mkdir()
            job={'demand_id':'a','target_id':'scan:1','profile':str(root/'a.json'),
                 'ownership':'borrow','run_directory':str(run),'status':'running'}
            state={'directory':str(root),'scan_directory':'scan','jobs':[job],'runs':[str(run)]}
            save(run/'pending.json',{'target_id':'scan:1'})
            save(root/'runtime/active-run.json',{'target_id':'scan:1','profile':str(root/'b.json')})
            with patch.object(runtime,'ROOT',root),patch.object(runtime.subprocess,'run') as child:
                with self.assertRaisesRegex(RuntimeError,'different job'):
                    runtime.execute_current(state,{'scan:1':'artifact'})
                child.assert_not_called()
            self.assertTrue((run/'pending.json').exists())
            self.assertEqual(runtime.read(root/'runtime/active-run.json')['profile'],str(root/'b.json'))

    def test_one_scan_shared_updates_cross_demand_handoff_and_priority_revisit(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'runtime').mkdir();scan=root/'scan';scan.mkdir()
            c=Campaign([Demand('a',profile('A')),Demand('b',profile('B'))],'borrow','priority',None,[])
            c.demands[1].profile.data['threshold']=.5
            config=c.snapshot(root/'config')
            original=[artifact('scan:1','flower',1,level=0),artifact('scan:2','plume',1,level=0)]
            names={a.id:'target' for a in original}
            calls=[];observed=[]
            def plan(pool,campaign,*args,**kwargs):
                levels=tuple(a.level for a in pool);observed.append(levels)
                sequence={(0,0):('a','scan:1'),(4,0):('b','scan:1'),(20,0):('a','scan:2')}
                chosen=sequence.get(levels)
                selected=None if chosen is None else {'demand_id':chosen[0],
                    'candidate':{'id':chosen[1],'probability_lower':.5},
                    'profile':next(d.profile.data for d in campaign.demands if d.id==chosen[0])}
                return {'claims':{},'next':selected,'demands':[],'blocked_demands':[]}
            def child(*args,**kwargs):
                active=runtime.read(root/'runtime/active-run.json')
                calls.append((Path(active['profile']).parent.name,active['target_id'],
                              Profile.load(active['profile']).threshold))
                level=4 if len(calls)==1 else 20
                a=next(a for a in original if a.id==active['target_id'])
                directory=Path(active['run_directory'])
                save(directory/'target-state.json',{'index':int(a.id.split(':')[-1]),'level':level,
                     'substats':[{'key':s.key,'value':float(s.value),'exact':True} for s in a.stats]})
                save(directory/'decision-final.json',{'action':'retain' if level==4 else 'complete'})
                return SimpleNamespace(returncode=0)
            class Nav:
                def observe(self,*args):return {'text':{'title':'target'}}
                def _navigation_click(self,*args):pass
                def ensure_bag(self):pass
                def yas(self):return self
                def close(self):pass
            with patch.object(runtime,'ROOT',root),patch('enhancer.__main__.ensure_controller'),\
                 patch('enhancer.workflow.fresh_scan',return_value=scan) as scanner,\
                 patch.object(runtime,'load_scan',return_value=(original,names,{'complete':True})),\
                 patch.object(runtime,'allocate',return_value={'claims':{},'demands':[]}),\
                 patch.object(runtime,'plan',side_effect=plan),patch.object(runtime,'report'),\
                 patch.object(runtime.subprocess,'run',side_effect=child),patch.object(runtime,'Navigation',Nav):
                runtime.start(config)
            self.assertEqual(scanner.call_count,1)
            self.assertEqual(observed,[(0,0),(4,0),(20,0),(20,20)])
            state=runtime.read(root/'runtime/active-batch.json')
            self.assertEqual([j['demand_id'] for j in state['jobs']],['a','b','a'])
            self.assertEqual(state['status'],'finished')
            self.assertEqual([call[2] for call in calls],[F('.05'),F('.5'),F('.05')])
            self.assertEqual(runtime.read(Path(state['directory'])/'inventory-updates.json')['scan:1']['level'],20)
