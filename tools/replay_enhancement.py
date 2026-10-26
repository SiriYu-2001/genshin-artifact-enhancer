"""Replay saved enhancement evidence using the production native parser; no game input."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import uuid


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--exe',type=Path,required=True);p.add_argument('--ort',type=Path,required=True)
    p.add_argument('--cases',type=Path,required=True,help='JSON cases; required=true makes rejection a blocking failure')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--baseline',type=Path,help='Previous results.json; reject regressions on previously correct frames')
    p.add_argument('--exploratory',action='store_true',help='Inspect unlabelled frames; never counts as release acceptance')
    args=p.parse_args()
    # Validate manifest paths before starting dozens of model processes.
    cases=json.loads(args.cases.read_text(encoding='utf-8'))
    previous=json.loads(args.baseline.read_text(encoding='utf-8')) if args.baseline else None
    args.output.mkdir(parents=True,exist_ok=True)
    results=[];env=dict(os.environ,ORT_DYLIB_PATH=str(args.ort.resolve()))
    for i,c in enumerate(cases):
        a=c['artifact'];request={'operationId':str(uuid.uuid4()),'action':'inspect','name':a['name'],
            'artifact':{'setKey':a['setKey'],'slotKey':a['slotKey'],'mainStatKey':a['mainStatKey'],'level':a['level'],'rarity':5,
                        'substats':[s for s in a['substats'] if not s.get('pending')],
                        'unactivatedSubstats':[s for s in a['substats'] if s.get('pending')],'location':'','lock':a['lock']}}
        path=args.output/f'case-{i}.json';path.write_text(json.dumps(request,ensure_ascii=False),encoding='utf-8')
        row={'case':i,'frame':c['frame'],'accepted':False,'required':c.get('required') is True,
             'expected_rejection':c.get('expected_rejection')}
        try:
            if 'required' in c and not isinstance(c['required'],bool):raise ValueError('required must be a boolean')
            run=subprocess.run([str(args.exe.resolve()),'--replay-enhancement',str(Path(c['frame']).resolve()),str(path.resolve()),'ppocrv6tiny'],
                               env=env,capture_output=True,text=True,encoding='utf-8',timeout=30)
            row['accepted']=run.returncode==0
            if run.returncode:
                row['error']=run.stderr
                if row['expected_rejection']:row['correct_rejection']=row['expected_rejection'] in run.stderr
            else:
                observed=json.loads(run.stdout);row['observed']=observed
                row['correct']=(observed['level']==c['level'] and observed['materialCount']==c['count']
                    and observed['stats']=={s['key']:float(s['value']) for s in a['substats']}
                    and all(observed.get(k)==v for k,v in c.get('expected_observation',{}).items()))
        except (subprocess.TimeoutExpired,OSError,ValueError,KeyError,TypeError) as exc:
            # A missing DLL, timeout or malformed output is not an OCR rejection.
            row.update(accepted=False,infrastructure_error=str(exc))
        results.append(row)
    summary={'cases':len(results),'accepted_correct':sum(r.get('correct',False) for r in results),
             'accepted_wrong':sum(r.get('correct') is False for r in results),
             'rejected':sum(not r['accepted'] and not r.get('infrastructure_error') for r in results),
             'correct_rejections':sum(r.get('correct_rejection',False) for r in results),
             'unexpected_acceptances':[r['case'] for r in results if r['expected_rejection'] and r['accepted']],
             'required_failed':[r['case'] for r in results if r['required'] and not (r.get('correct_rejection') if r['expected_rejection'] else r.get('correct'))]}
    summary.update(mode='exploratory' if args.exploratory else 'acceptance',
                   unlabelled_cases=[r['case'] for r in results if not r['required']],
                   infrastructure_failed=[r['case'] for r in results if r.get('infrastructure_error')],
                   labelled_positive=sum(r['required'] and not r['expected_rejection'] for r in results),
                   labelled_negative=sum(r['required'] and bool(r['expected_rejection']) for r in results))
    if args.baseline:
        required={r['frame'] for r in previous['cases'] if r.get('correct') is True}
        current={r['frame']:r for r in results}
        summary['regressed_frames']=[f for f in sorted(required) if not current.get(f,{}).get('correct')]
    failed=bool(not results or summary['accepted_wrong'] or summary['unexpected_acceptances'] or summary['required_failed']
                or summary['infrastructure_failed'] or summary.get('regressed_frames'))
    summary['acceptance_passed']=not (failed or args.exploratory or summary['unlabelled_cases'])
    (args.output/'results.json').write_text(json.dumps({'summary':summary,'cases':results},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary),flush=True)
    if failed or (not args.exploratory and summary['unlabelled_cases']):raise SystemExit(1)


if __name__=='__main__':main()
