"""Compare recognition models through yas on private saved frames; no game input."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from enhancer.equip import parse_panel
from enhancer.loadouts import fingerprint
from enhancer.recognition import label_matches
from enhancer.sets import SET_LABELS
from enhancer.yas_client import runtime_environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path)
    parser.add_argument('--dictionary', type=Path)
    parser.add_argument('--name', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--contexts', type=Path, help='Additional labelled UI frames')
    parser.add_argument('--context-only', action='store_true')
    args = parser.parse_args()
    if bool(args.model) != bool(args.dictionary):
        parser.error('model and dictionary must be supplied together')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cases = json.loads((ROOT/'runtime/equipment-replay-manifest.json').read_text(encoding='utf-8'))
    layout = json.loads((ROOT/'layouts/equip-selection-1920.json').read_text(encoding='utf-8'))
    plain = json.loads(json.dumps(layout))
    for region in plain['regions']:
        region.pop('trim_text', None)
    (output/'untrimmed.json').write_text(json.dumps(plain, ensure_ascii=False), encoding='utf-8')
    jobs = []
    contexts = json.loads(args.contexts.read_text(encoding='utf-8')) if args.contexts else []
    for variant, path in [('current', ROOT/'layouts/equip-selection-1920.json'), ('untrimmed', output/'untrimmed.json')]:
        for index, case in enumerate(cases):
            jobs.append({'id':f'{variant}:{index}', 'image':case['image'], 'layout':str(path)})
    if args.context_only:jobs=[]
    jobs.extend(dict(case, id=f'context:{index}') for index,case in enumerate(contexts))
    manifest = output/'batch.json'
    manifest.write_text(json.dumps(jobs, ensure_ascii=False), encoding='utf-8')
    env = runtime_environment()
    env.pop('YAS_GENERAL_MODEL', None)
    env.pop('YAS_GENERAL_DICT', None)
    if args.model:
        env.update(YAS_GENERAL_MODEL=str(args.model.resolve()), YAS_GENERAL_DICT=str(args.dictionary.resolve()))
    process = subprocess.run([str(ROOT/'vendor/yas/target/release/yas_readonly.exe'), '--ocr-batch', str(manifest)],
                             cwd=output, env=env, capture_output=True, timeout=300)
    (output/'stderr.log').write_bytes(process.stderr)
    process.check_returncode()
    raw = json.loads((output/'batch-observations.json').read_text(encoding='utf-8'))
    results = []
    context_results = []
    for record in raw['results']:
        variant, index = record['id'].split(':')
        if variant == 'context':
            case = contexts[int(index)]
            text = record['text']
            found = {value for value in SET_LABELS.values() if any(label_matches(t,value) for t in text.values())}
            expected = set(case.get('expected_sets',[]))
            context_results.append(dict(record, name=case['name'],
                labels_ok=all(label_matches(text.get(k,''),v) for k,v in case.get('expected_labels',{}).items()),
                text_ok=all(text.get(k,'')==v for k,v in case.get('expected_text',{}).items()),
                lost_sets=sorted(expected-found), added_sets=sorted(found-expected), found_sets=len(found)))
            continue
        case = cases[int(index)]
        text = record['text']
        row = dict(record, variant=variant, image=case['image'], identity_ok=False)
        try:
            actual = parse_panel(text, case['expected']['slot'], case['expected']['rarity'])
            row['identity_ok'] = fingerprint(actual) == fingerprint(case['expected'])
        except (ValueError, KeyError) as exc:
            row['error'] = str(exc)
        row['labels_ok'] = all(label_matches(text.get(k, ''), v) for k, v in case.get('expected_labels', {}).items())
        row['accepted'] = row['identity_ok'] and row['labels_ok']
        results.append(row)
    summary = {}
    for variant in ('current', 'untrimmed'):
        rows = [r for r in results if r['variant'] == variant]
        if not rows:continue
        timings = sorted(r['region_ms'] for r in rows)
        summary[variant] = {'cases':len(rows), 'accepted':sum(r['accepted'] for r in rows),
                            'identity_ok':sum(r['identity_ok'] for r in rows),
                            'median_region_ms':statistics.median(timings),
                            'p95_region_ms':timings[min(len(timings)-1, int(len(timings)*.95))]}
    report = {'name':args.name, 'model_sha256':hashlib.sha256(args.model.read_bytes()).hexdigest() if args.model else 'embedded-v5-mobile',
              'load_ms':raw['load_ms'], 'summary':summary, 'results':results, 'contexts':context_results,
              'timing_scope':'Single-thread yas CPU; warm whole-panel regions, includes crop/normalization/decoding/layout parsing, excludes PNG decode and model load.',
              'sample_limit':'35 development regression frames, not an independent test set; untrimmed retains current crop coordinates.'}
    (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'name':args.name, 'load_ms':raw['load_ms'], 'summary':summary}, ensure_ascii=False), flush=True)
    for row in context_results:
        print(json.dumps({k:row[k] for k in ('name','labels_ok','text_ok','lost_sets','added_sets','found_sets','region_ms')},ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
