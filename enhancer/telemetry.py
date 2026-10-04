"""Small local timing records; never store controller credentials."""
import json
from pathlib import Path
import time

ROOT=Path(__file__).resolve().parents[1]


def record(kind,name,started):
    value={'time':time.time(),'kind':kind,'name':name,'ms':round((time.perf_counter()-started)*1000,2)}
    try:
        with (ROOT/'runtime/timings.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(value)+'\n')
    except OSError:pass


def recent(path):
    path=Path(path)
    if not path.exists():return []
    with path.open('rb') as f:
        f.seek(0,2);length=f.tell();f.seek(max(0,length-64000));data=f.read().decode('utf-8',errors='replace')
    groups={}
    for line in data.splitlines():
        try:row=json.loads(line)
        except ValueError:continue
        key=row['kind']+':'+row['name'];groups.setdefault(key,[]).append(row['ms'])
    return [{'name':k,'count':len(v),'average_ms':round(sum(v)/len(v),1),'p90_ms':sorted(v)[min(len(v)-1,int(len(v)*.9))]}
            for k,v in sorted(groups.items())]
