"""Standalone entrypoint. All run-time decisions are local Python/Rust code."""
import argparse
import ctypes
import json
from pathlib import Path
import shutil
import subprocess
import time

from .navigation import ROOT
from .yas_client import runtime_environment


from .controller import ensure_controller


def main():
    parser=argparse.ArgumentParser(description="Local yas/Frostflake artifact experiment; no LLM service")
    commands=parser.add_subparsers(dest="command",required=True)
    commands.add_parser('controller-check',help='Verify packaged controller startup without game input')
    ui=commands.add_parser('ui',help='Open the local artifact control panel')
    ui.add_argument('--port',type=int,default=8766)
    loadout=commands.add_parser('loadout',help='Save, plan, or apply local loadouts with live OCR verification')
    loadout.add_argument('action',choices=['save','plan','apply'])
    loadout.add_argument('--library',type=Path,default=ROOT/'runtime/loadouts/library.json')
    loadout.add_argument('--allocation',type=Path)
    loadout.add_argument('--ids',nargs='+')
    loadout.add_argument('--scan',type=Path)
    loadout.add_argument('--updates',type=Path)
    loadout.add_argument('--conflicts',choices=['strict','priority'],default='strict')
    loadout.add_argument('--output',type=Path)
    campaign=commands.add_parser('campaign',help='One scan for multiple demands')
    campaign.add_argument('action',choices=['plan','start'])
    campaign.add_argument('--config',required=True,type=Path)
    campaign.add_argument('--scan',type=Path)
    campaign.add_argument('--updates',type=Path,help='Recorded updates for offline planning only')
    campaign.add_argument('--output',type=Path,help='Offline plan output')
    start=commands.add_parser('start',help='Scan from the artifact bag, then run the complete character batch')
    start.add_argument('--profile',required=True,type=Path)
    start.add_argument('--ownership',choices=['borrow','no-borrow'],default='no-borrow')
    start.add_argument('--scan',type=Path,help='Explicitly reuse a current complete scan instead of scanning')
    prepare=commands.add_parser("prepare")
    prepare.add_argument("--scan",required=True,type=Path)
    prepare.add_argument("--profile",required=True,type=Path)
    prepare.add_argument("--ownership",choices=["borrow","no-borrow"],default="no-borrow")
    commands.add_parser("run",help="Resume the prepared single-artifact experiment")
    commands.add_parser("batch",help="Run and replan all eligible inventory candidates")
    commands.add_parser("stop",help="Stop the local controller")
    commands.add_parser("summary",help="Reproduce the prepared run report without game input")
    args=parser.parse_args()
    if args.command=='controller-check':
        ensure_controller()
        from .controller import controller_ready,read_session
        state=read_session(ROOT/'runtime/session.json')
        result={'administrator':bool(ctypes.windll.shell32.IsUserAnAdmin()),
                'controller_ready':controller_ready(state),
                'bridge_bundled':Path(state.get('bridge_path','')).resolve()==(ROOT/'bin/cocogoat-control.exe').resolve(),
                'game_input':False}
        (ROOT/'runtime/controller-check.json').write_text(json.dumps(result),encoding='utf-8')
        if not all(result[k] for k in ('administrator','controller_ready','bridge_bundled')):
            raise RuntimeError('Packaged controller diagnostic failed')
        print(json.dumps(result),flush=True)
        return
    if args.command=='ui':
        from .ui_server import serve
        serve(args.port)
        return
    if args.command=='loadout':
        from .loadouts import save_allocation,plan_file
        if args.action=='apply':
            if not args.ids or not args.scan:parser.error('loadout apply requires --ids and --scan')
            from .equip import apply
            apply(args.library,args.ids,args.scan,args.updates,args.conflicts)
            return
        if args.action=='save':
            if not args.allocation:parser.error('loadout save requires --allocation')
            print(json.dumps({'saved':save_allocation(args.allocation,args.library),'library':str(args.library)},ensure_ascii=False))
        else:
            if not args.ids or not args.scan or not args.output:parser.error('loadout plan requires --ids, --scan and --output')
            result=plan_file(args.library,args.ids,args.scan,args.updates,args.conflicts)
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'status':result['status'],'operations':len(result['operations']),'executed':False,'output':str(args.output)},ensure_ascii=False))
        return
    if args.command=='campaign':
        from .campaign_runtime import offline,start
        if args.action=='plan':
            if not args.scan or not args.output:parser.error('campaign plan requires --scan and --output')
            result=offline(args.config,args.scan,args.updates)
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            from .campaign_report import write_report
            write_report(result,args.output.with_suffix('.md'))
            print(json.dumps({'output':str(args.output),'conflicts':len(result['conflicts']),
                             'demands':[{k:r[k] for k in ('id','score','eligible_count','status')} for r in result['demands']]},ensure_ascii=False))
        else:
            if args.updates or args.output:parser.error('--updates and --output are offline planning options')
            start(args.config,args.scan)
        return
    if args.command=='start':
        from .workflow import start
        start(args.profile,args.ownership,args.scan)
        return
    if args.command=="batch":
        ensure_controller()
        from .batch import main as batch
        batch()
        return
    if args.command=="summary":
        batch_path=ROOT/'runtime/active-batch.json'
        if batch_path.exists():
            batch=json.loads(batch_path.read_text(encoding='utf-8'))
            print((Path(batch['directory'])/'summary.json').read_text(encoding='utf-8'))
            return
        from .summarize import write_summary
        print(json.dumps(write_summary(),ensure_ascii=False,indent=2))
        return
    if args.command=="stop":
        (ROOT/"runtime/stop.signal").touch()
        return
    if args.command=="prepare":
        batch_path=ROOT/'runtime/active-batch.json'
        if batch_path.exists():
            previous=json.loads(batch_path.read_text(encoding='utf-8'))
            if previous['status']=='running' or any((Path(p)/'pending.json').exists() for p in previous['runs']):
                raise RuntimeError('Finish or stop the current batch before preparing another')
            batch_path.replace(ROOT/'runtime'/time.strftime('previous-batch-%Y%m%d-%H%M%S.json'))
        from .model import Profile
        from .report import create_report
        profile=Profile.load(args.profile)
        profile.data["allowed_equipped_characters"]=["*"] if args.ownership=="borrow" else profile.data.get("character_aliases",[profile.data["character"]])
        report=create_report(args.scan,profile,include_candidates=True)
        if not report["baseline_usable"]:
            raise RuntimeError("Inventory baseline is not usable")
        candidates=[c for c in report["candidates"] if c["action"]=="enhance"]
        if not candidates:
            raise RuntimeError("No candidate reaches the threshold")
        run_dir=ROOT/"runtime"/time.strftime("run-%Y%m%d-%H%M%S")
        run_dir.mkdir(parents=True)
        active={"scan_directory":str(args.scan.resolve()),"profile":str(args.profile.resolve()),
                "ownership":args.ownership,"target_id":candidates[0]["id"],"run_directory":str(run_dir)}
        (ROOT/"runtime/active-run.json").write_text(json.dumps(active,ensure_ascii=False,indent=2),encoding="utf-8")
        (run_dir/"plan-before.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(active,ensure_ascii=False))
    else:
        ensure_controller()
        from .stage import main as run
        run()


if __name__=="__main__":
    main()
