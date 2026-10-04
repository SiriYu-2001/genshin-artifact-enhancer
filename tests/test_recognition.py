import unittest
from unittest.mock import Mock

from enhancer.recognition import label_matches,read_verified,ReadRejected,normalize_ui_labels,set_row_candidate


class RecognitionTests(unittest.TestCase):
    def test_overlapping_set_labels_form_one_candidate(self):
        self.assertEqual(set_row_candidate({'left_580':'[影猎人','left_598':'[影猎人',
                          'right_598':'翠绿之影'},'逐影猎人'),('left',620))

    def test_ambiguous_or_weak_set_labels_do_not_select_a_row(self):
        self.assertIsNone(set_row_candidate({'left_130':'影猎人','right_310':'逐猎人'},'逐影猎人'))
        self.assertIsNone(set_row_candidate({'left_130':'逐影'},'逐影猎人'))

    def test_schema_normalization_preserves_numbers_and_material_evidence(self):
        raw={'confirm':' “强化”。','level':'+2O','sub_0':'攻击力+1O.5%',
             'material_count':'装备强化消耗(0/15)','five_label':'五星圣遗物?',
             'owner':'“奧黛塔已装备”。'}
        result=normalize_ui_labels(raw)
        self.assertEqual(result['confirm'],'强化')
        self.assertEqual(result['owner'],'奥黛塔已装备')
        for key in ('level','sub_0','material_count','five_label'):
            self.assertEqual(result[key],raw[key])
        self.assertEqual(raw['confirm'],' “强化”。')

    def test_label_noise_does_not_repair_digits_or_names(self):
        self.assertTrue(label_matches(' “强化”。 ','强化'))
        self.assertTrue(label_matches('·奧黛塔。','奥黛塔'))
        self.assertFalse(label_matches('强化0','强化'))
        self.assertFalse(label_matches('奥鲜塔','奥黛塔'))

    def test_validation_retries_complete_frames_not_field_votes(self):
        read=Mock(side_effect=[{'level':20,'atk':10}, {'level':12,'atk':21.6},
                               {'level':20,'atk':21.6}])
        def validate(frame):
            if frame!={'level':20,'atk':21.6}:raise ReadRejected('Identity mismatch')
            return frame
        result=read_verified(read,validate,interval=0)
        self.assertEqual(result,{'level':20,'atk':21.6})
        self.assertEqual(read.call_count,3)

    def test_unreadable_result_is_bounded_and_reported(self):
        read=Mock(return_value=None);record=Mock()
        def reject(_):raise ReadRejected('Unknown material rarity')
        with self.assertRaises(ReadRejected):
            read_verified(read,reject,attempts=3,interval=0,record=record)
        self.assertEqual(read.call_count,3)
        self.assertEqual(record.call_args.args[0]['status'],'failed')

    def test_transport_failure_is_not_misclassified_as_bad_ocr(self):
        read=Mock(side_effect=ConnectionError('Controller unavailable'))
        with self.assertRaises(ConnectionError):read_verified(read,lambda x:x,interval=0)
        self.assertEqual(read.call_count,1)
