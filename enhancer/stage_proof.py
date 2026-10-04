"""Whole-batch proof for game-generated selections, not arbitrary preselected materials."""
from dataclasses import dataclass
from .execution import GuardError, Material


@dataclass(frozen=True)
class StageProof:
    empty_before: bool
    rarity_limit: int
    five_star_quick_add_disabled: bool
    stage_add_observed: bool
    settings_evidence: str


def validate_restricted_stage(proof,count,visible):
    # A freshly empty tray populated only by the game's restricted stage-add
    # has known allowed types: <=4-star artifacts and the two EXP items.
    # Exact stock, stack quantities and Mora are not safety preconditions.
    if (not proof.empty_before or proof.rarity_limit!=4 or not proof.five_star_quick_add_disabled
            or not proof.stage_add_observed or not proof.settings_evidence):
        raise GuardError('Unverified restricted stage provenance')
    if not 1<=count<=15 or len(visible)!=min(count,6) or any(s not in (1,2,3,4) for s in visible):
        raise GuardError('Five-star, unknown or inconsistent visible material')


def infer_stage_batch(proof, count, mora_cost, remaining_three, remaining_four, visible=(), exp_quantities=None):
    if (not proof.empty_before or proof.rarity_limit != 4 or
            not proof.five_star_quick_add_disabled or not proof.stage_add_observed or not proof.settings_evidence):
        raise GuardError("No complete restricted-stage-selection proof")
    if not 1 <= count <= 15 or mora_cost is None or not 0 < mora_cost <= 1000000:
        raise GuardError("Invalid material quantity/cost")
    # This mode requires a complete scan with no 1/2-star artifacts. The only
    # eligible artifacts are 3/4 stars, and the game disallows 5-star quick-add.
    # Include stacked EXP items (2500/10000 Mora each, one slot per item type).
    # Enumerate all compositions and accept only one solution consistent with
    # the scanned inventory, slot count, cost and directly observed rarities.
    solutions=[]
    for three in range(min(count,remaining_three)+1):
        for four in range(min(count-three,remaining_four)+1):
            slots=count-three-four
            cost=mora_cost-1260*three-2520*four
            choices=[]
            if slots==0 and cost==0:choices=[(0,0)]
            elif slots==1 and cost>0:
                if cost%2500==0:choices.append((cost//2500,0))
                if cost%10000==0:choices.append((0,cost//10000))
            elif slots==2 and cost>0:
                choices=[((cost-10000*essence)//2500,essence) for essence in range(1,cost//10000+1)
                         if cost-10000*essence>0 and (cost-10000*essence)%2500==0]
            for oil,essence in choices:
                quantities={3:oil,4:essence}
                if any(quantities[star]!=q for star,q in (exp_quantities or {}).items()):continue
                stars=[3]*three+[4]*four+([3] if oil else [])+([4] if essence else [])
                if any(s not in (3,4) for s in visible) or any(visible.count(s)>stars.count(s) for s in (3,4)):continue
                materials=[Material("artifact",3,"3-star artifact",1,True)]*three+[Material("artifact",4,"4-star artifact",1,True)]*four
                if oil:materials.append(Material("exp_item",3,"祝圣油膏",oil,True))
                if essence:materials.append(Material("exp_item",4,"祝圣精华",essence,True))
                solutions.append(materials)
    if len(solutions)!=1:
        raise GuardError(f"Batch composition is not uniquely identified: {len(solutions)} possibilities")
    return solutions[0]
