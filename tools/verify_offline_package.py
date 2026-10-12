"""Developer acceptance tool; ordinary users only extract the Windows ZIP."""
from pathlib import Path
import json,os,subprocess,time,zipfile,hashlib,uuid
from urllib.request import Request,build_opener,ProxyHandler
import pefile,argparse
from PIL import Image

parser=argparse.ArgumentParser(description="Read-only isolated portable-package acceptance; no game input")
parser.add_argument('--zip',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args();archive=args.zip.resolve();folder=args.output.resolve();folder.mkdir(parents=True,exist_ok=False)
out=folder
with zipfile.ZipFile(archive) as z:z.extractall(folder)
app=folder/'ArtifactWorkbench';assert not (app/'runtime').exists()
manifest=json.loads((app/'build-manifest.json').read_text(encoding='utf-8'))
version=manifest['release']
for name,digest in manifest['files'].items():assert hashlib.sha256((app/name).read_bytes()).hexdigest()==digest,name
pe=pefile.PE(str(app/'ArtifactWorkbench.exe'));uac=False
for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries:
    if entry.id==24:
        for name in entry.directory.entries:
            for lang in name.directory.entries:
                d=lang.data.struct;uac |= b'requireAdministrator' in pe.get_data(d.OffsetToData,d.Size)
assert uac
for name in ('bin/goodscanner/workbench_goodscanner.exe','bin/onnxruntime.dll','bin/goodscanner/VCRUNTIME140.dll','bin/goodscanner/MSVCP140.dll','python310.dll','data/goodscanner-mappings.json'):
    assert (app/name).is_file(),name
keep=('SYSTEMROOT','WINDIR','TEMP','TMP','USERPROFILE','APPDATA','LOCALAPPDATA','PROGRAMFILES','PROGRAMFILES(X86)','PROGRAMDATA','COMSPEC','PATHEXT')
env={k:v for k,v in os.environ.items() if k.upper() in keep}
env['PATH']=r'C:\Windows\System32;C:\Windows';env['PYTHONNOUSERSITE']='1';env['WORKBENCH_OFFLINE_TEST']='1'
for key in ('USERPROFILE','APPDATA','LOCALAPPDATA','TEMP','TMP'):
    target=folder/('empty-'+key.lower());target.mkdir();env[key]=str(target)

# Read-only UI smoke at ordinary privileges; no persistent compatibility setting
# or manifest modification. Real launches retain their administrator prompt.
env['__COMPAT_LAYER']='RunAsInvoker'
log=(folder/'startup.log').open('wb')
process=subprocess.Popen([str(app/'ArtifactWorkbench.exe'),'--no-browser'],cwd=app,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
opener=build_opener(ProxyHandler({}));url='';token=''
def api(path,body=None):
    q=Request(url+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json','X-Local-Token':token})
    with opener.open(q,timeout=15) as reply:return json.load(reply)
try:
    for _ in range(120):
        if process.poll() is not None:raise RuntimeError('Preview startup exited; inspect isolated startup.log')
        try:
            server=json.loads((app/'runtime/ui/server.json').read_text(encoding='utf-8'));url=server['url'];health=api('/api/health');break
        except (OSError,ValueError):time.sleep(.25)
    else:raise RuntimeError('Preview UI did not become ready')
    assert health['frozen']
    boot=api('/api/bootstrap');token=boot['token'];catalog=boot['catalog'];assert catalog['snapshots']==[]
    with opener.open(url,timeout=10) as reply:html=reply.read().decode('utf-8')
    assert health['version']==version and all(x in html for x in ('page-configure','page-elixir','page-dust','page-inventory'))
    for name in ('/app.js','/style.css'):
        with opener.open(url+name,timeout=10) as reply:assert reply.status==200
    artifacts=[]
    profile=next(p['data'] for p in catalog['profiles'] if p['data']['set_key']=='HeartOfTheFurnace')
    for slot,main in [('flower','hp'),('plume','atk'),('sands','atk_'),('goblet','atk_'),('circlet','critDMG_')]:
        artifacts.append({'setKey':'HeartOfTheFurnace','slotKey':slot,'mainStatKey':main,'rarity':5,'level':20,'location':'','lock':True,'elixirCrafted':False,'totalRolls':9,
                          'substats':[{'key':k,'value':v} for k,v in [('critRate_',18.7),('enerRech_',5.2),('eleMas',19),('def',19)]]})
    preview=api('/api/inventory/import',{'action':'preview','filename':'synthetic-good.json','content':json.dumps({'format':'GOOD','version':3,'artifacts':artifacts})})
    assert preview['can_import'];imported=api('/api/inventory/import',{'action':'commit','preview':preview['preview']})
    mona={'flower':[{'setName':'gladiatorFinale','position':'flower','star':5,'level':20,'mainTag':{'name':'lifeStatic','value':4780},
                    'normalTags':[{'name':k,'value':v} for k,v in [('critical',.187),('recharge',.052),('elementalMastery',19),('defendStatic',19)]],'equip':None,'omit':False}]}
    assert api('/api/inventory/import',{'action':'preview','filename':'synthetic-mona.json','content':json.dumps(mona)})['can_import']
    profile.update(name='临时包自检',artifact_energy_recharge_min=0)
    config={'name':'临时包自检','mode':'single','equipment':'borrow','allocation':'priority','demands':[{'id':'smoke','profile':profile}]}
    saved=api('/api/config',{'config':config});assert api('/api/config/'+saved['id'])['name']=='临时包自检'
    job=api('/api/jobs',{'kind':'preview','config_id':saved['id'],'snapshot_id':imported['snapshot_id']})['id']
    for _ in range(240):
        state=api('/api/state')
        if state['job'] and state['job']['id']==job and state['job']['status']!='running':break
        time.sleep(.25)
    assert state['job']['id']==job and state['job']['status']=='completed',state['job']['status']
    native_env=dict(env,ORT_DYLIB_PATH=str(app/'bin/onnxruntime.dll'))
    # Embedded models must load from the archive, with no user caches or Python on PATH.
    image=folder/'synthetic.png';Image.new('RGB',(320,48),'black').save(image)
    regions=folder/'regions.json';regions.write_text(json.dumps([[0,0,320,48]]))
    for model in ('ppocrv6tiny','ppocrv5','ppocrv4'):
        native=subprocess.run([str(app/'bin/goodscanner/workbench_goodscanner.exe'),'--replay-regions',str(image),str(regions),model],
            cwd=app,env=native_env,capture_output=True,text=True,encoding='utf-8',timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
        assert native.returncode==0,native.stderr
        result=json.loads(native.stdout);assert len(result)==1 and result[0]['rect']==[0,0,320,48]
    # A stale browser config ID must preserve the user's submitted form.
    recovered=api('/api/config',{'id':'f'*32,'config':config})
    assert recovered['recovered_missing'] and recovered['id']!='f'*32
    api('/api/stop',{})
    retry=api('/api/jobs',{'kind':'preview','config_id':recovered['id'],'snapshot_id':imported['snapshot_id']})['id']
    for _ in range(240):
        state=api('/api/state')
        if state['job']['id']==retry and state['job']['status']!='running':break
        time.sleep(.25)
    assert state['job']['status']=='completed'
    proof={'status':'passed','version':version,'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
           'manifest_requires_administrator':True,'ui_test_privilege':'standard-user read-only compatibility mode',
           'frozen_ui':True,'initial_inventory_empty':True,'config_saved_and_read':True,'good_import':True,'mona_import_preview':True,
           'allocation_preview_worker':True,'bundled_ocr_replay':True,'python_on_path':False,'agent_environment':False,
           'external_dependency_downloads':False,'python_external_network_blocked':True,'empty_user_cache':True,'stale_config_recovered':True,'stop_then_new_job':True,'game_input':False,'live_game_acceptance':False}
    (out/'isolated-smoke-check.json').write_text(json.dumps(proof,indent=2),encoding='utf-8')
    (folder/'check.json').write_text(json.dumps(proof,indent=2),encoding='utf-8')
    print(json.dumps(proof),flush=True)
finally:
    if process.poll() is None:process.terminate();process.wait(timeout=10)
    log.close()
