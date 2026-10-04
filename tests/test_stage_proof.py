import unittest
from dataclasses import replace
from enhancer.stage_proof import StageProof,infer_stage_batch,validate_restricted_stage
from enhancer.execution import GuardError


class BatchProofTests(unittest.TestCase):
    def setUp(self):
        self.proof=StageProof(True,4,True,True,"settings.png")

    def test_rejects_untrusted_source_and_five_star_settings(self):
        for bad in [replace(self.proof,empty_before=False),replace(self.proof,rarity_limit=5),
                    replace(self.proof,five_star_quick_add_disabled=False),replace(self.proof,stage_add_observed=False)]:
            with self.assertRaises(GuardError):infer_stage_batch(bad,15,18900,50,100)
        with self.assertRaises(GuardError):infer_stage_batch(self.proof,15,18900,50,100,[5])

    def test_real_plain_and_mixed_artifact_batches(self):
        self.assertEqual([m.rarity for m in infer_stage_batch(self.proof,15,18900,50,100)],[3]*15)
        self.assertEqual([m.rarity for m in infer_stage_batch(self.proof,15,22680,12,100)],[3]*12+[4]*3)

    def test_final_batch_includes_oil(self):
        batch=infer_stage_batch(self.proof,11,27700,0,92,[4]*6)
        self.assertEqual(len(batch),11)
        self.assertEqual(sum(m.kind=="artifact" for m in batch),10)
        self.assertEqual((batch[-1].name,batch[-1].quantity),("祝圣油膏",1))

    def test_ambiguous_exp_items_require_rarity_evidence(self):
        with self.assertRaises(GuardError):infer_stage_batch(self.proof,1,10000,0,100)
        batch=infer_stage_batch(self.proof,1,10000,0,100,[4])
        self.assertEqual((batch[0].name,batch[0].quantity),("祝圣精华",1))

    def test_stacked_stage_bottles_use_observed_quantity(self):
        batch=infer_stage_batch(self.proof,2,67500,0,82,[4,3],{4:6})
        self.assertEqual({m.name:m.quantity for m in batch},{'祝圣精华':6,'祝圣油膏':3})

    def test_resource_free_stage_keeps_five_star_guards(self):
        validate_restricted_stage(self.proof,2,[4,3])
        for proof,count,visible in [(replace(self.proof,rarity_limit=5),2,[4,3]),
                (replace(self.proof,five_star_quick_add_disabled=False),2,[4,3]),
                (replace(self.proof,empty_before=False),2,[4,3]),
                (self.proof,2,[5,3]),(self.proof,2,[None,3]),(self.proof,2,[4])]:
            with self.assertRaises(GuardError):validate_restricted_stage(proof,count,visible)
