"""Apply the Workbench host/verification adapter to GOODScanner bffc4aad (v2026.09.30).

Run once on an unmodified checkout. No replacement OCR or independent scanner.
"""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]/'vendor/goodscanner'
def edit(file,old,new):
    p=ROOT/file;s=p.read_text(encoding='utf-8')
    if s.count(old)!=1:raise RuntimeError(f'Upstream context mismatch: {file}: {old[:60]}')
    p.write_text(s.replace(old,new),encoding='utf-8')

edit('yas/Cargo.toml','ort = { version = "2.0.0-rc.10", optional = true, features = ["load-dynamic"] }',
     'ort = { version = "=2.0.0-rc.10", optional = true, default-features = false, features = ["std", "ndarray", "load-dynamic"] }')
target=ROOT/'genshin/src/bin/workbench_goodscanner.rs';target.parent.mkdir(exist_ok=True)
target.write_bytes(Path(__file__).with_name('workbench_goodscanner.rs').read_bytes())

# Never fetch mapping updates while running the bundled offline backend.
edit('genshin/src/scanner/common/mappings.rs','    let meta = load_meta();\n    let cache_exists',
     '''    if std::env::var_os("WORKBENCH_GOOD_TOKEN").is_some() {
        anyhow::ensure!(Path::new(MAPPINGS_CACHE_PATH).is_file(), "bundled mappings missing");
        return Ok(());
    }
    let meta = load_meta();
    let cache_exists''')
edit('genshin/src/cli.rs','    let scan_defaults = ScanCoreConfig {\n        scan_characters: true,',
     '    let scan_defaults = ScanCoreConfig {\n        artifact_min_rarity: 5,\n        scan_characters: true,')

# Dedicated process/token: never attach to a user's unrelated scanner instance.
edit('genshin/src/server.rs','            match (method, url.as_str()) {', '''            if let Ok(token) = std::env::var("WORKBENCH_GOOD_TOKEN") {
                let expected=format!("Bearer {}",token);
                let valid=request.headers().iter().any(|h| h.field.equiv("Authorization") && h.value.as_str()==expected);
                if !valid {respond_json(request,403,&serde_json::json!({"error":"unauthorized"}).to_string(),cors_ref);continue;}
            }
            match (method, url.as_str()) {
                (Method::Get, "/workbench") => {
                    let value=serde_json::json!({"backend":"GOODScanner","revision":"bffc4aad040eac0bb5f10c8b0b2ef86121fb29b5",
                        "instance":std::env::var("WORKBENCH_GOOD_INSTANCE").unwrap_or_default(),"verifyOnly":true,"stopMarker":true});
                    respond_json(request,200,&value.to_string(),cors_ref);
                },''')

watch='''
// RAII cancellation watcher for a Workbench-owned job. No input after its marker.
struct WorkbenchCancelWatch(std::sync::Arc<std::sync::atomic::AtomicBool>);
impl WorkbenchCancelWatch {
    fn new(token: yas::cancel::CancelToken) -> Self {
        let done=Arc::new(AtomicBool::new(false));
        if let Some(path)=std::env::var_os("WORKBENCH_STOP_FILE") {
            let complete=done.clone();
            std::thread::spawn(move || {
                while !complete.load(Ordering::Relaxed) {
                    if std::path::Path::new(&path).exists() {token.cancel(yas::cancel::StopReason::UserAbort);break;}
                    std::thread::sleep(std::time::Duration::from_millis(20));
                }
            });
        }
        Self(done)
    }
}
impl Drop for WorkbenchCancelWatch {fn drop(&mut self){self.0.store(true,Ordering::Relaxed);}}
'''
with (ROOT/'genshin/src/server.rs').open('a',encoding='utf-8') as f:f.write(watch)
edit('genshin/src/server.rs','        let cancel_token = yas::cancel::CancelToken::new();',
     '        let cancel_token = yas::cancel::CancelToken::new();\n        let _workbench_cancel_watch=WorkbenchCancelWatch::new(cancel_token.clone());')

control='genshin/src/scanner/common/game_controller.rs'
for signature in ('pub fn click_at(&mut self, base_x: f64, base_y: f64)',
                  'pub fn move_to(&mut self, base_x: f64, base_y: f64)',
                  'pub fn key_press(&mut self, key: enigo::Key)',
                  'pub fn mouse_scroll(&mut self, amount: i32)',
                  'pub fn mouse_scroll_wheel_delta(&mut self, delta: i32)'):
    edit(control,signature+' {',signature+' {\n        if self.cancel.is_cancelled() { return; }')
edit(control,'        self.system_control.mouse_click().unwrap();','        if self.cancel.is_cancelled() { return; }\n        self.system_control.mouse_click().unwrap();')

edit('genshin/src/manager/models.rs','pub struct EquipRequest {\n    pub equip: Vec<EquipInstruction>,',
     '''pub struct EquipRequest {
    #[serde(default, rename="verifyOnly")]
    pub verify_only: bool,
    #[serde(default, rename="preflightOnly")]
    pub preflight_only: bool,
    #[serde(default, rename="allowBorrow")]
    pub allow_borrow: bool,
    pub equip: Vec<EquipInstruction>,''')
edit('genshin/src/manager/equip_manager.rs','    pub target_location: String,','    pub target_location: String,\n    pub verify_only: bool,\n    pub preflight_only: bool,')
edit('genshin/src/manager/orchestrator.rs','                target_location: instr.location.clone(),',
     '                target_location: instr.location.clone(),\n                verify_only: request.verify_only,\n                preflight_only: request.preflight_only,')
edit('genshin/src/server.rs','''        self.manager
            .execute_equip(&mut self.ctrl, request, progress_fn, cancel_token)''','''        std::env::set_var("WORKBENCH_ALLOW_BORROW",if request.allow_borrow {"1"} else {"0"});
        self.manager.execute_equip(&mut self.ctrl, request, progress_fn, cancel_token)''')

ui='genshin/src/manager/ui_actions.rs'
# Candidate matching and final verification share the same complete-frame parser.
p=ROOT/ui;s=p.read_text(encoding='utf-8');start=s.index('fn full_match_detail_panel(');end=s.index('\n}\n',start)+3
s=s[:start]+'''fn full_match_detail_panel(ctrl: &GenshinGameController,target: &GoodArtifact,
    ocr: &dyn ImageToText<RgbImage>,mappings: &MappingManager,_tag: &str) -> Result<bool> {
    let panel=ctrl.capture_region(SEL_PANEL_X,SEL_PANEL_Y,SEL_PANEL_W,SEL_PANEL_H)?;
    Ok(full_match_from_panel_verbose(&panel,target,ocr,mappings)?.0==MatchVerdict::Match)
}
'''+s[end:];p.write_text(s,encoding='utf-8')
edit(ui,'const VALUE_TOLERANCE: f64 = 0.1;','const VALUE_TOLERANCE: f64 = 0.0001;')
edit(ui,'    // 3. Main stat (slot-aware: flower=hp, plume=atk, others use fixup)',
     '    if level < 0 { return Ok((MatchVerdict::DirtyReject, details)); }\n\n    // 3. Main stat (slot-aware: flower=hp, plume=atk, others use fixup)')
edit(ui,'            let _ = writeln!(details, "=> (parse failed, skipping check)");',
     '            return Ok((MatchVerdict::DirtyReject, details));')
# The two empty label branches (main and set) must both reject, never skip.
p=ROOT/ui;s=p.read_text(encoding='utf-8');s=s.replace('        let _ = writeln!(details, "=> (empty)");','        return Ok((MatchVerdict::DirtyReject, details));');p.write_text(s,encoding='utf-8')
edit(ui,'            let _ = writeln!(details, "=> (no match, skipping)");','            return Ok((MatchVerdict::DirtyReject, details));')
edit(ui,'        let text = text.replace(\':\', ".");','''        let strict=regex::Regex::new(r"^\\s*(生命值|攻击力|防御力|元素精通|元素充能效率|暴击率|暴击伤害)\\s*\\+?\\s*\\d+(?:\\.\\d)?\\s*%?\\s*$")?;
        if !strict.is_match(&text) {return Ok((MatchVerdict::DirtyReject, details));}''')
edit(ui,'        if let Some(parsed) = stat_parser::parse_stat_from_text(&text) {\n            // Truncate',
     '        if let Some(parsed) = stat_parser::parse_stat_from_text(&text) {\n            if parsed.key.ends_with(\'_\') != text.trim_end().ends_with(\'%\') {return Ok((MatchVerdict::DirtyReject, details));}\n            // Truncate')

# One capture supplies both the full item identity and the equipped-owner label.
addition='''
fn workbench_crop(frame:&RgbImage,rect:(f64,f64,f64,f64))->RgbImage {
    let sx=frame.width() as f64/1920.0;let sy=frame.height() as f64/1080.0;
    image::imageops::crop_imm(frame,(rect.0*sx).round() as u32,(rect.1*sy).round() as u32,
                           (rect.2*sx).round() as u32,(rect.3*sy).round() as u32).to_image()
}
pub fn workbench_verify_equipped(ctrl:&GenshinGameController,target:&GoodArtifact,owner:&str,
    ocr:&dyn ImageToText<RgbImage>,mappings:&MappingManager,save_failure:bool)->Result<bool> {
    for attempt in 0..3 {
        let frame=ctrl.capture_game()?;
        let panel=workbench_crop(&frame,(SEL_PANEL_X,SEL_PANEL_Y,SEL_PANEL_W,SEL_PANEL_H));
        let (verdict,details)=full_match_from_panel_verbose(&panel,target,ocr,mappings)?;
        let owner_img=min_channel_preprocess(&workbench_crop(&frame,SEL_EQUIP_OWNER_RECT));
        let raw=ocr.image_to_text(&owner_img,false)?;
        let text:String=raw.chars().filter(|c|!c.is_whitespace()).collect();
        let exact_owner=mappings.character_name_map.iter().any(|(zh,key)|key==owner && text.contains(zh));
        if verdict==MatchVerdict::Match && exact_owner {
            let dir=std::path::Path::new("receipts");std::fs::create_dir_all(dir)?;
            let record=serde_json::json!({"jobId":std::env::var("WORKBENCH_ACTIVE_JOB").unwrap_or_default(),
                "expected":target,"ownerKey":owner,"ownerRaw":raw,"matchDetails":details,"sameFrame":true});
            std::fs::write(dir.join(format!("verify-{}.json",uuid::Uuid::new_v4())),serde_json::to_vec_pretty(&record)?)?;
            return Ok(true);
        }
        if attempt==2 && save_failure {
            let dir=std::path::Path::new("failures");let _=std::fs::create_dir_all(dir);
            let id=uuid::Uuid::new_v4().to_string();let _=frame.save(dir.join(format!("verify-{}.png",id)));
            let _=std::fs::write(dir.join(format!("verify-{}.txt",id)),format!("{}\\nowner={}",details,raw));
        }
        yas::utils::sleep(150);
    }
    Ok(false)
}
'''
with (ROOT/ui).open('a',encoding='utf-8') as f:f.write(addition)
edit('genshin/src/manager/equip_manager.rs','            // Check if the currently equipped artifact already matches (live OCR check)',
     '''            if target.preflight_only {
                let found=ui_actions::find_artifact_in_grid(ctrl,&target.artifact,ocr,&self.mappings,false).unwrap_or(false);
                let outcome=if found {InstructionResult::outcome(target.result_id.clone(),InstructionStatus::Success)}
                    else {InstructionResult::failure(target.result_id.clone(),InstructionStatus::NotFound,
                        "预检没有找到完整匹配的物品；没有执行换装", "Preflight did not find an exact item; no equip click",None)};
                results.insert(target.result_id.clone(),outcome);continue;
            }
            if target.verify_only {
                let verified=if ui_actions::workbench_verify_equipped(ctrl,&target.artifact,&target.target_location,ocr,&self.mappings,false).unwrap_or(false) {true}
                    else if ui_actions::find_artifact_in_grid(ctrl,&target.artifact,ocr,&self.mappings,false).unwrap_or(false) {
                        ui_actions::workbench_verify_equipped(ctrl,&target.artifact,&target.target_location,ocr,&self.mappings,true).unwrap_or(false)
                    } else {false};
                let outcome=if verified {InstructionResult::outcome(target.result_id.clone(),InstructionStatus::AlreadyCorrect)}
                    else {InstructionResult::failure(target.result_id.clone(),InstructionStatus::UiError,
                        "装备复核未通过；没有重新点击装备", "Equipment verification failed; no equip click was performed",None)};
                results.insert(target.result_id.clone(),outcome);continue;
            }
            // Check if the currently equipped artifact already matches (live OCR check)''')

# Do not click an unrecognized confirmation, and honor no-borrow mode at runtime.
edit(ui,'''        // Confirm dialog if artifact is on another character ("替换" case)
        ctrl.click_at(SEL_CONFIRM_BUTTON_X, SEL_CONFIRM_BUTTON_Y);
        yas::utils::sleep(d_action() * 5 / 8);''','''        let frame=ctrl.capture_game()?;
        let read=|rect| -> Result<String> {
            let im=min_channel_preprocess(&workbench_crop(&frame,rect));
            Ok(ocr.image_to_text(&im,false)?.chars().filter(|c|!c.is_whitespace()).collect())
        };
        let confirm=read((1030.0,728.0,300.0,56.0))?;
        let cancel=read((620.0,728.0,270.0,56.0))?;
        if confirm=="确认" && cancel=="取消" {
            let body=read((510.0,435.0,900.0,50.0))?+&read((510.0,480.0,900.0,50.0))?;
            if !body.contains("装备") {bail!("Unrecognized equipment confirmation");}
            if std::env::var("WORKBENCH_ALLOW_BORROW").as_deref()!=Ok("1") {
                ctrl.click_at(760.0,755.0);yas::utils::sleep(d_action());bail!("Borrowing is disabled");
            }
            ctrl.click_at(SEL_CONFIRM_BUTTON_X, SEL_CONFIRM_BUTTON_Y);
            yas::utils::sleep(d_action() * 5 / 8);
        }''')

with (ROOT/'docs/MANAGER_API.md').open('a',encoding='utf-8') as f:
    f.write('\n## Artifact Workbench adapter\nThe separate workbench_goodscanner binary requires a local bearer token, serves GET /workbench, watches the stop marker, scans five-star artifacts, and accepts verifyOnly/allowBorrow on /equip. verifyOnly performs complete-frame attribute and owner verification without equip clicks. No /manage client is exposed by Workbench. Mapping data is bundled for offline startup.\n')
edit('genshin/src/server.rs','        let cancel_token = yas::cancel::CancelToken::new();',
     '        std::env::set_var("WORKBENCH_ACTIVE_JOB",&job_id);\n        let cancel_token = yas::cancel::CancelToken::new();')
edit(ui,'if let Some(ocr_set_key) = fuzzy_match_map(cleaned, &mappings.artifact_set_map) {',
     'let label:String=cleaned.split([\':\',\'：\',\'(\',\'（\']).next().unwrap_or(\"\").chars().filter(|c|!c.is_whitespace()).collect();\n        if let Some(ocr_set_key) = mappings.artifact_set_map.get(&label).cloned() {')

# Clear all artifact filter conditions before an all-inventory scan; then re-read count.
reset = r"""
pub fn workbench_reset_artifact_filters(ctrl:&mut GenshinGameController,ocr:&dyn ImageToText<RgbImage>)->Result<()> {
    ctrl.click_at(167.0,1018.0);yas::utils::sleep(450);
    let labels=|frame:&RgbImage| -> Result<(String,String,String)> {
        let read=|rect| -> Result<String> {
            let image=min_channel_preprocess(&workbench_crop(frame,rect));
            Ok(ocr.image_to_text(&image,false)?.chars().filter(|c|!c.is_whitespace()).collect())
        };
        Ok((read((20.0,22.0,230.0,50.0))?,read((135.0,992.0,105.0,53.0))?,read((437.0,992.0,105.0,53.0))?))
    };
    let (heading,reset,apply)=labels(&ctrl.capture_game()?)?;
    anyhow::ensure!(heading=="圣遗物筛选" && reset=="重置" && apply=="确认","Artifact filter controls not verified");
    ctrl.click_at(165.0,1020.0);yas::utils::sleep(200);
    let (heading,_,apply)=labels(&ctrl.capture_game()?)?;
    anyhow::ensure!(heading=="圣遗物筛选" && apply=="确认","Artifact filter page changed");
    ctrl.click_at(480.0,1020.0);yas::utils::sleep(350);Ok(())
}
"""
with (ROOT/ui).open('a',encoding='utf-8') as f:f.write(reset)
edit('genshin/src/scanner/artifact/scanner.rs','        // Return count OCR model to pool before scan loop',"""        let total_count=if std::env::var_os("WORKBENCH_GOOD_TOKEN").is_some() && !self.config.keep_five_star_filter {
            crate::manager::ui_actions::workbench_reset_artifact_filters(ctrl,&count_ocr_guard)?;
            backpack_scanner::select_tab_and_read_count(ctrl,"artifact",self.config.delay_tab,&count_ocr_guard,false,self.config.dump_images)?.0
        } else {total_count};
        // Return count OCR model to pool before scan loop""")
# A level-sorted inventory interleaves rarities. Low rarity is a skipped item,
# never evidence that every remaining item is low rarity.
scan_file='genshin/src/scanner/artifact/scanner.rs'
edit(scan_file,'        bp.scan_grid(total, &scan_config, start_at, |ctrl, event| {', '''        let mut seen=0usize;
        let mut five_star=0usize;
        let mut lower_star=0usize;
        let mut unknown=0usize;
        let outcome=bp.scan_grid(total, &scan_config, start_at, |ctrl, event| {''')
edit(scan_file,'''                    // Quick rarity check on main thread to stop early.
                    if pixel_utils::artifact_below_min_rarity(
                        &frame,
                        &scaler,
                        self.config.min_rarity,
                    ) {
                        voter.finish_additional_passes(&scaler, || ctrl.capture_game().ok());
                        let ready = voter.early_stop_flush();
                        let _ = emit_ready(ready, &item_tx);
                        return ScanAction::Stop;
                    }''', '''                    seen+=1;
                    match pixel_utils::detect_artifact_rarity_evidence(&frame,&scaler) {
                        Some(5)=>five_star+=1,
                        Some(_)=>lower_star+=1,
                        None=>{
                            unknown+=1;
                            let _=std::fs::create_dir_all("failures");
                            let _=frame.image.save(format!("failures/scan-{}-rarity-{}.png",
                                std::env::var("WORKBENCH_ACTIVE_JOB").unwrap_or_default(),idx));
                        },
                    }
                    // Send every grid index to the shared worker. It cheaply skips
                    // known low rarities without leaving holes in its ordered queue.''')
edit(scan_file,'        // Write index map for debug image correlation (output position → folder name)', '''        if std::env::var_os("WORKBENCH_GOOD_TOKEN").is_some() {
            let complete=outcome.termination==backpack_scanner::ScanTermination::Exhausted
                && start_at==0 && self.config.max_count==0 && seen==total
                && outcome.missed_count==0 && outcome.skipped_count==0
                && unknown==0 && five_star+lower_star==seen && artifacts.len()==five_star;
            let audit=serde_json::json!({"jobId":std::env::var("WORKBENCH_ACTIVE_JOB").unwrap_or_default(),
                "complete":complete,"expected":total,"visited":seen,"five_star":five_star,
                "skipped_lower_rarity":lower_star,"unknown_rarity":unknown,"accepted":artifacts.len(),
                "missed":outcome.missed_count,"skipped_positions":outcome.skipped_count,
                "termination":format!("{:?}",outcome.termination)});
            std::fs::create_dir_all("receipts")?;
            std::fs::write(format!("receipts/scan-{}.json",std::env::var("WORKBENCH_ACTIVE_JOB").unwrap_or_default()),serde_json::to_vec_pretty(&audit)?)?;
            anyhow::ensure!(complete,"Incomplete artifact inventory: {}",audit);
        }
        // Write index map for debug image correlation (output position → folder name)''')
edit(scan_file,'                        Err(e) => {\n                            annotator::finalize_error(None, &e.to_string());','                        Err(e) => {\n                            let _=std::fs::create_dir_all("failures");\n                            let _=work_item.frame.image.save(format!("failures/scan-{}-ocr-{}.png",\n                                std::env::var("WORKBENCH_ACTIVE_JOB").unwrap_or_default(),work_item.index));\n                            annotator::finalize_error(None, &e.to_string());')

# Bounded diagnostics: retain sampled frames in memory, write only on failure.
edit(ui,'    find_artifact_in_grid_inner(ctrl, target, ocr, mappings, equip, None, false)', '''    let mut samples=Vec::new();
    let result=find_artifact_in_grid_inner(ctrl,target,ocr,mappings,equip,Some(&mut samples),false);
    if !matches!(result,Ok(true)) && std::env::var_os("WORKBENCH_GOOD_TOKEN").is_some() {
        let dir=std::path::PathBuf::from("failures").join(format!("find-{}-{}",target.slot_key,uuid::Uuid::new_v4()));
        let _=std::fs::create_dir_all(&dir);
        if let Ok(frame)=ctrl.capture_game() {let _=frame.save(dir.join("screen.png"));}
        let _=std::fs::write(dir.join("target.json"),serde_json::to_vec_pretty(target).unwrap_or_default());
        for (i,sample) in samples.iter().take(12).enumerate() {
            let _=sample.panel_image.save(dir.join(format!("panel-{i}.png")));
            let _=std::fs::write(dir.join(format!("panel-{i}.txt")),format!("page={} row={} col={} level_raw={}\n{}",sample.page,sample.row,sample.col,sample.level_text,sample.ocr_details));
        }
    }
    result''')
edit(ui,'        total_checked += cells_checked.load(Ordering::SeqCst);',
     '        total_checked += cells_checked.load(Ordering::SeqCst);\n        if let Some(ref mut out)=debug_out {if out.len()>40 {out.truncate(40);}}')
edit('genshin/src/manager/equip_manager.rs','            if target.preflight_only {',
     '            log_info!("[workbench] slot={} preflight={} verify={}","[workbench] slot={} preflight={} verify={}",target.artifact.slot_key,target.preflight_only,target.verify_only);\n            if target.preflight_only {')
edit('genshin/src/manager/equip_manager.rs','                results.insert(target.result_id.clone(),outcome);continue;\n            }\n            if target.verify_only {',
     '                results.insert(target.result_id.clone(),outcome);if !found {break;} continue;\n            }\n            if target.verify_only {')


# Display bullet markers are formatting, not part of the numeric stat value.
edit(ui,'^\\s*(生命值|攻击力|防御力|元素精通|元素充能效率|暴击率|暴击伤害)',
     '^[\\s·•・]*(生命值|攻击力|防御力|元素精通|元素充能效率|暴击率|暴击伤害)')
edit(ui,'        if !strict.is_match(&text) {return Ok((MatchVerdict::DirtyReject, details));}',
     '        let _=writeln!(details,"sub{} raw={:?}",idx,text);\n        if !strict.is_match(&text) {return Ok((MatchVerdict::DirtyReject, details));}')
edit(ui,'const SEL_LEVEL_RECT: (f64, f64, f64, f64) = (1443.0, 310.0, 100.0, 26.0);',
     'const SEL_LEVEL_RECT: (f64, f64, f64, f64) = (1460.0, 310.0, 78.0, 26.0);')
for y in (349,383,417):
    edit(ui,f'(1460.0, {y}.0, 256.0, 30.0)',f'(1485.0, {y}.0, 310.0, 30.0)')
edit(ui,'(1460.0, 451.0, 336.0, 30.0)','(1485.0, 451.0, 310.0, 30.0)')
# Include slot label in the same capture used for complete identity verification.
edit(ui,'const SEL_PANEL_Y: f64 = 210.0;','const SEL_PANEL_Y: f64 = 170.0;')
edit(ui,'const SEL_PANEL_H: f64 = 320.0;','const SEL_PANEL_H: f64 = 360.0;')
edit(ui,'    // 1. Rarity (pixel check)', '''    let slot_img=crop_from_panel(panel,(1460.0,174.0,250.0,30.0));
    let slot_raw=ocr.image_to_text(&slot_img,false).unwrap_or_default();
    let slot:String=slot_raw.chars().filter(|c|!c.is_whitespace()).collect();
    let expected=match target.slot_key.as_str() {"flower"=>"生之花","plume"=>"死之羽","sands"=>"时之沙","goblet"=>"空之杯","circlet"=>"理之冠",_=>"INVALID"};
    let _=writeln!(details,"slot: OCR={:?} expected={}",slot_raw,expected);
    if slot!=expected {return Ok((MatchVerdict::DirtyReject,details));}
    // 1. Rarity (pixel check)''')
edit(ui,'            "flower" => "hp".to_string(),\n            "plume" => "atk".to_string(),',
     '            "flower" | "plume" => raw_key.to_string(),')
# Offline replay is the production parser, without any game/controller construction.
replay='''
pub fn workbench_replay_selection(frame:&RgbImage,target:&GoodArtifact,ocr:&dyn ImageToText<RgbImage>,mappings:&MappingManager)->Result<serde_json::Value> {
    let panel=workbench_crop(frame,(SEL_PANEL_X,SEL_PANEL_Y,SEL_PANEL_W,SEL_PANEL_H));
    let (verdict,details)=full_match_from_panel_verbose(&panel,target,ocr,mappings)?;
    let raw_subs:Vec<String>=SEL_SUB_RECTS.iter().map(|r|ocr.image_to_text(&crop_from_panel(&panel,*r),false).unwrap_or_default()).collect();
    Ok(serde_json::json!({"matched":verdict==MatchVerdict::Match,"details":details,"raw_subs":raw_subs}))
}
'''
with (ROOT/ui).open('a',encoding='utf-8') as f:f.write(replay)


# Fixed UI labels may carry recognized decoration; numeric strings remain strict.
edit(ui,'    if slot!=expected {return Ok((MatchVerdict::DirtyReject,details));}',
     '    let slot=slot.trim_end_matches(|c:char|"。．·.:：，,".contains(c));\n    if slot!=expected {return Ok((MatchVerdict::DirtyReject,details));}')
# Names and artifact numbers use their respective upstream OCR pools.
edit('genshin/src/manager/equip_manager.rs','''        ocr: &dyn yas::ocr::ImageToText<image::RgbImage>,
        _scaler: &CoordScaler,
        results: &mut HashMap<String, InstructionResult>,
    ) {
        // Click 圣遗物 menu''','''        character_ocr: &dyn yas::ocr::ImageToText<image::RgbImage>,
        _scaler: &CoordScaler,
        results: &mut HashMap<String, InstructionResult>,
    ) {
        let artifact_ocr=self.pools.artifact().v4().get();
        let ocr:&dyn yas::ocr::ImageToText<image::RgbImage>=&artifact_ocr;
        // Click 圣遗物 menu''')
edit(ui,'ocr:&dyn ImageToText<RgbImage>,mappings:&MappingManager,save_failure:bool)->Result<bool>',
     'ocr:&dyn ImageToText<RgbImage>,owner_ocr:&dyn ImageToText<RgbImage>,mappings:&MappingManager,save_failure:bool)->Result<bool>')
edit(ui,'        let raw=ocr.image_to_text(&owner_img,false)?;','        let raw=owner_ocr.image_to_text(&owner_img,false)?;')
p=ROOT/'genshin/src/manager/equip_manager.rs';s=p.read_text(encoding='utf-8')
s=s.replace('&target.target_location,ocr,&self.mappings,','&target.target_location,ocr,character_ocr,&self.mappings,')
s=s.replace('ui_actions::read_selected_artifact_owner(ctrl, ocr, &self.mappings)','ui_actions::read_selected_artifact_owner(ctrl, character_ocr, &self.mappings)');p.write_text(s,encoding='utf-8')
# Recheck the actual selected item after asynchronous candidate navigation.
edit(ui,'            if equip {\n                if !click_equip_button_safe(ctrl, ocr, "grid_scan", page, row, col)? {',
     '            if !full_match_detail_panel(ctrl,target,ocr,mappings,"selected_candidate")? {bail!("Selected item identity changed after navigation");}\n            if equip {\n                if !click_equip_button_safe(ctrl, ocr, "grid_scan", page, row, col)? {')


# Several fixed crop widths from ONE frame: accept only unambiguous literal
# numeric OCR, never trim trailing numeric noise or reconstruct a value.
sub_reader='''
fn workbench_substat_text(panel:&RgbImage,rect:(f64,f64,f64,f64),ocr:&dyn ImageToText<RgbImage>,details:&mut String)->Result<String> {
    use std::fmt::Write;
    let strict=regex::Regex::new(r"^[\s·•・]*(生命值|攻击力|防御力|元素精通|元素充能效率|暴击率|暴击伤害)\s*\+?\s*\d+(?:\.\d)?\s*%?\s*$")?;
    let mut accepted:Vec<(String,f64,String)>=Vec::new();
    for width in [215.0,255.0,310.0] {
        let img=crop_from_panel(panel,(rect.0,rect.1,width,rect.3));
        let raw=ocr.image_to_text(&img,false).unwrap_or_default();
        let text=raw.trim();let _=writeln!(details,"crop {} raw={:?}",width,text);
        if !strict.is_match(text) {continue;}
        // A crop that intersects a bright glyph at its right edge is not a
        // complete numeric observation, even if its text looks plausible.
        let clipped=(img.width().saturating_sub(3)..img.width()).any(|x|
            (3..img.height().saturating_sub(3)).any(|y|img.get_pixel(x,y)[0]>210));
        if clipped {continue;}
        if let Some(parsed)=stat_parser::parse_stat_from_text(text) {
            if parsed.key.ends_with('_')!=text.ends_with('%') || (!parsed.key.ends_with('_') && parsed.value.fract().abs()>0.0001) {continue;}
            if accepted.iter().any(|(key,value,_)|key!=&parsed.key || (*value-parsed.value).abs()>0.0001) {
                let _=writeln!(details,"conflicting crop readings; rejecting numeric field");return Ok(String::new());
            }
            accepted.push((parsed.key,parsed.value,text.to_string()));
        }
    }
    Ok(accepted.into_iter().next().map(|(_,_,text)|text).unwrap_or_default())
}
'''
with (ROOT/ui).open('a',encoding='utf-8') as f:f.write(sub_reader)
edit(ui,'        let img = crop_from_panel(panel, *rect);\n        let text = ocr.image_to_text(&img, false).unwrap_or_default();\n        let text = text.trim().to_string();',
     '        let text=workbench_substat_text(panel,*rect,ocr,&mut details)?;')
edit(ui,'    let solver_input = SolverInput {\n        rarity,\n        level_candidates: vec![effective_level],',
     '    let observed=sub_candidates.clone();\n    let solver_input = SolverInput {\n        rarity,\n        level_candidates: vec![effective_level],')
edit(ui,'    // 6. Match solved substats against target', '''    if solved_subs.len()!=observed.len() || solved_subs.iter().any(|s| !observed.iter().flatten().any(|o|
        o.key==s.key && o.inactive==s.inactive && (o.value-s.value).abs()<0.0001)) {
        let _=writeln!(details,"solver changed observed numbers; rejecting");return Ok((MatchVerdict::DirtyReject,details));
    }
    // 6. Match solved substats against target''')


# A literal percent sign terminates the number. Decoration after that unit
# cannot change its digits; undelimited flat numbers remain strict.
edit(ui,'    for width in [215.0,255.0,310.0] {','    for width in [215.0,235.0,255.0,310.0] {')
edit(ui,'        let text=raw.trim();let _=writeln!(details,"crop {} raw={:?}",width,text);', '''        let raw=raw.trim();let _=writeln!(details,"crop {} raw={:?}",width,raw);
        let text=if let Some(end)=raw.find('%') {
            if raw[end+1..].chars().all(|c|c.is_whitespace() || "·•・。.．".contains(c)) {&raw[..end+1]} else {raw}
        } else {raw};''')
edit(ui,'''    let panel=ctrl.capture_region(SEL_PANEL_X,SEL_PANEL_Y,SEL_PANEL_W,SEL_PANEL_H)?;
    Ok(full_match_from_panel_verbose(&panel,target,ocr,mappings)?.0==MatchVerdict::Match)''','''    for attempt in 0..3 {
        let panel=ctrl.capture_region(SEL_PANEL_X,SEL_PANEL_Y,SEL_PANEL_W,SEL_PANEL_H)?;
        let (verdict,details)=full_match_from_panel_verbose(&panel,target,ocr,mappings)?;
        if verdict==MatchVerdict::Match {return Ok(true);}
        if verdict==MatchVerdict::CleanReject {return Ok(false);}
        if attempt==2 {log_warn!("[selected_recheck] {}","[selected_recheck] {}",details);}
        yas::utils::sleep(120);
    }
    Ok(false)''')
edit('genshin/src/manager/equip_manager.rs','                        remaining_chars -= 1;', '''                        for target in &slot_targets {
                            results.entry(target.result_id.clone()).or_insert_with(||InstructionResult::failure(
                                target.result_id.clone(),InstructionStatus::Skipped,
                                "前一部位预检失败，后续部位未执行", "Skipped after a previous slot failed preflight",None));
                        }
                        remaining_chars -= 1;''')


edit(ui,'    let slot=slot.trim_end_matches(|c:char|"。．·.:：，,".contains(c));',
     '    let slot:String=slot.chars().filter(|c|(\'\\u{4e00}\'..=\'\\u{9fff}\').contains(c)).collect();')
old=r'''    let strict=regex::Regex::new(r"^[\s·•・]*(生命值|攻击力|防御力|元素精通|元素充能效率|暴击率|暴击伤害)\s*\+?\s*\d+(?:\.\d)?\s*%?\s*$")?;'''
new=r'''    let strict=regex::Regex::new(r"^[\s·•・]*(?P<key>生命值|攻击力|防御力|元素精通|元素充能效率|暴击率|暴击伤害)\s*\+?\s*(?P<num>\d+(?:\.\d)?)\s*(?P<unit>%?)(?P<tail>[\s\p{P}\p{S}]*)$")?;'''
# Only the crop reader uses this grammar; the complete-frame parser still
# receives a strict key+unchanged numeric token+unit string.
p=ROOT/ui;s=p.read_text(encoding='utf-8');i=s.index('fn workbench_substat_text(');prefix=s[:i];tail=s[i:];assert tail.count(old)==1;tail=tail.replace(old,new)
a=tail.index('        let text=if let Some(end)=raw.find(\'%\')');b=tail.index('        // A crop',a)
tail=tail[:a]+'''        let Some(caps)=strict.captures(raw) else {continue;};
        let unit=&caps["unit"];let number=&caps["num"];
        if caps["tail"].contains('%') || (unit.is_empty() && number.contains('.')) {continue;}
        let text=format!("{}+{}{}",&caps["key"],number,unit);
'''+tail[b:]
tail=tail.replace('stat_parser::parse_stat_from_text(text)','stat_parser::parse_stat_from_text(&text)');p.write_text(prefix+tail,encoding='utf-8')
edit(ui,'''            let value = if parsed.key.ends_with('_') {
                (parsed.value * 10.0).trunc() / 10.0
            } else {
                parsed.value.trunc()
            };''','            let value = parsed.value;')


# Set names and slot labels share exact Chinese-label normalization. No fuzzy
# replacement is used to approve an item identity.
edit(ui,'let label:String=cleaned.split([\':\',\'：\',\'(\',\'（\']).next().unwrap_or("").chars().filter(|c|!c.is_whitespace()).collect();',
     'let label:String=cleaned.split([\':\',\'：\',\'(\',\'（\']).next().unwrap_or("").chars().filter(|c|(\'\\u{4e00}\'..=\'\\u{9fff}\').contains(c)).collect();')

print('GOODScanner adapter applied')

# Keep enhancement adapter reproducible on the same pinned upstream checkout.
import runpy
runpy.run_path(str(Path(__file__).with_name("patch_good_enhancement.py")), run_name="__main__")
