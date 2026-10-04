"""Known simplified/traditional OCR glyph equivalents, not fuzzy identity matching."""
import re

_EQUIVALENTS=str.maketrans({'淚':'泪','於':'于','奧':'奥','喚':'唤','脈':'脉',
                           '異':'异','別':'别','宮':'宫','鮮':'鲜','茲':'兹'})


def canonical(text):
    return re.sub(r'\s+','',str(text)).translate(_EQUIVALENTS)


def title_has_name(title,name):
    expected=canonical(name)
    return bool(expected) and expected in canonical(title)
