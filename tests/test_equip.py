from types import SimpleNamespace
from copy import deepcopy
from unittest.mock import Mock,patch
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import json

from enhancer.equip import parse_panel,EquipDriver,borrow_dialog,loadout_allows_borrow
from enhancer.loadouts import fingerprint


class EquipTests(unittest.TestCase):
    def test_one_wrong_glyph_in_short_character_name_requests_exact_reread(self):
        wrong={'character':'冰元素【英多涅·','artifact_menu':'圣遗物','replace':'替换'}
        right=dict(wrong,character='冰元素／桑多涅')
        with TemporaryDirectory() as directory,patch('enhancer.equip.time.sleep'):
            driver=EquipDriver.__new__(EquipDriver)
            driver.directory=Path(directory);driver.nav=SimpleNamespace(directory=directory)
            driver.focus=Mock();driver.click=Mock();driver.key=Mock()
            driver.observe=Mock(side_effect=[wrong,right,right,{}])
            driver.enter(['木偶','桑多涅'])
            self.assertFalse(any(c.args[0]=='equip_next' for c in driver.click.call_args_list))
            self.assertEqual(json.loads((Path(directory)/'character.json').read_text(encoding='utf-8'))['name'],'桑多涅')

    def test_persistent_similar_character_is_never_accepted_as_target(self):
        wrong={'character':'冰元素【英多涅·','artifact_menu':'圣遗物','replace':'替换'}
        other=dict(wrong,character='火元素／安柏')
        with TemporaryDirectory() as directory,patch('enhancer.equip.time.sleep'):
            driver=EquipDriver.__new__(EquipDriver)
            driver.directory=Path(directory);driver.nav=SimpleNamespace(directory=directory)
            driver.focus=Mock();driver.click=Mock();driver.key=Mock()
            driver.observe=Mock(side_effect=[wrong,wrong,wrong,wrong,other,wrong,wrong,wrong,wrong])
            with self.assertRaisesRegex(RuntimeError,'Target character not found'):driver.enter(['桑多涅'])
            self.assertFalse(any(c.args[0]=='equip_menu' for c in driver.click.call_args_list))

    def test_borrow_policy_defaults_protected_and_reads_legacy_allocation(self):
        self.assertFalse(loadout_allows_borrow({}))
        self.assertTrue(loadout_allows_borrow({'equipment_policy':'borrow'}))
        self.assertFalse(loadout_allows_borrow({'equipment_policy':'protected'}))
        with self.assertRaises(ValueError):loadout_allows_borrow({'equipment_policy':'invalid'})
        with TemporaryDirectory() as directory:
            path=Path(directory)/'allocation.json'
            path.write_text(json.dumps({'equipment':'borrow'}),encoding='utf-8')
            self.assertTrue(loadout_allows_borrow({'source_allocation':str(path)}))
            self.assertFalse(loadout_allows_borrow({'source_allocation':str(path),'equipment_policy':'protected'}))

    def test_borrow_dialog_does_not_depend_on_donor_spelling(self):
        self.assertTrue(borrow_dialog({'confirm':'确认','cancel':'取消','body':'宗室之花已被迪希装备，是否更换？'}))
        self.assertFalse(borrow_dialog({'confirm':'确认','cancel':'取消','body':'是否消耗所选素材？'}))
        self.assertFalse(borrow_dialog({'confirm':'确认','cancel':'','body':'已装备，是否更换？'}))

    def test_authorized_borrow_confirms_once_and_protected_borrow_cancels(self):
        for allowed in (True,False):
            with self.subTest(allowed=allowed), TemporaryDirectory() as directory:
                actual=parse_panel(self.panel(),'flower',5)
                ref={'attributes':actual,'fingerprint':fingerprint(actual)}
                driver=EquipDriver.__new__(EquipDriver)
                driver.directory=Path(directory);driver.nav=SimpleNamespace(directory=directory)
                driver.allow_borrow=allowed;driver.aliases=['奥黛塔']
                driver.find=Mock(return_value=(actual,{'owner':'迪希雅已装备','action':'替换'}))
                driver.read_actionable=Mock(side_effect=lambda ref,match:match)
                driver.click=Mock()
                driver.observe=Mock(return_value={'confirm':'确认','cancel':'取消','body':'宗室之花已被迪希装备，是否更换？'})
                driver.matches=Mock(return_value=(actual,{'owner':'奥黛塔已装备','action':'卸下'}))
                driver.dismiss_borrow_dialog=Mock(return_value={'after':'cancelled'})
                if allowed:
                    self.assertEqual(driver.equip(ref)['status'],'equipped')
                    self.assertEqual(sum(c.args[0]=='equip_confirm' for c in driver.click.call_args_list),1)
                    driver.dismiss_borrow_dialog.assert_not_called()
                else:
                    with self.assertRaisesRegex(RuntimeError,'Borrowing is disabled'):driver.equip(ref)
                    self.assertFalse(any(c.args[0]=='equip_confirm' for c in driver.click.call_args_list))
                    driver.dismiss_borrow_dialog.assert_called_once()
                self.assertFalse((Path(directory)/'equip-pending.json').exists())

    def test_action_noise_requires_a_fresh_complete_match(self):
        noisy=({}, {'action':'3。卸下'})
        valid=({}, {'action':'卸下'})
        driver=SimpleNamespace(matches=Mock(return_value=valid))
        self.assertEqual(EquipDriver.read_actionable(driver,{},noisy),valid)
        driver.matches.assert_called_once()

    def test_character_search_recovers_lost_page_without_clicking_world(self):
        start={'character':'冰元素／桑多涅','artifact_menu':'圣遗物','replace':'替换'}
        target=dict(start,character='冰元素／奥黛塔')
        world={'character':'N','artifact_menu':'','replace':''}
        with TemporaryDirectory() as directory, patch('enhancer.equip.time.sleep'):
            driver=EquipDriver.__new__(EquipDriver)
            driver.directory=Path(directory);driver.nav=SimpleNamespace(directory=directory)
            driver.focus=Mock();driver.click=Mock();driver.key=Mock()
            driver.observe=Mock(side_effect=[start,world,world,world,world,target,target,{}])
            driver.enter(['奥黛塔'])
            self.assertEqual(sum(c.args[0]=='equip_next' for c in driver.click.call_args_list),1)
            driver.key.assert_called_once_with('character')

    def test_unchanged_character_after_click_is_not_a_complete_roster_cycle(self):
        start={'character':'冰元素／桑多涅','artifact_menu':'圣遗物','replace':'替换'}
        target=dict(start,character='冰元素／奥黛塔')
        with TemporaryDirectory() as directory, patch('enhancer.equip.time.sleep'):
            driver=EquipDriver.__new__(EquipDriver)
            driver.directory=Path(directory);driver.nav=SimpleNamespace(directory=directory)
            driver.focus=Mock();driver.click=Mock();driver.key=Mock()
            driver.observe=Mock(side_effect=[start,start,target,target,{}])
            driver.enter(['奥黛塔'])
            self.assertEqual(sum(c.args[0]=='equip_next' for c in driver.click.call_args_list),2)
            driver.key.assert_not_called()

    def test_partial_numeric_match_is_retried_but_never_accepted(self):
        expected=parse_panel(self.panel(),'flower',5)
        wrong=deepcopy(expected);wrong['substats'][0]['value']=10.0
        ref={'attributes':expected,'fingerprint':fingerprint(expected)}
        reader=Mock(side_effect=[(wrong,{}),(expected,{})])
        self.assertEqual(EquipDriver.matches(SimpleNamespace(read=reader),ref)[0],expected)
        self.assertEqual(reader.call_count,2)
        self.assertIsNone(EquipDriver.matches(SimpleNamespace(read=Mock(return_value=(wrong,{}))),ref))

    def panel(self):
        return {'level':'+20','main':'生命值','main_value':'irrelevant star icons',
                'sub_0':'·攻击力+10.5%','sub_1':'暴击率+16.7%',
                'sub_2':'暴击伤害+7.0%','sub_3':'元素精通+21',
                'set':'流浪大地的乐团：（0）'}

    def test_complete_attributes_parse_and_flat_main_is_slot_specific(self):
        data=parse_panel(self.panel(),'flower',5)
        self.assertEqual(data['main'],'hp')
        self.assertEqual(data['set_key'],'WanderersTroupe')
        self.assertEqual(data['substats'][0]['key'],'atk_')
        p=dict(self.panel(),main='攻击力')
        self.assertEqual(parse_panel(p,'plume',5)['main'],'atk')
        self.assertEqual(parse_panel(p,'sands',5)['main'],'atk_')

    def test_missing_or_wrong_unit_and_missing_set_are_rejected(self):
        for changes in ({'sub_1':'暴击率+16.7'},{'sub_2':''},{'set':''},{'main':''}):
            with self.assertRaises(ValueError):parse_panel(dict(self.panel(),**changes),'flower',5)

    def test_live_selection_panel_decorations_do_not_change_attributes(self):
        noisy=dict(self.panel(),main='生命值，',sub_1='·暴击率+16.7.%',sub_3='·元素精通+21：')
        self.assertEqual(parse_panel(noisy,'flower',5),parse_panel(self.panel(),'flower',5))

    def test_owner_and_button_must_both_confirm_equipped(self):
        driver=SimpleNamespace(aliases=['奥黛塔'])
        self.assertTrue(EquipDriver.owned(driver,{'owner':'奥黛塔已装备','action':'卸下'}))
        self.assertFalse(EquipDriver.owned(driver,{'owner':'甘雨已装备','action':'卸下'}))
        self.assertFalse(EquipDriver.owned(driver,{'owner':'奥黛塔已装备','action':'替换'}))

    def test_owner_retry_requires_a_later_exact_read(self):
        state=SimpleNamespace(aliases=['奥黛塔'])
        noisy=({}, {'owner':'奥黛塔已装备米','action':'卸下'})
        exact=({}, {'owner':'奥黛塔已装备','action':'卸下'})
        driver=SimpleNamespace(owned=lambda text:EquipDriver.owned(state,text),
                               matches=Mock(return_value=exact))
        self.assertEqual(EquipDriver.confirm_owned(driver,{},noisy),exact)
        driver.matches=Mock(return_value=noisy)
        self.assertIsNone(EquipDriver.confirm_owned(driver,{},noisy))

    def test_character_recognition_requires_element_heading(self):
        driver=SimpleNamespace()
        self.assertEqual(EquipDriver.character_name(driver,{'character':'冰元素／奧黛塔'}),'奥黛塔')
        self.assertEqual(EquipDriver.character_name(driver,{'character':'冰元素1奥黛塔'}),'奥黛塔')
        self.assertIsNone(EquipDriver.character_name(driver,{'character':'奥黛塔'}))

    def test_recorded_character_heading_noise_and_wrong_names(self):
        driver=SimpleNamespace()
        for text,expected in [('冰元素／桑多涅','桑多涅'),('岩元素／兹白°','兹白'),
                              ('冰元素／奥黛塔”。','奥黛塔'),("'冰元素／奥黛塔",'奥黛塔'),
                              ('火元素！玛薇卡','玛薇卡'),('雷元素/伊涅芙','伊涅芙'),
                              ('冰元素／桑多涅0','桑多涅0'),('冰元素／桑多涅甲','桑多涅甲')]:
            self.assertEqual(EquipDriver.character_name(driver,{'character':text}),expected)
