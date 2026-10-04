"""Single-command fresh scan, planning and inventory-wide execution."""
from pathlib import Path
import json
import time
import uuid

from .navigation import ROOT, Navigation
from .model import Profile
from .report import load_scan, create_report
from .batch import save


def fresh_scan():
    nav=Navigation()
    try:
        games=[w for w in nav.api('GET','/api/windows') if w['title']=='原神' and w['classname']=='UnityWndClass']
        if len(games)!=1:raise RuntimeError('请先启动原神并打开背包圣遗物页面')
        nav.api('PATCH',f"/api/windows/{games[0]['hWnd']}")
        time.sleep(.5)
        try:
            nav.ensure_bag()
        except RuntimeError:
            nav.return_from_character_to_bag()
        nav._navigation_click('open_filter',167,1018)
        nav.clear_open_filter()
        if nav.acquisition_enabled() is True:
            nav.turn_off_acquisition_order()
        # Resetting the filter returns the grid to its beginning. Up-scroll
        # also clears small residual offsets before yas's calibrated traversal.
        nav.scroll_grid(-80)
        session=nav.session()
    finally:
        nav.yas().close()
    request_id=uuid.uuid4().hex
    directory=Path(session['directory'])
    save(directory/'requests'/f'{request_id}.json',{'id':request_id,'kind':'scan-all'})
    response=directory/'responses'/f'{request_id}.json'
    deadline=time.monotonic()+2450
    last_notice=0
    while time.monotonic()<deadline:
        if (ROOT/'runtime/stop.signal').exists():raise RuntimeError('Stop requested')
        if response.exists():
            try:state=json.loads(response.read_text(encoding='utf-8-sig'))
            except json.JSONDecodeError:state={}
            if state.get('state')=='failed':raise RuntimeError(state.get('error','Scan failed'))
            if state.get('state')=='exited':
                if state.get('exitCode')!=0:raise RuntimeError(f"yas failed: {state['directory']}")
                scan=Path(state['directory'])
                _,_,coverage=load_scan(scan)
                if not coverage['complete']:raise RuntimeError(f'Incomplete scan: {coverage}')
                print(json.dumps({'scan_finished':str(scan),'coverage':coverage},ensure_ascii=False),flush=True)
                return scan
        if time.monotonic()-last_notice>45:
            print(json.dumps({'phase':'scanning','request_id':request_id}),flush=True)
            last_notice=time.monotonic()
        time.sleep(.5)
    raise RuntimeError('Scan timed out')


def start(profile_path,ownership,scan=None):
    profile=Profile.load(profile_path)
    profile.data['allowed_equipped_characters']=['*'] if ownership=='borrow' else profile.data['character_aliases']
    manifest=ROOT/'runtime/active-batch.json'
    if manifest.exists():
        old=json.loads(manifest.read_text(encoding='utf-8'))
        if old['status'] not in ('finished','finished-with-deferred','stopped') or any((Path(p)/'pending.json').exists() for p in old['runs']):
            raise RuntimeError('Previous batch must finish before starting a new character')
    from .__main__ import ensure_controller
    ensure_controller()
    scan=Path(scan) if scan else fresh_scan()
    report=create_report(scan,profile)
    if not report['baseline_usable']:raise RuntimeError('Fresh inventory baseline is not usable')
    directory=ROOT/'runtime'/time.strftime('batch-%Y%m%d-%H%M%S')
    directory.mkdir()
    save(directory/'profile.json',profile.data)
    save(directory/'plan-before.json',report)
    save(directory/'inventory-updates.json',{})
    if manifest.exists():manifest.replace(ROOT/'runtime'/time.strftime('previous-batch-%Y%m%d-%H%M%S.json'))
    save(manifest,{'directory':str(directory),'scan_directory':str(scan.resolve()),
                  'profile':str(directory/'profile.json'),'ownership':ownership,'runs':[],
                  'status':'prepared','started':time.time(),'entrypoint':'start'})
    print(json.dumps({'phase':'enhancing','baseline':report['best_available_build']['displayed_score'],'directory':str(directory)},ensure_ascii=False),flush=True)
    from .batch import main
    main()
