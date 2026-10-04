"""Shared read-only recognition rules. Never repairs digits or repeats input."""
import time
import unicodedata
from difflib import SequenceMatcher

from .text_identity import canonical


class ReadRejected(ValueError):
    """A fresh observation did not satisfy the caller's complete validation."""


def label_edges(text):
    """Remove edge decoration from labels, never from numeric measurements."""
    def noise(c):
        return c.isspace() or unicodedata.category(c).startswith('P') or c in '°●・'
    while text and noise(text[0]):text=text[1:]
    while text and noise(text[-1]):text=text[:-1]
    return text


def label_matches(observed,expected):
    return label_edges(canonical(observed))==canonical(expected)


UI_LABEL_FIELDS=frozenset(('page','action','heading','apply','confirm','cancel',
    'artifact_menu','attributes','weapon','artifact','replace','locked','nonmax',
    'idle','stage_add','selected_set','character','owner'))


def normalize_ui_labels(fields):
    # Fixed labels only. Numeric fields, material evidence, descriptions and
    # complete artifact attributes stay byte-for-byte as the OCR returned them.
    return {key:label_edges(canonical(value)) if key in UI_LABEL_FIELDS else value
            for key,value in fields.items()}


def set_row_candidate(fields,expected):
    """Locate one probable row in overlapping OCR crops, not confirm a set.

    The caller must read the selected set's complete title before applying it.
    Adjacent crops of the same row are grouped before judging ambiguity.
    """
    rows=[]
    for key,value in fields.items():
        parts=key.split('_')
        if len(parts)!=2 or parts[0] not in ('left','right') or not parts[1].isdigit():continue
        value=label_edges(canonical(value))
        score=SequenceMatcher(None,value,canonical(expected)).ratio()
        if score>=.5:rows.append((parts[0],int(parts[1]),score))
    groups=[]
    for side,y,score in sorted(rows):
        if groups and groups[-1]['side']==side and y-groups[-1]['last']<=36:
            groups[-1]['values'].append((score,y));groups[-1]['last']=y
        else:groups.append({'side':side,'last':y,'values':[(score,y)]})
    ranked=sorted(((max(g['values'])[0],g) for g in groups),key=lambda row:row[0],reverse=True)
    if not ranked or ranked[0][0]<.75:return None
    if len(ranked)>1 and ranked[0][0]-ranked[1][0]<.2:return None
    score,group=ranked[0];ys=sorted(y for s,y in group['values'] if s==score)
    return group['side'],ys[len(ys)//2]+22


def read_verified(read,validate,*,attempts=4,interval=.15,retry_on=(ReadRejected,),record=None):
    """Retry whole observations; only explicit validation failures are retried.

    Transport failures propagate immediately. Validators receive one complete
    frame, not a mixture of independently voted fields from different frames.
    The callbacks must only read/validate: input and operation replay live in
    the caller's state machine, especially when a consuming action is pending.
    """
    if attempts<1:raise ValueError('At least one observation is required')
    for attempt in range(1,attempts+1):
        sample=read()
        try:
            result=validate(sample)
        except retry_on as error:
            if record:record({'status':'failed' if attempt==attempts else 'retry',
                              'attempt':attempt,'error':str(error)})
            if attempt==attempts:raise
            time.sleep(interval)
        else:
            if record:record({'status':'accepted','attempt':attempt})
            return result
