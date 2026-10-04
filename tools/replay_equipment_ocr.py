"""Replay saved frames through the shipped yas reader; never accesses the game."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from enhancer.equip import parse_panel
from enhancer.loadouts import fingerprint
from enhancer.yas_client import runtime_environment
from enhancer.recognition import label_matches


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--layout',type=Path,default=ROOT/'layouts/equip-selection-1920.json')
    args=parser.parse_args()
    cases=json.loads(args.manifest.read_text(encoding='utf-8'))
    results=[]
    with TemporaryDirectory(prefix='yas-ocr-replay-') as directory:
        dest=Path(directory)/'observation.json'
        for case in cases:
            started=time.perf_counter()
            row={'image':case['image'],'expected':case['expected'],'accepted':False}
            try:
                dest.unlink(missing_ok=True)
                subprocess.run([str(ROOT/'vendor/yas/target/release/yas_readonly.exe'),
                    '--ocr-image',str(Path(case['image']).resolve()),
                    '--observe-layout',str(args.layout.resolve())],cwd=directory,
                    env=runtime_environment(),capture_output=True,check=True,timeout=20)
                text=json.loads(dest.read_text(encoding='utf-8'))['text']
                actual=parse_panel(text,case['expected']['slot'],case['expected']['rarity'])
                labels=case.get('expected_labels',{})
                row.update(actual=actual,labels={key:text.get(key) for key in labels},
                           accepted=fingerprint(actual)==fingerprint(case['expected']) and
                           all(label_matches(text.get(key,''),value) for key,value in labels.items()))
            except Exception as exc:row['error']=str(exc)
            row['seconds']=time.perf_counter()-started
            results.append(row)
    report={'cases':len(results),'accepted':sum(r['accepted'] for r in results),'results':results}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('cases','accepted')},ensure_ascii=False))
    return 0 if report['accepted']==report['cases'] else 1


if __name__=='__main__':raise SystemExit(main())
