from copy import deepcopy
from dataclasses import replace
from fractions import Fraction as F
import unittest

from enhancer.good_enhancement import wire,validate_result,before_from_payload
from enhancer.model import Profile,ROLLS
from test_planner import ROOT,piece


class GoodEnhancementTests(unittest.TestCase):
    def setUp(self):
        self.profile=Profile.load(ROOT/'profiles/木偶.json');self.before=piece('a',level=16)
        stats=list(self.before.stats);stats[0]=replace(stats[0],value=stats[0].value+ROLLS[stats[0].key][0])
        self.after=replace(self.before,level=20,stats=tuple(stats))
        self.payload={'operationId':'one','action':'step','name':'fixture','artifact':wire(self.before)}
        self.result={'request':self.payload,'confirmed':True,'before':{'level':16,'stats':{s.key:float(s.value) for s in self.before.stats},'materialCount':0,'exp':10},
                     'after':{'level':20,'stats':{s.key:float(s.value) for s in self.after.stats},'materialCount':0,'exp':None},'artifact':wire(self.after),
                     'materialProof':{'sameFrame':True,'selection':'restricted_game_stage_add','emptyBefore':True,'rarityLimit':4,'fiveStarQuickAddDisabled':True,'stageAddObserved':True,'count':1,'visibleRarities':[4]}}

    def test_valid_native_receipt_preserves_original_scoring(self):
        self.assertEqual(wire(validate_result(self.before,self.profile,self.payload,self.result)),wire(self.after))

    def test_native_serde_default_fields_do_not_break_identity(self):
        result=deepcopy(self.result)
        result['request']['artifact']['astralMark']=False
        result['request']['artifact'].pop('unactivatedSubstats',None)
        self.assertEqual(wire(validate_result(self.before,self.profile,self.payload,result)),wire(self.after))

    def test_resume_after_local_state_write_does_not_replay_or_reject_old_before(self):
        recovered=before_from_payload(self.after,self.payload)
        self.assertEqual(wire(recovered),wire(self.before))
        self.assertEqual(wire(validate_result(recovered,self.profile,self.payload,self.result)),wire(self.after))

    def test_five_star_or_unknown_material_never_accepted(self):
        for value in (5,0,None):
            r=deepcopy(self.result);r['materialProof']['visibleRarities']=[value]
            with self.assertRaises(Exception):validate_result(self.before,self.profile,self.payload,r)

    def test_wrong_request_missing_progress_and_impossible_roll_rejected(self):
        variants=[]
        r=deepcopy(self.result);r['request']['operationId']='another';variants.append(r)
        r=deepcopy(self.result);r['after']['level']=16;r['after']['exp']=10;variants.append(r)
        r=deepcopy(self.result);r['after']['stats']['critRate_']=99;variants.append(r)
        r=deepcopy(self.result);r['materialProof']['fiveStarQuickAddDisabled']=False;variants.append(r)
        for r in variants:
            with self.assertRaises(Exception):validate_result(self.before,self.profile,self.payload,r)

if __name__=='__main__':unittest.main()
