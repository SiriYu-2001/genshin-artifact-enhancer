"""Reproducible enhancement extension, applied after patch_goodscanner.py."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]/'vendor/goodscanner'

def edit(name,old,new):
    p=ROOT/name;s=p.read_text(encoding='utf-8')
    if s.count(old)!=1:raise RuntimeError(f'Enhancement patch context mismatch: {name}: {old[:70]}')
    p.write_text(s.replace(old,new),encoding='utf-8')

def main():
    source=Path(__file__).with_name('workbench_enhance.rs')
    (ROOT/'genshin/src/manager/workbench_enhance.rs').write_bytes(source.read_bytes())
    (ROOT/'genshin/src/bin/workbench_goodscanner.rs').write_bytes(source.with_name('workbench_goodscanner.rs').read_bytes())
    p=ROOT/'genshin/src/manager/mod.rs'
    p.write_text(p.read_text(encoding='utf-8')+'\npub mod workbench_enhance;\n',encoding='utf-8')
    edit('genshin/Cargo.toml','"Win32_System_LibraryLoader", "Win32_UI_WindowsAndMessaging"','"Win32_System_LibraryLoader", "Win32_UI_WindowsAndMessaging", "Win32_Storage_FileSystem"')
    edit('genshin/src/scanner/artifact/scanner.rs','        // Maximum possible substat lines for this rarity and level.', '''        if crate::manager::workbench_enhance::strict_bag_read() {
            let exact=regex::Regex::new(r"^\\s*\\+?\\s*\\d{1,2}\\s*$")?;
            anyhow::ensure!(exact.is_match(&level_text1) && exact.is_match(&level_text2) && lv1==lv2 && lv1>=0,"Uncertain original artifact level");
        }
        // Maximum possible substat lines for this rarity and level.''')
    edit('genshin/src/scanner/artifact/scanner.rs','        // 6. Set name', '''        if crate::manager::workbench_enhance::strict_bag_read() {
            anyhow::ensure!(solved.is_some(),"Artifact roll history not validated");
            crate::manager::workbench_enhance::verify_bag_numbers(&phase1_texts,&substats,&unactivated_substats)?;
        }
        // 6. Set name''')
    f='genshin/src/server.rs'
    edit(f,'    Equip(EquipRequest),','    Equip(EquipRequest),\n    Enhance(crate::manager::workbench_enhance::Request),')
    edit(f,'pub trait ManageExecutor {','''pub trait ManageExecutor {
    fn execute_enhance(&mut self, _request:crate::manager::workbench_enhance::Request,
                       _cancel:yas::cancel::CancelToken)->ManageResult { panic!("Enhancement executor unavailable") }
''')
    edit(f,'impl ManageExecutor for GameExecutor {','''impl ManageExecutor for GameExecutor {
    fn execute_enhance(&mut self, request:crate::manager::workbench_enhance::Request,
                       cancel:yas::cancel::CancelToken)->ManageResult {
        crate::manager::workbench_enhance::execute(&mut self.ctrl,&self.manager,request,cancel)
    }
''')
    edit(f,'"verifyOnly":true,"stopMarker":true','"verifyOnly":true,"stopMarker":true,"enhancementVersion":1')
    edit(f,'                (Method::Post, "/equip") => {','''                (Method::Post, "/enhance") if std::env::var_os("WORKBENCH_GOOD_TOKEN").is_some() => {
                    handle_enhance(request,&http_enabled,&http_state,&http_job_tx,cors_ref);
                },
                (Method::Post, "/equip") => {''')
    edit(f,'                        JobRequest::Equip(r) => r.equip.len(),','                        JobRequest::Equip(r) => r.equip.len(),\n                        JobRequest::Enhance(_) => 1,')
    edit(f,'                JobRequest::Equip(_) => true,','                JobRequest::Equip(_) => true,\n                JobRequest::Enhance(_) => true,')
    edit(f,'                JobRequest::Equip(equip_req) => {','''                JobRequest::Enhance(r) => JobOutcome::ManageEquip {
                    result:exec.execute_enhance(r,cancel_token),artifact_snapshot:None,invalidates_cache:true,
                },
                JobRequest::Equip(equip_req) => {''')
    p=ROOT/f
    with p.open('a',encoding='utf-8') as out:out.write('''
fn handle_enhance(mut request:tiny_http::Request,enabled:&AtomicBool,state:&Arc<Mutex<JobState>>,
                  tx:&mpsc::Sender<(String,JobRequest)>,origin:Option<&str>) {
    if !enabled.load(Ordering::Relaxed) || state.lock().unwrap().state==JobPhase::Running {
        respond_error_message(request,409,"Executor busy or disabled",origin);return;
    }
    if request.body_length().unwrap_or(MAX_BODY_SIZE+1)>MAX_BODY_SIZE {
        respond_error_message(request,413,"Request too large",origin);return;
    }
    let mut body=String::new();
    if request.as_reader().read_to_string(&mut body).is_err(){respond_error_message(request,400,"Invalid body",origin);return;}
    let parsed=serde_json::from_str::<crate::manager::workbench_enhance::Request>(&body)
        .map_err(anyhow::Error::from).and_then(|r|{crate::manager::workbench_enhance::validate(&r)?;Ok(r)});
    let r=match parsed{Ok(r)=>r,Err(e)=>{respond_error_message(request,400,&e.to_string(),origin);return;}};
    let id=uuid::Uuid::new_v4().to_string();*state.lock().unwrap()=JobState::running(id.clone(),1);
    if tx.send((id.clone(),JobRequest::Enhance(r))).is_err(){*state.lock().unwrap()=JobState::idle();respond_error_message(request,500,"Executor unavailable",origin);return;}
    respond_json(request,202,&serde_json::json!({"jobId":id,"total":1}).to_string(),origin);
}
''')

if __name__=='__main__':
    main()
    import runpy
    runpy.run_path(str(Path(__file__).with_name('patch_workbench_feedback.py')),run_name='__main__')
