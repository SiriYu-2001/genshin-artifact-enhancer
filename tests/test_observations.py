import unittest
from enhancer.execution import GuardError
from enhancer.observations import EnhancementObservation, integer


class ObservationTests(unittest.TestCase):
    def test_impossible_exp_is_unknown_not_zero_or_permission_to_repeat(self):
        from enhancer.execution import Progress,compare_progress
        text=dict(confirm='强化',level='+10',material_count='装备强化消耗(0/15)',exp='8425/1050')
        parsed=EnhancementObservation.parse(text)
        self.assertIsNone(parsed.exp)
        self.assertEqual(compare_progress(Progress('a',10,None),Progress('a',10,parsed.exp)),'reread')
        self.assertEqual(compare_progress(Progress('a',10,None),Progress('a',12,None)),'reevaluate')

    def test_unreadable_numbers_never_default_to_zero(self):
        for value in ("", "。", "O", "需要●", "10/20", "15O00"):
            with self.assertRaises(GuardError):
                integer(value)
        self.assertEqual(integer("26,847,138"), 26847138)

    def test_replayed_empty_material_screen(self):
        text = dict(confirm="强化", exp="0/3000", level="+0", material_count="装备强化消耗(0/15)",
                    mora="26847138", mora_cost="。")
        parsed = EnhancementObservation.parse(text)
        self.assertEqual(parsed.material_slots_used, 0)
        self.assertIsNone(parsed.mora_cost)
        text["material_count"] = "装备强化消耗(1/15)"
        self.assertIsNone(EnhancementObservation.parse(text).mora_cost)

    def test_missing_resource_fields_do_not_block_level_verification(self):
        text=dict(confirm='强化',level='+16',material_count='装备强化消耗(0/15)',exp='350/i3025')
        parsed=EnhancementObservation.parse(text)
        self.assertEqual(parsed.level,16)
        self.assertIsNone(parsed.exp)
        self.assertIsNone(parsed.mora)


if __name__ == "__main__":
    unittest.main()
