"""Strict field parsers for yas output; unreadable digits never become zero."""
from dataclasses import dataclass
from collections import Counter
import re
from .execution import GuardError
from .recognition import label_matches


def integer(text):
    normalized = text.strip().replace(",", "").replace("，", "")
    if not re.fullmatch(r"[0-9]+", normalized):
        raise GuardError(f"Unreadable integer: {text!r}")
    return int(normalized)


@dataclass(frozen=True)
class EnhancementObservation:
    title: str
    level: int
    exp: int | None
    next_level_exp: int | None
    mora: int | None
    material_slots_used: int | None
    material_capacity: int | None
    mora_cost: int | None

    @classmethod
    def parse(cls, text):
        # In this calibrated field the white current level precedes a gold +N preview.
        level_match = re.fullmatch(r"\+([0-9]{1,2})(?:\s*\+[0-9]{1,2})?", text["level"].strip())
        count = re.fullmatch(r"装备强化消耗[（(]([0-9]+)/([0-9]+)[）)]", text["material_count"].replace(" ", ""))
        if not level_match:
            raise GuardError("Unreadable actual level or material count")
        level = int(level_match[1])
        if not label_matches(text["confirm"],"强化") and level!=20:
            raise GuardError("Enhancement page not recognized")
        if not count and level!=20:
            raise GuardError("Unreadable material count")
        used, capacity = map(int, count.groups()) if count else (None,None)
        if not 0 <= level <= 20 or (count and not 0 <= used <= capacity <= 30):
            raise GuardError("Implausible enhancement values")
        if level == 20:
            exp, requirement = None, None
        else:
            exp_text = text["exp"].replace(" ", "")
            if "exp_tight" in text:
                votes = Counter(value.replace(" ", "").strip(".,。:：·-－") for key, value in text.items()
                                if key in ("exp", "exp_tight", "exp_wide", "exp_high")
                                and re.fullmatch(r"[0-9]+/[0-9]+", value.replace(" ", "").strip(".,。:：·-－")))
                ranked = votes.most_common()
                if not ranked or ranked[0][1] < 2 or (len(ranked) > 1 and ranked[0][1] == ranked[1][1]):
                    exp_text = ''
                else:
                    exp_text = ranked[0][0]
            exp_match = re.fullmatch(r"([0-9]+)/([0-9]+)", exp_text)
            if not exp_match:
                exp, requirement = None, None
            else:
                exp, requirement = map(int, exp_match.groups())
                if not 0 <= exp < requirement:
                    # A dropped denominator digit is an unknown observation,
                    # not a reason to reject otherwise verified level progress.
                    exp, requirement = None, None
        try:
            cost = integer(text.get("mora_cost", ""))
        except GuardError:
            cost = None
        return cls(text.get("title", ""), level, exp, requirement, integer(text["mora"]) if text.get("mora") else None, used, capacity, cost)
