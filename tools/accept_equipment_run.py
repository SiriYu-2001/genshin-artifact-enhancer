"""Run two complete equipment passes against frozen code, without agent input."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import requests

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from enhancer.batch import save


def hashes():
    files=[]
    for directory,pattern in [('enhancer','*.py'),('layouts','*.json'),('tools','*.py'),
                              ('tools','*.ps1'),('tools','*.rs')]:
        files.extend((ROOT/directory).glob(pattern))
    files.extend([ROOT/'vendor/yas/target/release/yas_readonly.exe',
                  ROOT/'runtime/loadouts/library.json'])
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--base',default='http://127.0.0.1:8766')
    args=parser.parse_args()
    active=json.loads((ROOT/'runtime/active-batch.json').read_text(encoding='utf-8'))
    if Path(active['directory']).resolve()!=args.campaign.resolve():raise ValueError('Campaign changed')
    selection=json.loads((args.campaign/'equip-selection.json').read_text(encoding='utf-8'))
    report={'status':'running','ids':selection['ids'],'characters':selection['characters'],
            'source_hashes':hashes(),'passes':[],'started':time.time()}
    save(args.output,report)
    session=requests.Session();session.trust_env=False
    session.headers['X-Local-Token']=session.get(args.base+'/api/bootstrap',timeout=10).json()['token']
    snapshots=session.get(args.base+'/api/catalog',timeout=10).json()['snapshots']
    snapshot=next(s for s in snapshots if s['label']==Path(active['scan_directory']).name)
    try:
        for number in (1,2):
            if hashes()!=report['source_hashes']:raise RuntimeError('Code, layout, model adapter or saved loadouts changed')
            response=session.post(args.base+'/api/jobs',json={'kind':'equip','ids':selection['ids'],
                                  'snapshot_id':snapshot['id']},timeout=20)
            response.raise_for_status();job=response.json()['id']
            report['current_job']=job;report['current_pass']=number;save(args.output,report)
            print(json.dumps({'pass':number,'job':job}),flush=True)
            result_path=ROOT/'runtime/ui/jobs'/job/'result.json'
            deadline=time.monotonic()+45*60
            while not result_path.exists():
                if time.monotonic()>deadline:raise RuntimeError('Acceptance wait timed out; job was not replayed or killed')
                time.sleep(2)
            result=json.loads(result_path.read_text(encoding='utf-8'))
            if result.get('status')!='verified':raise RuntimeError(result.get('error','Equipment pass failed'))
            if [r['id'] for r in result['loadouts']]!=selection['ids']:
                raise RuntimeError('Not every requested loadout was verified in order')
            signatures=[]
            for row in result['loadouts']:
                if {v['slot'] for v in row['verified']}!={'flower','plume','sands','goblet','circlet'}:
                    raise RuntimeError('Incomplete slot verification')
                signatures.extend(v['fingerprint'] for v in row['verified'])
            if len(signatures)!=len(set(signatures)):raise RuntimeError('Verified loadouts conflict')
            changed=sum(item['status']=='equipped' for row in result['loadouts'] for item in row['items'])
            if number==2 and changed:raise RuntimeError('Second identical pass changed equipment unexpectedly')
            if hashes()!=report['source_hashes']:raise RuntimeError('Source changed during acceptance')
            report['passes'].append({'number':number,'job':job,'directory':result['directory'],
                                      'verified_slots':len(signatures),'changed':changed})
            save(args.output,report)
            # Let the UI release its worker before the next complete pass.
            while session.get(args.base+'/api/state',timeout=10).json()['busy']:time.sleep(.5)
        report['status']='verified';report['finished']=time.time()
    except Exception as exc:
        report.update(status='failed',error=str(exc),finished=time.time())
        raise
    finally:save(args.output,report)


if __name__=='__main__':main()
