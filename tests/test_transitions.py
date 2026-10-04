from dataclasses import replace
from fractions import Fraction as F
import unittest

from enhancer.model import Artifact,Profile,Stat
from enhancer.observations import EnhancementObservation
from enhancer.transitions import enhancement_result
from enhancer.execution import GuardError
from test_planner import ROOT


class TransitionTests(unittest.TestCase):
    def test_title_identity_accepts_observed_glyph_variants_but_not_a_different_name(self):
        from enhancer.text_identity import title_has_name
        self.assertTrue(title_has_name('生之花/流离者的晶泪','流离者的晶淚'))
        self.assertFalse(title_has_name('生之花/流离者的晶泪','止于荣礼的缎彩'))
    def test_elemental_goblet_can_be_located_by_complete_yas_record(self):
        from enhancer.stage import raw_matches
        p=Artifact('a','ObsidianCodex','goblet','pyro_dmg_',0,
                   tuple(Stat(k,F(v)) for k,v in (('critRate_','3.9'),('critDMG_','7.8'),('atk_','5.8'),('eleMas','23'))))
        raw={'name':'cup','level':0,'star':5,'main_stat_name':'火元素伤害加成',
             'pending':[False]*4,'sub_stat':['暴击率+3.9%','暴击伤害+7.8%','攻击力+5.8%','元素精通+23']}
        self.assertTrue(raw_matches(raw,p,'cup'))
        self.assertFalse(raw_matches(dict(raw,main_stat_name='冰元素伤害加成'),p,'cup'))

    def setUp(self):
        self.profile=Profile.load(ROOT/"profiles/木偶.json")
        self.before=Artifact("a","DisenchantmentInDeepShadow","flower","hp",2,
                             tuple(Stat(k,F(v)) for k,v in (("atk_","4.1"),("critRate_","3.9"),("enerRech_","6.5"),("critDMG_","6.2"))))
        self.observation=EnhancementObservation("flower",4,0,5900,10,0,15,0)
        self.text={f"sub_value_{i}":v for i,v in enumerate(("4.1%","3.9%","12.3%","6.2%"))}

    def test_real_plus_two_to_four_changes_exactly_one_stat(self):
        after=enhancement_result(self.before,self.observation,self.text,self.profile)
        self.assertEqual(after.level,4)
        self.assertEqual(after.stats[2].value,F("12.3"))

    def test_roll_up_arrow_is_decoration_but_numeric_garbage_is_rejected(self):
        text=dict(self.text,sub_value_2='12.3%↑',sub_value_0='`、4.1%')
        after=enhancement_result(self.before,self.observation,text,self.profile)
        self.assertEqual(after.stats[2].value,F('12.3'))
        for raw in ('12.3%xyz','12.3 6.5','12.3%→6.5%',''):
            with self.assertRaises(GuardError):
                enhancement_result(self.before,self.observation,dict(text,sub_value_2=raw),self.profile)

    def test_partial_level_progress_cannot_change_substats(self):
        with self.assertRaises(GuardError):
            enhancement_result(self.before,replace(self.observation,level=3),self.text,self.profile)

    def test_stale_substats_at_new_checkpoint_are_rejected(self):
        text=dict(self.text,sub_value_2="6.5%")
        with self.assertRaises(GuardError):
            enhancement_result(self.before,self.observation,text,self.profile)

    def test_two_claimed_rolls_cannot_be_observed_at_one_node(self):
        text=dict(self.text,sub_value_0="9.9%")
        with self.assertRaises(GuardError):
            enhancement_result(self.before,self.observation,text,self.profile)
