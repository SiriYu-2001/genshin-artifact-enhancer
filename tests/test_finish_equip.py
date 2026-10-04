import unittest
from enhancer.finish_equip import select_final


def row(identifier,character,items,status='ready'):
    return {'id':identifier,'name':identifier,'character':character,'status':status,
            'items':[{'id':str(x)} for x in items],
            'effective_profile':{'character_aliases':[character]}}


class FinishEquipTests(unittest.TestCase):
    def test_unused_alternative_releases_gear_to_next_character(self):
        from dataclasses import replace
        from enhancer.campaign import Campaign,Demand,allocate
        from enhancer.finish_equip import allocate_active
        from enhancer.model import SLOTS,ROLLS
        from test_campaign import profile,artifact
        a=profile('A');alternate=profile('A');alternate.data['weights']={'def':1}
        b=profile('B');b.data['weights']={'def':1}
        pool=[artifact(f'er-{s}',s,5) for s in SLOTS]
        for s in SLOTS:
            x=artifact(f'def-{s}',s,1)
            pool.append(replace(x,stats=tuple(replace(t,value=ROLLS['def'][1]*5) if t.key=='def' else t for t in x.stats)))
            pool.append(artifact(f'weak-{s}',s,1))
        c=Campaign([Demand('a',a),Demand('alternate',alternate),Demand('b',b)],'borrow','priority',None,[])
        names={x.id:x.id for x in pool}
        before=allocate(pool,c,names)
        after,skipped=allocate_active(pool,c,names)
        self.assertEqual([r['id'] for r in after['demands']],['a','b'])
        self.assertGreater(after['demands'][-1]['score'],before['demands'][-1]['score'])
        self.assertFalse(after['conflicts'])

    def test_priority_order_and_one_loadout_per_character(self):
        plan={'demands':[row('a','A',range(5)),row('a-alt','A',range(5,10)),row('b','B',range(10,15))], 'scenarios':None}
        selected,skipped=select_final(plan)
        self.assertEqual([r['id'] for r in selected],['a','b'])
        self.assertEqual(skipped[0]['reason'],'higher_priority_loadout_for_same_character')

    def test_unbuildable_first_choice_can_fall_back_to_complete_alternative(self):
        selected,skipped=select_final({'demands':[row('a','A',[],'missing_mature_build'),row('alt','A',range(5))]})
        self.assertEqual([r['id'] for r in selected],['alt'])
        self.assertEqual(skipped[0]['reason'],'no_complete_build')

    def test_lower_priority_cannot_steal_items_and_multiple_scenes_need_selection(self):
        plan={'demands':[row('a','A',range(5)),row('b','B',range(5))],'scenarios':[['a'],['b']]}
        with self.assertRaises(ValueError):select_final(plan)
        self.assertEqual([r['id'] for r in select_final(plan,1)[0]],['b'])
        plan['scenarios']=None
        selected,skipped=select_final(plan)
        self.assertEqual(len(selected),1)
        self.assertEqual(skipped[0]['reason'],'artifact_conflict_with_higher_priority')
