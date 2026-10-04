"""Offline single-definition recommendations from an existing complete scan."""
import argparse
import json
from pathlib import Path
from fractions import Fraction as F
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from enhancer.report import load_scan
from enhancer.batch import apply_updates,save
from enhancer.model import Profile,SLOTS
from enhancer.elixir import ElixirAdvisor


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--ids',nargs='+',required=True)
    parser.add_argument('--scan',type=Path,required=True)
    parser.add_argument('--updates',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--four-line-probability',type=F,default=F(1,3))
    parser.add_argument('--minimum-gain',type=float,default=0.)
    args=parser.parse_args()
    config=json.loads(args.config.read_text(encoding='utf-8-sig'))
    pool,names,coverage=load_scan(args.scan)
    if not coverage['complete']:raise ValueError('Complete inventory required')
    if args.updates:pool=apply_updates(pool,json.loads(args.updates.read_text(encoding='utf-8-sig')))
    demands={d['id']:d for d in config['demands']}
    report={'status':'running','scope':'One character at a time, one on-set definition, full +20 outcome, existing +20 inventory, no Dust, no future farming.',
            'algorithm':'Exhaustive discrete type/count/tier enumeration; float64 weighted sums, 1e-10 comparison tolerance; inventory rounding bounds.',
            'assumptions':{'p_four':float(args.four_line_probability),'p_four_status':'community assumption, not an official rate disclosure',
                           'remaining_types':'weighted without replacement after excluding main and selected stats',
                           'guarantee':'late forced hits, total at least two selected-stat upgrades',
                           'minimum_gain':args.minimum_gain,'definition_slots_remaining':'not read; verify manually before using recommendations'},
            'config':str(args.config.resolve()),'scan':str(args.scan.resolve()),'profiles':[]}
    for identifier in args.ids:
        profile=Profile(demands[identifier]['profile']);advisor=ElixirAdvisor(pool,profile)
        row={'id':identifier,'profile':profile.data,'baseline':[float(advisor.inventory.baseline.lo),float(advisor.inventory.baseline.hi)],
             'baseline_ids':list(advisor.inventory.best_ids),'actions':[]}
        report['profiles'].append(row);started=time.perf_counter()
        actions=list(advisor.actions())
        for index,(slot,main,selected) in enumerate(actions,1):
            record=advisor.evaluate(slot,main,selected,args.four_line_probability,args.minimum_gain)
            row['actions'].append(record)
            if index%36==0 or index==len(actions):
                save(args.output,report)
                print(json.dumps({'character':profile.data['character'],'evaluated':index,'total':len(actions),'elapsed_seconds':round(time.perf_counter()-started,1)},ensure_ascii=False),flush=True)
        row['best_per_slot']=[max((a for a in row['actions'] if a['slot']==slot),key=lambda a:a['expected_gain_lower']) for slot in SLOTS]
        row['recommendations']={key:sorted(row['actions'],key=lambda a:a[key],reverse=True)[:5]
                                for key in ('probability_lower','expected_gain_lower','expected_gain_per_elixir_lower')}
        row['seconds']=time.perf_counter()-started
        save(args.output,report)
    report['status']='complete';save(args.output,report)
    for row in report['profiles']:
        print(json.dumps({'character':row['profile']['character'],'baseline':row['baseline'],'best_per_slot':[{k:a[k] for k in ('slot','main','selected','cost','probability_lower','expected_gain_lower','expected_gain_per_elixir_lower')} for a in row['best_per_slot']]},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
