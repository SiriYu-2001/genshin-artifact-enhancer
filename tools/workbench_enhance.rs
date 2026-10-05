//! Workbench enhancement adapter. Uses GOODScanner's yas capture/OCR/input.
//! No optimizer, arbitrary-input endpoint, or five-star material bypass lives here.
use anyhow::{bail, ensure, Context, Result};
use image::RgbImage;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{collections::BTreeMap, path::Path, time::{Duration, Instant}};
use yas::ocr::ImageToText;
use crate::scanner::common::{game_controller::GenshinGameController, models::GoodArtifact};
use super::{models::*, orchestrator::ArtifactManager, ui_actions};

thread_local! {static STRICT_BAG:std::cell::Cell<bool>=const {std::cell::Cell::new(false)};}
thread_local! {
    // GOODScanner's bundled text model. Initialized only for failed labels,
    // reused on this executor thread, never used to guess or repair numbers.
    static TEXT_RESCUE:std::cell::RefCell<Option<Box<dyn ImageToText<RgbImage>+Send>>>=const {std::cell::RefCell::new(None)};
}
pub fn strict_bag_read()->bool {STRICT_BAG.with(|v|v.get())}
fn exact_scan<T>(f:impl FnOnce()->T)->T {
    struct Reset(bool);impl Drop for Reset{fn drop(&mut self){STRICT_BAG.with(|v|v.set(self.0));}}
    let old=STRICT_BAG.with(|v|v.replace(true));let _guard=Reset(old);f()
}
pub fn verify_bag_numbers(raw:&[Option<String>;4],active:&[crate::scanner::common::models::GoodSubStat],inactive:&[crate::scanner::common::models::GoodSubStat])->Result<()> {
    let re=regex::Regex::new(r"^[\s\p{P}\p{S}]*(生命值|攻击力|防御力|元素精通|元素充能效率|暴击率|暴击伤害)\s*\+\s*(\d+(?:,\d{3})*(?:\.\d)?)\s*(%?)\s*(?:[（(](?:未|待)激活[）)])?[\s\p{P}\p{S}]*$")?;
    let mut observed=BTreeMap::new();
    for text in raw {let text=text.as_ref().context("Missing original substat reading")?;let c=re.captures(text).context("Original substat numeric token unreadable")?;
        let key=stat_key(&c[1],&c[3]=="%").context("Unknown stat type/unit")?;
        ensure!(observed.insert(key.to_string(),(c[2].replace(',',"").parse::<f64>()?,text.contains("未激活")||text.contains("待激活"))).is_none(),"Repeated observed substat");
    }
    let solved:BTreeMap<_,_>=active.iter().map(|s|(s.key.clone(),(s.value,false))).chain(inactive.iter().map(|s|(s.key.clone(),(s.value,true)))).collect();
    ensure!(observed==solved,"Roll solver changed original observed numbers; refusing target identity");Ok(())
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all="camelCase", deny_unknown_fields)]
pub struct Request {
    pub operation_id: String,
    pub action: String,
    pub artifact: GoodArtifact,
    pub name: String,
    #[serde(default="ordinary_source")]
    pub source_kind: String,
}
fn ordinary_source()->String {"ordinary".into()}
fn journal_root()->std::path::PathBuf {std::env::var_os("WORKBENCH_ENHANCE_DIR").map(std::path::PathBuf::from).unwrap_or_else(||"enhancement".into())}

pub fn game_window_state()->Value {
    #[cfg(windows)] {
        let handles:Vec<_>=yas::utils::iterate_window().into_iter().filter(|&h|{
            if !matches!(yas::utils::get_window_title(h).as_deref(),Some("原神")|Some("Genshin Impact")){return false;}
            let mut name=[0u16;256];let n=unsafe{windows_sys::Win32::UI::WindowsAndMessaging::GetClassNameW(h as _,name.as_mut_ptr(),256)};
            n>0 && matches!(String::from_utf16_lossy(&name[..n as usize]).as_str(),"UnityWndClass"|"Qt5152QWindowIcon")
        }).collect();
        if handles.len()!=1{return json!({"found":false,"count":handles.len(),"supported":false});}
        if let Ok(rect)=yas::utils::get_client_rect(handles[0]) {
            let foreground=unsafe{windows_sys::Win32::UI::WindowsAndMessaging::GetForegroundWindow()} as usize==handles[0] as usize;
            return json!({"found":true,"width":rect.width,"height":rect.height,"foreground":foreground,"supported":rect.width==1920 && rect.height==1080});
        }
    }
    json!({"found":false,"supported":false})
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all="camelCase")]
pub struct Observation {
    pub level: i32,
    pub exp: Option<u32>,
    pub next_exp: Option<u32>,
    pub material_count: u32,
    pub title: String,
    pub main_label: String,
    pub stats: BTreeMap<String, f64>,
    pub stage_add: bool,
    pub low_rarity_filter: bool,
}

fn cn(s:&str)->String {s.chars().filter(|c| ('\u{4e00}'..='\u{9fff}').contains(c)).collect()}
fn crop(frame:&RgbImage,r:(u32,u32,u32,u32))->RgbImage {
    image::imageops::crop_imm(frame,r.0,r.1,r.2,r.3).to_image()
}
fn text(frame:&RgbImage,r:(u32,u32,u32,u32),ocr:&dyn ImageToText<RgbImage>)->Result<String> {
    ocr.image_to_text(&crop(frame,r),false).map(|s|s.trim().to_string())
}
fn gray_text(frame:&RgbImage,r:(u32,u32,u32,u32),ocr:&dyn ImageToText<RgbImage>)->Result<String> {
    let mut im=crop(frame,r);
    // GOODScanner's min-channel grayscale suppresses colored panel backgrounds.
    for p in im.pixels_mut(){let v=*p.0.iter().min().unwrap();*p=image::Rgb([v,v,v]);}
    ocr.image_to_text(&im,false).map(|v|v.trim().to_string())
}
fn rescue_text(frame:&RgbImage,r:(u32,u32,u32,u32))->Result<String> {
    log::debug!("[enhance_ocr] same-frame PP-OCRv5 text fallback at {:?}",r);
    TEXT_RESCUE.with(|slot|{
        let mut model=slot.borrow_mut();
        if model.is_none(){*model=Some(crate::scanner::common::ocr_factory::create_ocr_model("ppocrv5")?);}
        model.as_ref().unwrap().image_to_text(&crop(frame,r),false).map(|s|s.trim().to_string())
    })
}
fn numeric(frame:&RgbImage,r:(u32,u32,u32,u32),ocr:&dyn ImageToText<RgbImage>)->Result<Vec<String>> {
    let im=crop(frame,r);
    // Same-frame candidates only. GOODScanner also upscales short number crops.
    let doubled=image::imageops::resize(&im,im.width()*2,im.height()*2,image::imageops::FilterType::CatmullRom);
    Ok(vec![ocr.image_to_text(&im,false)?,ocr.image_to_text(&doubled,false)?])
}
fn level_glyphs(frame:&RgbImage)->RgbImage {
    // Integer level only: preserve connected full-height +/digit glyphs and
    // remove tiny particle dots. Never use this on decimal substat numbers.
    let im=crop(frame,(1170,126,77,51));let w=im.width() as usize;let h=im.height() as usize;
    let mut ink:Vec<bool>=im.pixels().map(|p|{let lo=*p.0.iter().min().unwrap();let hi=*p.0.iter().max().unwrap();lo>210 && hi-lo<45}).collect();
    let mut cleaned=RgbImage::new(w as u32,h as u32);
    for start in 0..ink.len() {
        if !ink[start]{continue;}ink[start]=false;
        let mut todo=vec![start];let mut component=vec![start];let mut min_y=start/w;let mut max_y=min_y;
        while let Some(index)=todo.pop(){
            let (x,y)=(index%w,index/w);
            for dy in -1isize..=1 {for dx in -1isize..=1 {
                let (nx,ny)=(x as isize+dx,y as isize+dy);
                if nx<0 || ny<0 || nx>=w as isize || ny>=h as isize{continue;}
                let n=ny as usize*w+nx as usize;
                if ink[n]{ink[n]=false;todo.push(n);component.push(n);min_y=min_y.min(n/w);max_y=max_y.max(n/w);}
            }}
        }
        if component.len()>=14 && max_y-min_y+1>=9 {
            for n in component {cleaned.put_pixel((n%w) as u32,(n/w) as u32,image::Rgb([255,255,255]));}
        }
    }
    cleaned
}
fn substat_number(frame:&RgbImage,y:u32,ocr:&dyn ImageToText<RgbImage>,dim:bool)->Result<Vec<String>> {
    let im=crop(frame,(1760,y,125,43));
    // Locate the right-aligned white glyph run; tiny animated sparkles must not
    // become a leading decimal point. Geometry is independent of expected value.
    let ink:Vec<usize>=(0..im.width()).map(|x|(2..im.height()-2).filter(|&z|{
        let p=im.get_pixel(x,z);let lo=*p.0.iter().min().unwrap();let hi=*p.0.iter().max().unwrap();lo>if dim {105}else{170} && hi-lo<65
    }).count()).collect();
    let end=(0..116).rev().find(|&x|ink[x]>=5).context("No numeric glyphs")?;
    let mut start=end;let mut gap=0;
    for x in (0..end).rev(){if ink[x]>=5{start=x;gap=0;}else{gap+=1;if gap>20{break;}}}
    ensure!(end-start>=5,"Numeric glyph run too short");
    let x=start.saturating_sub(3) as u32;let width=((end+5).min(125) as u32)-x;
    let mut readings=numeric(frame,(1760+x,y,width,43),ocr)?;
    // Independent wider same-frame read prevents a tight crop from turning
    // 6.2 into 2, even when both scales of the cropped image agree.
    readings.extend(numeric(frame,(1785,y,100,43),ocr)?);
    Ok(readings)
}
fn consensus<T:PartialEq+Clone>(values:Vec<T>,field:&str)->Result<T> {
    ensure!(!values.is_empty(),"Unreadable {field}");
    ensure!(values.iter().all(|v|v==&values[0]),"Conflicting same-frame {field}");
    Ok(values[0].clone())
}
fn parse_level(s:&str)->Option<i32> {
    let re=regex::Regex::new(r"^\s*\+\s*(\d{1,2})\s*$").unwrap();
    re.captures(s).and_then(|c|c[1].parse().ok()).filter(|n| *n<=20)
}
fn count(s:&str)->Option<u32> {
    let re=regex::Regex::new(r"^\s*装备强化消耗\s*[（(]\s*(\d{1,2})\s*/\s*15\s*[）)]\s*$").unwrap();
    re.captures(s).and_then(|c|c[1].parse().ok()).filter(|n|*n<=15)
}
fn value(s:&str)->Option<(f64,bool)> {
    // Decoration may be discarded; digits, decimal points and units never repaired.
    let re=regex::Regex::new(r"^\s*[·•・]*\s*(\d+(?:,\d{3})*(?:\.\d)?)\s*(%?)\s*↑?\s*$").unwrap();
    let c=re.captures(s)?;
    Some((c[1].replace(',',"").parse().ok()?, &c[2]=="%"))
}
fn stat_key(label:&str,percent:bool)->Option<&'static str> {
    Some(match (cn(label).replace("未激活", "").replace("待激活", "").as_str(),percent) {
        ("暴击率",true)=>"critRate_",("暴击伤害",true)=>"critDMG_",
        ("元素充能效率",true)=>"enerRech_",("元素精通",false)=>"eleMas",
        ("攻击力",true)=>"atk_",("攻击力",false)=>"atk",
        ("生命值",true)=>"hp_",("生命值",false)=>"hp",
        ("防御力",true)=>"def_",("防御力",false)=>"def",_=>return None,
    })
}

/// Entire authoritative observation is obtained from this one frame.
pub fn observe_frame(frame:&RgbImage,ocr:&dyn ImageToText<RgbImage>,expected:&Request)->Result<Observation> {
    ensure!(frame.dimensions()==(1920,1080),"Enhancement requires calibrated 1920x1080");
    let mut title=text(frame,(138,24,620,45),ocr)?;
    let slot=match expected.artifact.slot_key.as_str(){"flower"=>"生之花","plume"=>"死之羽","sands"=>"时之沙","goblet"=>"空之杯","circlet"=>"理之冠",_=>bail!("Unknown slot")};
    if cn(&title)!=format!("{}{}",slot,cn(&expected.name)){title=gray_text(frame,(138,24,620,45),ocr)?;}
    if cn(&title)!=format!("{}{}",slot,cn(&expected.name)) && ocr.model_id()!=Some("ppocrv5") {
        title=rescue_text(frame,(138,24,620,45))?;
    }
    ensure!(cn(&title)==format!("{}{}",slot,cn(&expected.name)),"Enhancement title mismatch: {title:?}");
    // Geometric separation is essential: gold +N starts at x=1254.
    // Never OCR the current and preview values in one region.
    let mut level_raw=numeric(frame,(1170,126,77,51),ocr)?;
    if !level_raw.iter().any(|s|parse_level(s).is_some()) {
        level_raw.push(gray_text(frame,(1170,126,77,51),ocr)?);
    }
    if !level_raw.iter().any(|s|parse_level(s).is_some()) {
        level_raw.push(ocr.image_to_text(&level_glyphs(frame),false)?);
    }
    let level=consensus(level_raw.iter().filter_map(|s|parse_level(s)).collect(),"current level")
        .with_context(||format!("Current level OCR: {level_raw:?}"))?;
    let mut main_label=cn(&text(frame,(1200,237,245,39),ocr)?);
    let expected_main=match expected.artifact.main_stat_key.as_str(){"hp"|"hp_"=>"生命值","atk"|"atk_"=>"攻击力","def_"=>"防御力","eleMas"=>"元素精通","enerRech_"=>"元素充能效率","critRate_"=>"暴击率","critDMG_"=>"暴击伤害","heal_"=>"治疗加成","pyro_dmg_"=>"火元素伤害加成","hydro_dmg_"=>"水元素伤害加成","anemo_dmg_"=>"风元素伤害加成","electro_dmg_"=>"雷元素伤害加成","dendro_dmg_"=>"草元素伤害加成","cryo_dmg_"=>"冰元素伤害加成","geo_dmg_"=>"岩元素伤害加成","physical_dmg_"=>"物理伤害加成",_=>bail!("Unknown main stat")};
    if main_label!=expected_main{main_label=cn(&gray_text(frame,(1200,237,245,39),ocr)?);}
    if main_label!=expected_main && ocr.model_id()!=Some("ppocrv5") {main_label=cn(&rescue_text(frame,(1200,237,245,39))?);}
    ensure!(main_label==expected_main,"Main stat label mismatch");
    let (material_count,stage_add,low_rarity_filter)=if level==20 {(0,false,false)} else {
        ensure!(cn(&text(frame,(1690,994,132,45),ocr)?)=="强化","Not enhancement page");
        let counts=[(1410,688,270,51),(1390,692,319,44)].into_iter().map(|r|text(frame,r,ocr)).collect::<Result<Vec<_>>>()?;
        ensure!(counts.iter().any(|s|cn(s)=="装备强化消耗"),"Material count label unreadable");
        // Read the fraction separately: full-line OCR can collapse 11/15 to 1/15.
        let fraction=regex::Regex::new(r"^\s*(?:耗)?\s*[（(]?\s*(\d{1,2})\s*/\s*15\s*[）)]?\s*$")?;
        let mut candidates=Vec::new();
        for rect in [(1547,697,90,36),(1553,696,92,38)] {
            for raw in numeric(frame,rect,ocr)? {if let Some(c)=fraction.captures(&raw){if let Ok(n)=c[1].parse::<u32>(){if n<=15{candidates.push(n);}}}}
        }
        let n=consensus(candidates,"material count fraction")?;
        let filter=text(frame,(1176,745,365,48),ocr)?.replace(' ',"");
        (n,cn(&text(frame,(1660,745,210,48),ocr)?)=="阶段放入",filter=="4星及以下素材")
    };
    let exp_re=regex::Regex::new(r"^\s*(\d+)\s*/\s*(\d+)\s*$")?;
    let mut exp_values=Vec::new();
    if level<20 {for rect in [(1710,138,171,34),(1695,136,185,36),(1720,132,162,48)] {for raw in numeric(frame,rect,ocr)? {if let Some(c)=exp_re.captures(&raw){let a:u32=c[1].parse()?;let b:u32=c[2].parse()?;if a<b {exp_values.push((a,b));}}}}}
    // Unknown EXP is not fabricated. A level increase still proves progress;
    // when the level stays unchanged, reconciliation requires known EXP growth.
    let exp_pair=if level==20 {None} else {consensus(exp_values,"experience").ok()};
    // Material preview inserts an extra line. Determine the layout by observed
    // field labels, never by expected numeric values or a guessed vertical shift.
    let mut layouts=Vec::new();let mut debug=Vec::new();
    for base in [307,360] {
        let mut stats=BTreeMap::new();
        for i in 0..4 {
            let y=base+i*53;
            let mut label=text(frame,(1198,y,326,43),ocr)?;
            if stat_key(&label,false).is_none() && stat_key(&label,true).is_none(){label=gray_text(frame,(1198,y,326,43),ocr)?;}
            // A wrong vertical layout has no valid label. Do not run four
            // numeric inferences on every blank/banner row of that layout.
            if stat_key(&label,false).is_none() && stat_key(&label,true).is_none() {stats.clear();break;}
            let readings=substat_number(frame,y,ocr,label.contains("激活")).unwrap_or_default();
            debug.push(format!("y={y} label={label:?} values={readings:?}"));
            let parsed:Vec<_>=readings.iter().filter_map(|s|value(s)).collect();
            if let Ok((v,percent))=consensus(parsed,"substat") {
                if let Some(key)=stat_key(&label,percent) { if stats.insert(key.to_string(),v).is_some(){stats.clear();break;} }
            }
        }
        if stats.len()==4 {layouts.push(stats);}
    }
    let stats=consensus(layouts,"substat layout").with_context(||debug.join("; "))?;
    let expected_keys:std::collections::BTreeSet<_>=expected.artifact.substats.iter().chain(expected.artifact.unactivated_substats.iter()).map(|s|s.key.as_str()).collect();
    ensure!(stats.keys().map(String::as_str).collect::<std::collections::BTreeSet<_>>()==expected_keys,"Substat keys changed");
    Ok(Observation{level,exp:exp_pair.map(|v|v.0),next_exp:exp_pair.map(|v|v.1),material_count,title:cn(&title),main_label,stats,stage_add,low_rarity_filter})
}

fn read_current(ctrl:&GenshinGameController,ocr:&dyn ImageToText<RgbImage>,r:&Request)->Result<Observation> {
    let mut last=None;
    for _ in 0..4 {
        ensure!(!ctrl.is_cancelled(),"Cancelled");
        let frame=ctrl.capture_game()?;
        match observe_frame(&frame,ocr,r){
            Ok(v)=>{
                if v.material_count==0 && v.level<20 {
                    if let Err(e)=visible_materials(&frame,0){last=Some((frame,e));yas::utils::sleep(120);continue;}
                }
                return Ok(v)
            },Err(e)=>last=Some((frame,e))
        }
        std::thread::sleep(Duration::from_millis(120));
    }
    let (frame,e)=last.unwrap();let dir=Path::new("failures").join(format!("enhance-{}",r.operation_id));std::fs::create_dir_all(&dir)?;
    frame.save(dir.join("frame.png"))?;std::fs::write(dir.join("request.json"),serde_json::to_vec_pretty(r)?)?;
    Err(e.context(format!("Enhancement observation failed; {}",dir.display())))
}

pub fn validate(r:&Request)->Result<()> {
    uuid::Uuid::parse_str(&r.operation_id)?;
    ensure!(matches!(r.action.as_str(),"open"|"inspect"|"step"|"reconcile"|"leave"),"Unsupported action");
    ensure!(r.artifact.rarity==5 && (0..=20).contains(&r.artifact.level),"Only five-star targets");
    ensure!(!r.name.is_empty() && r.name.chars().count()<40,"Invalid target name");
    ensure!(r.artifact.substats.len()+r.artifact.unactivated_substats.len()==4,"Four known substats required");
    ensure!(matches!(r.source_kind.as_str(),"ordinary"|"defined"|"unknown"),"Unknown source kind");
    Ok(())
}

fn save(path:&Path,value:&Value)->Result<()> {
    use std::io::Write;
    if let Some(p)=path.parent(){std::fs::create_dir_all(p)?;}
    let tmp=path.with_extension("tmp");
    let mut file=std::fs::File::create(&tmp)?;file.write_all(&serde_json::to_vec_pretty(value)?)?;file.sync_all()?;drop(file);
    // Windows rename does not replace an existing file. Preserve the old copy
    // until the new data has been flushed, then use ReplaceFileW via rename API.
    #[cfg(windows)] {
        use std::os::windows::ffi::OsStrExt;
        let a:Vec<u16>=tmp.as_os_str().encode_wide().chain(Some(0)).collect();
        let b:Vec<u16>=path.as_os_str().encode_wide().chain(Some(0)).collect();
        ensure!(unsafe {windows_sys::Win32::Storage::FileSystem::MoveFileExW(a.as_ptr(),b.as_ptr(),0x1|0x8)}!=0,"Atomic evidence write failed");
    }
    #[cfg(not(windows))] std::fs::rename(tmp,path)?;
    Ok(())
}
fn same_identity(a:&GoodArtifact,b:&GoodArtifact)->bool {
    let stats=|v:&GoodArtifact|->BTreeMap<String,(f64,bool)> {v.substats.iter().map(|s|(s.key.clone(),(s.value,false))).chain(v.unactivated_substats.iter().map(|s|(s.key.clone(),(s.value,true)))).collect()};
    a.set_key==b.set_key && a.slot_key==b.slot_key && a.main_stat_key==b.main_stat_key && a.rarity==b.rarity && a.level==b.level && stats(a)==stats(b)
}
fn matches_observation(o:&Observation,a:&GoodArtifact)->bool {
    o.level==a.level && o.stats==a.substats.iter().chain(a.unactivated_substats.iter()).map(|s|(s.key.clone(),s.value)).collect()
}
fn open_target(ctrl:&mut GenshinGameController,manager:&ArtifactManager,r:&Request,ocr:&dyn ImageToText<RgbImage>)->Result<(Observation,GoodArtifact)> {
    use crate::scanner::{artifact::{ArtifactOcrRegions,ArtifactScanResult,GoodArtifactScanner,GoodArtifactScannerConfig},common::{backpack_scanner::{self,BackpackScanner,BackpackScanConfig,PanelWaitMode,GridEvent,ScanAction},capture_frame::CaptureFrame,grid_voter::GridVoteSchedule}};
    // An already-selected verified target can be resumed without leaving its page.
    if r.source_kind!="unknown" {
        if let Ok(bytes)=std::fs::read(journal_root().join("binding.json")) {
            if let Ok(binding)=serde_json::from_slice::<Value>(&bytes) {
                if let Ok(artifact)=serde_json::from_value::<GoodArtifact>(binding["artifact"].clone()) {
                    if same_identity(&artifact,&r.artifact) && binding["name"]==r.name {
                        if let Ok(current)=observe_frame(&ctrl.capture_game()?,ocr,r) {
                            if matches_observation(&current,&r.artifact) && current.material_count==0 {return Ok((current,artifact));}
                        }
                    }
                }
            }
        }
    }
    let count=if let Ok((n,_))=BackpackScanner::new(ctrl).read_item_count(ocr){n} else {
        backpack_scanner::open_backpack_to_tab(ctrl,"artifact",1000,350,ocr,false,false)?.0
    };
    ui_actions::workbench_reset_artifact_filters(ctrl,ocr)?;
    ensure!(ui_actions::apply_backpack_multi_set_filter(ctrl,&[r.artifact.set_key.as_str()],manager.mappings(),ocr,false)?==1,"Target set filter not verified");
    let scaler=ctrl.scaler.clone();
    let config=BackpackScanConfig{delay_scroll:200,panel_wait:PanelWaitMode::Fingerprint{timeout_ms:350,initial_wait_ms:40},extra_delay:40,detail_panel_rect:None,grid_vote_schedule:GridVoteSchedule::for_page,probe_last_cell_per_page:false,detect_grid_duplicates:true,detect_empty_cells:true,min_items_before_visual_end:0};
    let settings=GoodArtifactScannerConfig{min_rarity:5,..Default::default()};let regions=ArtifactOcrRegions::new();let mut found=false;
    let read=|f:&CaptureFrame|->Option<GoodArtifact>{match exact_scan(||GoodArtifactScanner::scan_single_artifact(ocr,ocr,f,&scaler,&regions,manager.mappings(),&settings,0,None)){Ok(ArtifactScanResult::Artifact(a))=>Some(a),_=>None}};
    let outcome=BackpackScanner::new(ctrl).scan_grid(count.max(1) as usize,&config,0,|_,event| {
        if let GridEvent::Item{frame,..}=event {if read(&frame).is_some_and(|a|same_identity(&a,&r.artifact)){found=true;return ScanAction::Stop;}}
        ScanAction::Continue
    });
    if !found {
        ensure!(!matches!(outcome.termination,backpack_scanner::ScanTermination::CaptureFailure|backpack_scanner::ScanTermination::Cancelled),
            "Target search interrupted: {:?}; visited={}, missed={}; not evidence that target is absent",outcome.termination,outcome.scanned_count,outcome.missed_count);
    }
    ensure!(found,"Exact target not located in filtered inventory");
    // Recheck the selected full panel. Cached positions and earlier frames are hints.
    let actual=read(&CaptureFrame::full(ctrl.capture_game()?)).context("Selected target unreadable")?;
    ensure!(same_identity(&actual,&r.artifact),"Selected artifact changed before opening enhancement");
    ctrl.click_at(1700.0,1018.0);yas::utils::sleep(450);ctrl.move_to(1100.0,650.0);
    let current=read_current(ctrl,ocr,r)?;
    ensure!(matches_observation(&current,&r.artifact) && current.material_count==0,"Enhancement entry differs from verified target");
    Ok((current,actual))
}
fn verify_settings(ctrl:&mut GenshinGameController,ocr:&dyn ImageToText<RgbImage>)->Result<()> {
    ctrl.click_at(1608.0,768.0);yas::utils::sleep(250);
    let mut frame=None;
    for _ in 0..4 {let f=ctrl.capture_game()?;if cn(&text(&f,(870,276,190,55),ocr)?)=="放入设置" {frame=Some(f);break;}yas::utils::sleep(100);}
    let f=frame.context("Material settings dialog not recognized")?;
    let label=text(&f,(601,436,526,48),ocr)?;
    ensure!(label.contains('5') && cn(&label).contains("星圣遗物"),"Five-star setting label unverified");
    let sample=crop(&f,(1238,449,15,16));let mut channels=[Vec::new(),Vec::new(),Vec::new()];
    for p in sample.pixels(){for c in 0..3{channels[c].push(p[c]);}}
    let med:Vec<u8>=channels.iter_mut().map(|c|{c.sort_unstable();c[c.len()/2]}).collect();
    let bright=sample.pixels().filter(|p|*p.0.iter().min().unwrap()>160).count();
    ensure!(med.iter().all(|&c|c<140) && med[2]>med[0]+5 && bright<=3,"Five-star quick-add is enabled or unknown; no consumption");
    ctrl.click_at(1347.0,300.0);yas::utils::sleep(200);
    Ok(())
}
pub fn visible_materials(frame:&RgbImage,n:u32)->Result<Vec<u32>> {
    let mut stars=Vec::new();
    for i in 0..n.min(6) {
        let left=(1177.0+i as f64*111.3).round() as u32;let im=crop(frame,(left,898,99,21));
        let mut xs=Vec::new();let mut ys=Vec::new();let mut pixels=0;
        for (x,y,p) in im.enumerate_pixels(){
            // Gold star pixels, not the brown five-star card background.
            if p[0]>170 && p[1]>130 && p[0].saturating_sub(p[2])>90 && p[1].saturating_sub(p[2])>55 && p[0]>=p[1] {xs.push(x);ys.push(y);pixels+=1;}
        }
        ensure!(!xs.is_empty(),"Unknown material rarity");
        let width=xs.iter().max().unwrap()-xs.iter().min().unwrap()+1;let height=ys.iter().max().unwrap()-ys.iter().min().unwrap()+1;
        let count=(width as f64/15.5).round() as u32;
        ensure!((1..=4).contains(&count) && (width as f64-count as f64*15.5).abs()<=3.0 && (12..=18).contains(&height) && (pixels as i32-count as i32*126).abs()<=count as i32*35,"Five-star or unrecognized material; no confirmation");
        stars.push(count);
    }
    if n<6 {
        let left=(1177.0+n as f64*111.3).round() as u32;
        let next=crop(frame,(left,898,99,21));
        let gold=next.pixels().filter(|p|p[0]>170 && p[1]>130 && p[0].saturating_sub(p[2])>90 && p[1].saturating_sub(p[2])>55 && p[0]>=p[1]).count();
        ensure!(gold<40,"Visible material beyond reported count; count OCR is not trustworthy");
    }
    Ok(stars)
}
fn result_artifact(r:&Request,o:&Observation)->GoodArtifact {
    let mut a=r.artifact.clone();a.level=o.level;a.total_rolls=None;
    if o.level>=4 {a.substats.append(&mut a.unactivated_substats);}
    for s in a.substats.iter_mut().chain(a.unactivated_substats.iter_mut()){s.value=o.stats[&s.key];s.initial_value=None;s.rolls.clear();}
    a
}
fn staged_ready(before:&Observation,after:&Observation)->Result<bool> {
    ensure!(after.level==before.level && after.stats==before.stats && after.title==before.title && after.main_label==before.main_label,
        "Stage target identity changed: before={before:?}; after={after:?}");
    if let (Some(a),Some(b))=(before.exp,after.exp) {
        ensure!(a==b,"Stage experience changed before confirmation: {a} -> {b}");
    }
    ensure!(after.low_rarity_filter,"Stage material filter changed; confirmation withheld");
    Ok(after.material_count>0)
}
fn wait_staged(ctrl:&GenshinGameController,ocr:&dyn ImageToText<RgbImage>,r:&Request,before:&Observation)->Result<(Observation,Vec<u32>)> {
    let deadline=Instant::now()+Duration::from_secs(4);
    let mut observations=Vec::new();let mut last=String::new();
    while Instant::now()<deadline {
        ensure!(!ctrl.is_cancelled(),"Cancelled before material confirmation");
        let frame=ctrl.capture_game()?;
        match observe_frame(&frame,ocr,r) {
            Ok(o)=>{
                observations.push(json!(o));
                match staged_ready(before,&o) {
                    Ok(true)=>{
                        match visible_materials(&frame,o.material_count) {
                            Ok(stars)=>return Ok((o,stars)),
                            Err(e)=>last=e.to_string(),
                        }
                    },
                    Ok(false)=>last="Stage add produced no materials (0/15)".into(),
                    Err(e)=>{last=e.to_string();break;},
                }
            },
            Err(e)=>last=format!("Stage observation unreadable: {e}"),
        }
        yas::utils::sleep(120);
    }
    let dir=Path::new("failures").join(format!("enhance-{}",r.operation_id));std::fs::create_dir_all(&dir)?;
    save(&dir.join("stage-observations.json"),&json!({"before":before,"observations":observations,"error":last,"confirmationSent":false}))?;
    bail!("{last}; no confirmation sent; stage-add was sent once")
}
fn progressed(before:&Observation,after:&Observation)->bool {
    after.level>before.level || (after.level==before.level && matches!((before.exp,after.exp),(Some(a),Some(b)) if b>a))
}
fn reconcile(ctrl:&GenshinGameController,ocr:&dyn ImageToText<RgbImage>,r:&Request,before:&Observation)->Result<Observation> {
    let deadline=Instant::now()+Duration::from_secs(12);let mut last=String::new();
    while Instant::now()<deadline {
        ensure!(!ctrl.is_cancelled(),"Cancelled; confirmation remains pending");
        let frame=ctrl.capture_game()?;
        match observe_frame(&frame,ocr,r) {
            Ok(o) if o.material_count==0 && (o.level==20 || visible_materials(&frame,0).is_ok()) && progressed(before,&o) && o.stats.iter().all(|(k,v)|*v>=before.stats[k]) =>return Ok(o),
            Ok(_)=>last="Result not yet verified".into(),Err(e)=>last=e.to_string(),
        }
        yas::utils::sleep(150);
    }
    bail!("Unresolved enhancement confirmation: {last}; never replay confirmation")
}
fn execute_inner(ctrl:&mut GenshinGameController,manager:&ArtifactManager,r:&Request)->Result<Value> {
    validate(r)?;ctrl.focus_game_window();
    ensure!(game_window_state()["foreground"]==true,"Game window activation failed; no input sent");
    let pool=manager.pools().artifact().v4().clone();let ocr=pool.get();
    let root_buf=journal_root();
    let root=root_buf.as_path();std::fs::create_dir_all(root)?;
    let pending_path=root.join("pending.json");let binding=root.join("binding.json");
    let response=root.join(format!("{}.json",r.operation_id));
    if response.exists(){let old:Value=serde_json::from_slice(&std::fs::read(response)?)?;ensure!(old["request"]==serde_json::to_value(r)?,"Operation ID reused with another request");return Ok(old);}
    if r.action=="reconcile" {
        let pending:Value=serde_json::from_slice(&std::fs::read(&pending_path).context("No pending native confirmation")?)?;
        let original:Request=serde_json::from_value(pending["request"].clone())?;
        ensure!(same_identity(&original.artifact,&r.artifact),"Pending target differs");
        let before:Observation=serde_json::from_value(pending["before"].clone())?;
        let after=reconcile(ctrl,&ocr,&original,&before)?;
        let artifact=result_artifact(&original,&after);
        let result=json!({"request":original,"before":before,"after":after,"artifact":artifact,"materialProof":pending["materialProof"],"confirmed":true});
        save(&root.join(format!("{}.json",original.operation_id)),&result)?;save(&binding,&json!({"artifact":artifact,"name":r.name}))?;std::fs::remove_file(pending_path)?;
        return Ok(result);
    }
    ensure!(!pending_path.exists(),"Native confirmation pending; reconcile before any input");
    if r.action=="open" {
        let (o,actual)=open_target(ctrl,manager,r,&ocr)?;save(&binding,&json!({"artifact":actual,"name":r.name}))?;
        return Ok(json!({"request":r,"observation":o,"artifact":actual,"sourceVerified":true,"confirmed":false}));
    }
    let bound:Value=serde_json::from_slice(&std::fs::read(&binding).context("No verified target binding")?)?;
    let a:GoodArtifact=serde_json::from_value(bound["artifact"].clone())?;
    ensure!(same_identity(&a,&r.artifact) && bound["name"]==r.name,"Enhancement target is not bound");
    let before=read_current(ctrl,&ocr,r)?;
    ensure!(matches_observation(&before,&r.artifact),"Bound target changed; no input");
    if r.action=="inspect" {return Ok(json!({"request":r,"observation":before,"confirmed":false}));}
    if r.action=="leave" {ensure!(before.material_count==0,"Unconfirmed materials remain");ctrl.click_at(1840.0,48.0);yas::utils::sleep(300);return Ok(json!({"request":r,"confirmed":false}));}
    ensure!(r.source_kind=="ordinary" && !a.elixir_crafted,"Crafted or unverified source: automatic consumption disabled");
    ensure!(before.level<20 && before.material_count==0 && before.stage_add && before.low_rarity_filter,"Stage preconditions not met");
    verify_settings(ctrl,&ocr)?;
    let empty=read_current(ctrl,&ocr,r)?;
    ensure!(matches_observation(&empty,&r.artifact) && empty.material_count==0 && empty.stage_add && empty.low_rarity_filter,"Stage preconditions changed");
    // Let the game consume the release at the clicked position before moving
    // the cursor. Poll the resulting state, never replay the stage-add click.
    ctrl.click_at(1767.0,768.0);yas::utils::sleep(80);ctrl.move_to(1100.0,650.0);
    // Identity and material proof are read from one complete fresh frame.
    // No second unbounded OCR pass, and no stitching across observations.
    let (prepared,stars)=wait_staged(ctrl,&ocr,r,&empty)?;
    let proof=json!({"emptyBefore":true,"rarityLimit":4,"fiveStarQuickAddDisabled":true,"stageAddObserved":true,"visibleRarities":stars,"count":prepared.material_count,"sameFrame":true,"selection":"restricted_game_stage_add"});
    let pending=json!({"request":r,"before":empty,"prepared":prepared,"materialProof":proof});
    ensure!(game_window_state()["foreground"]==true,"Game window activation failed before confirmation; no consuming input sent");
    save(&pending_path,&pending)?; // Durable before the ONE consuming click.
    if ctrl.is_cancelled(){std::fs::remove_file(&pending_path)?;bail!("Cancelled before confirmation; no consuming input sent");}
    ctrl.click_at(1743.0,1019.0);yas::utils::sleep(80);ctrl.move_to(1100.0,650.0);
    let after=reconcile(ctrl,&ocr,r,&empty)?;
    let artifact=result_artifact(r,&after);
    let result=json!({"request":r,"before":empty,"after":after,"artifact":artifact,"materialProof":proof,"confirmed":true});
    save(&response,&result)?;save(&binding,&json!({"artifact":artifact,"name":r.name}))?;std::fs::remove_file(pending_path)?;
    Ok(result)
}

#[cfg(test)]
mod stage_tests {
    use super::*;
    fn empty()->Observation {Observation{level:12,exp:Some(350),next_exp:Some(13025),material_count:0,title:"生之花测试".into(),main_label:"生命值".into(),stats:BTreeMap::from([("atk".into(),31.0)]),stage_add:true,low_rarity_filter:true}}
    #[test] fn empty_tray_is_pending_not_changed_target(){let o=empty();assert!(!staged_ready(&o,&o).unwrap());}
    #[test] fn ready_requires_materials_and_same_identity(){let o=empty();let mut a=o.clone();a.material_count=11;assert!(staged_ready(&o,&a).unwrap());a.level=16;assert!(staged_ready(&o,&a).is_err());}
    #[test] fn missing_optional_exp_is_not_an_observed_change(){let o=empty();let mut a=o.clone();a.material_count=1;a.exp=None;assert!(staged_ready(&o,&a).unwrap());a.exp=Some(351);assert!(staged_ready(&o,&a).is_err());}
    #[test] fn changed_filter_or_stat_prevents_confirmation(){let o=empty();let mut a=o.clone();a.material_count=1;a.low_rarity_filter=false;assert!(staged_ready(&o,&a).is_err());a.low_rarity_filter=true;a.stats.insert("atk".into(),32.0);assert!(staged_ready(&o,&a).is_err());}
    #[test] fn unknown_exp_does_not_prove_same_level_progress(){let mut o=empty();let mut a=o.clone();o.exp=None;assert!(!progressed(&o,&a));a.level=13;assert!(progressed(&o,&a));a.level=12;o.exp=Some(350);a.exp=Some(351);assert!(progressed(&o,&a));a.exp=None;assert!(!progressed(&o,&a));}
}
pub fn execute(ctrl:&mut GenshinGameController,manager:&ArtifactManager,r:Request,cancel:yas::cancel::CancelToken)->ManageResult {
    ctrl.set_cancel_token(cancel);
    let started=Instant::now();
    log::info!("[enhance] {} {} started",r.action,r.operation_id);
    let row=match execute_inner(ctrl,manager,&r) {
        Ok(value)=>InstructionResult{id:"enhance:0".into(),status:InstructionStatus::Success,message:Some(value.to_string())},
        Err(e)=>{
            let root=std::env::var_os("WORKBENCH_ENHANCE_DIR").map(std::path::PathBuf::from).unwrap_or_else(||"enhancement".into());
            if !root.join("pending.json").exists() {
                let _=save(&root.join(format!("{}.json",r.operation_id)),&json!({"request":r,"confirmed":false,"error":format!("{e:#}")}));
            }
            let dir=Path::new("failures").join(format!("enhance-{}",r.operation_id));let _=std::fs::create_dir_all(&dir);
            if !dir.join("frame.png").exists(){if let Ok(f)=ctrl.capture_game(){let _=f.save(dir.join("frame.png"));}}
            let _=save(&dir.join("error.json"),&json!({"action":r.action,"elapsedMs":started.elapsed().as_millis(),"error":format!("{e:#}")}));
            let _=std::fs::write(dir.join("request.json"),serde_json::to_vec_pretty(&r).unwrap_or_default());
            InstructionResult::failure("enhance:0",InstructionStatus::UiError,"强化未完成，未确认操作不会自动重发", "Enhancement stopped; confirmations are never replayed",Some(&e))
        }
    };
    log::info!("[enhance] {} {} finished in {} ms",r.action,r.operation_id,started.elapsed().as_millis());
    let results=vec![row];ManageResult{summary:ManageSummary::from_results(&results),results}
}
