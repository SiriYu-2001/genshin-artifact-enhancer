//! Identify safe Workbench entry pages before using game input.
use anyhow::{bail, ensure, Result};
use image::{imageops, GrayImage, RgbImage};
use std::{path::Path, sync::OnceLock, time::{Duration, Instant}};
use yas::{ocr::ImageToText, utils::sleep};

use crate::scanner::common::{
    backpack_scanner::parse_item_count_text,
    game_controller::GenshinGameController,
};

#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub enum Page {
    World,
    ArtifactBag(i32, i32),
    OtherBag,
    Character,
    EquipmentList,
    ArtifactDetail,
    Filter,
    Uncertain,
}

struct Icon {
    image: GrayImage,
    x: u32,
    y: u32,
    min_mean: f32,
}

impl Icon {
    fn new(bytes: &[u8], x: u32, y: u32, min_mean: f32) -> Self {
        Self { image: image::load_from_memory(bytes).expect("bundled entry icon").to_luma8(), x, y, min_mean }
    }

    fn matches(&self, frame: &RgbImage) -> bool {
        // Small local search to tolerate rounding and slight shifts in the HUD.
        let width = self.image.width();
        let height = self.image.height();
        let crop = imageops::crop_imm(frame, self.x - 5, self.y - 5, width + 10, height + 10).to_image();
        let region = image::DynamicImage::ImageRgb8(crop).into_luma8();
        let n = (width * height) as f32;
        let template_mean = self.image.as_raw().iter().map(|&p| p as f32).sum::<f32>() / n;
        let template_var = self.image.as_raw().iter().map(|&p| (p as f32 - template_mean).powi(2)).sum::<f32>();
        for dy in 0..=10 {
            for dx in 0..=10 {
                let mut sum = 0.;
                let mut squares = 0.;
                let mut dot = 0.;
                for (x, y, t) in self.image.enumerate_pixels() {
                    let p = region.get_pixel(x + dx, y + dy)[0] as f32;
                    sum += p;
                    squares += p * p;
                    dot += p * (t[0] as f32 - template_mean);
                }
                let mean = sum / n;
                let var = squares - sum * mean;
                if mean >= self.min_mean && var > 0. && dot >= 0.82 * (var * template_var).sqrt() {
                    return true;
                }
            }
        }
        false
    }
}

struct Icons {
    paimon: Icon,
    world_bag: Icon,
    bag_header: Icon,
    artifact_tab: Icon,
    close: Icon,
    back: Icon,
}

fn icons() -> &'static Icons {
    static ICONS: OnceLock<Icons> = OnceLock::new();
    ICONS.get_or_init(|| Icons {
        paimon: Icon::new(include_bytes!("workbench-entry-assets/paimon.png"),39,35,88.),
        world_bag: Icon::new(include_bytes!("workbench-entry-assets/world-bag.png"),1749,27,110.),
        bag_header: Icon::new(include_bytes!("workbench-entry-assets/bag-header.png"),55,38,95.),
        artifact_tab: Icon::new(include_bytes!("workbench-entry-assets/artifact-tab.png"),652,26,100.),
        close: Icon::new(include_bytes!("workbench-entry-assets/close.png"),1830,34,110.),
        back: Icon::new(include_bytes!("workbench-entry-assets/back.png"),1830,34,110.),
    })
}

fn read(frame: &RgbImage, ocr: &dyn ImageToText<RgbImage>, rect: (u32,u32,u32,u32)) -> String {
    let (x,y,w,h) = rect;
    ocr.image_to_text(&imageops::crop_imm(frame,x,y,w,h).to_image(),false).unwrap_or_default()
}

fn label(frame: &RgbImage, ocr: &dyn ImageToText<RgbImage>, rect: (u32,u32,u32,u32)) -> String {
    read(frame,ocr,rect).chars().filter(|c| ('\u{4e00}'..='\u{9fff}').contains(c)).collect()
}

fn selection(frame: &RgbImage, ocr: &dyn ImageToText<RgbImage>) -> bool {
    label(frame,ocr,(1640,25,115,45)) == "对比"
        && label(frame,ocr,(1470,25,145,45)) == "圣遗物推荐"
        && label(frame,ocr,(1705,982,140,55)) == "强化"
        && matches!(label(frame,ocr,(1520,990,100,35)).as_str(),"装备"|"替换"|"卸下")
}

pub fn identify(frame: &RgbImage, ocr: &dyn ImageToText<RgbImage>) -> Page {
    if frame.dimensions() != (1920,1080) { return Page::Uncertain; }
    let icons = icons();
    if icons.paimon.matches(frame) && icons.world_bag.matches(frame) {
        return Page::World;
    }
    if icons.bag_header.matches(frame) && icons.close.matches(frame) {
        let heading = label(frame,ocr,(125,25,145,45));
        if heading == "圣遗物" && icons.artifact_tab.matches(frame) {
            let count = read(frame,ocr,(1545,28,263,41));
            if let Ok((n,cap)) = parse_item_count_text(&count) {
                if n > 0 && cap > 0 { return Page::ArtifactBag(n,cap); }
            }
        }
        if matches!(heading.as_str(),"武器"|"角色培养素材"|"食物"|"材料"|"小道具"|"任务"|"贵重道具"|"摆设") {
            return Page::OtherBag;
        }
    }
    if label(frame,ocr,(20,22,230,50)) == "圣遗物筛选" {
        return Page::Filter;
    }
    if icons.close.matches(frame)
        && label(frame,ocr,(137,126,123,51)) == "属性"
        && label(frame,ocr,(132,200,130,48)) == "武器"
        && label(frame,ocr,(132,272,150,46)) == "圣遗物"
    {
        return Page::Character;
    }
    if selection(frame,ocr) { return Page::EquipmentList; }
    if icons.back.matches(frame) {
        let title = read(frame,ocr,(140,24,540,45));
        let slot = title.split(['/', '／']).next().unwrap_or_default().trim();
        if matches!(slot,"生之花"|"死之羽"|"时之沙"|"空之杯"|"理之冠")
            && label(frame,ocr,(105,123,130,56)) == "详情"
            && label(frame,ocr,(105,190,145,62)) == "强化"
        {
            return Page::ArtifactDetail;
        }
    }
    Page::Uncertain
}

#[derive(Clone, Copy, Debug)]
pub enum Goal { Bag, ReopenBag, Character, World }

#[derive(Debug, PartialEq, Clone, Copy)]
enum Action { Done, Back, Close, OpenBag, ArtifactTab, OpenCharacter, Stop }

fn next(page: Page, goal: Goal, reopen: bool, pending: bool) -> Action {
    use Action::*;
    use Page::*;
    if pending && page == ArtifactDetail { return Stop; }
    if let ArtifactBag(_,_) = page {
        if matches!(goal,Goal::Bag) || matches!(goal,Goal::ReopenBag) && !reopen {
            return Done;
        }
    }
    if page == Character && matches!(goal,Goal::Character) || page == World && matches!(goal,Goal::World) {
        return Done;
    }
    match page {
        World => if matches!(goal, Goal::Character) { OpenCharacter } else { OpenBag },
        OtherBag if matches!(goal, Goal::Bag | Goal::ReopenBag) => ArtifactTab,
        ArtifactBag(_,_) | OtherBag | Character => Close,
        EquipmentList | ArtifactDetail => Back,
        Filter | Uncertain => Stop,
    }
}

fn arrived(action: Action, before: Page, after: Page) -> bool {
    match action {
        Action::Close => after == Page::World,
        Action::OpenBag => matches!(after, Page::ArtifactBag(_, _) | Page::OtherBag),
        Action::ArtifactTab => matches!(after, Page::ArtifactBag(_, _)),
        Action::OpenCharacter => after == Page::Character,
        Action::Back if before == Page::EquipmentList => after == Page::Character,
        Action::Back => matches!(after, Page::ArtifactBag(_, _) | Page::OtherBag | Page::Character | Page::EquipmentList),
        Action::Done | Action::Stop => false,
    }
}

fn enhancement_pending() -> bool {
    if std::env::var_os("WORKBENCH_ENHANCE_DIR")
        .is_some_and(|root| std::path::PathBuf::from(root).join("pending.json").is_file())
    {
        return true;
    }
    let Some(stop) = std::env::var_os("WORKBENCH_STOP_FILE").map(std::path::PathBuf::from) else {
        return false;
    };
    let Some(runtime) = stop.parent() else { return true; };
    let active = match std::fs::read(runtime.join("active-run.json")) {
        Ok(raw) => raw,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return false,
        Err(_) => return true,
    };
    let Ok(active) = serde_json::from_slice::<serde_json::Value>(&active) else { return true; };
    active["run_directory"].as_str().is_none_or(|dir| Path::new(dir).join("pending.json").is_file())
}

pub fn navigate(ctrl: &mut GenshinGameController, ocr: &dyn ImageToText<RgbImage>,
                goal: Goal) -> Result<Page> {
    let mut reopen = matches!(goal,Goal::ReopenBag);
    let mut frame = ctrl.capture_game()?;
    let mut page = identify(&frame,ocr);
    // Require a stable observation before rejecting an entrance that may be animating.
    for _ in 0..3 {
        if page != Page::Uncertain { break; }
        sleep(100);
        frame = ctrl.capture_game()?;
        page = identify(&frame,ocr);
    }
    let pending = enhancement_pending();
    let mut route = Vec::new();
    for _ in 0..7 {
        ensure!(!ctrl.check_rmb(),"Entry navigation cancelled");
        let action = next(page,goal,reopen,pending);
        if action == Action::Done { return Ok(page); }
        if action == Action::Stop { break; }
        log::info!("[entry] {:?} -> {:?} for {:?}",page,action,goal);
        route.push(format!("{page:?} -> {action:?}"));
        match action {
            Action::Close => ctrl.click_at(1840.,48.),
            Action::Back if page == Page::ArtifactDetail => ctrl.click_at(1840.,48.),
            Action::Back => ctrl.key_press(enigo::Key::Escape),
            Action::OpenBag => { ctrl.key_press(enigo::Key::Layout('b')); reopen = false; }
            Action::ArtifactTab => { ctrl.click_at(675.,50.); reopen = false; }
            Action::OpenCharacter => ctrl.key_press(enigo::Key::Layout('c')),
            _ => unreachable!(),
        }
        sleep(350);
        let deadline = Instant::now()+Duration::from_millis(1400);
        let mut changed = false;
        let mut observed = Page::Uncertain;
        while Instant::now()<deadline {
            ensure!(!ctrl.check_rmb(),"Entry navigation cancelled");
            frame = ctrl.capture_game()?;
            observed = identify(&frame,ocr);
            if arrived(action,page,observed) {
                page = observed;
                changed = true;
                break;
            }
            sleep(130);
        }
        if !changed {
            route.push(format!("no confirmed transition; last observation {observed:?}"));
            break;
        }
    }
    let job = std::env::var("WORKBENCH_ACTIVE_JOB").ok()
        .and_then(|s|uuid::Uuid::parse_str(&s).ok()).map(|v|v.to_string());
    if let Some(job) = job {
        let dir = Path::new("failures").join(format!("entry-{job}"));
        let _ = std::fs::create_dir_all(&dir);
        let _ = frame.save(dir.join("frame.png"));
        let _ = std::fs::write(dir.join("observations.json"),serde_json::to_vec_pretty(
            &serde_json::json!({"lastPage":format!("{page:?}"),"goal":format!("{goal:?}"),"route":route})).unwrap_or_default());
    }
    if pending && page == Page::ArtifactDetail { bail!("Entry navigation blocked by unconfirmed enhancement"); }
    if page == Page::Filter { bail!("Entry filter panel is open; close it manually"); }
    bail!("Entry page unverified: {:?}; expected {:?}; steps: {:?}",page,goal,route)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn routes_use_only_verified_pages() {
        assert_eq!(next(Page::Character,Goal::Bag,false,false),Action::Close);
        assert_eq!(next(Page::EquipmentList,Goal::Bag,false,false),Action::Back);
        assert_eq!(next(Page::World,Goal::Bag,false,false),Action::OpenBag);
        assert_eq!(next(Page::OtherBag,Goal::Bag,false,false),Action::ArtifactTab);
        assert_eq!(next(Page::ArtifactBag(10,2700),Goal::Bag,false,false),Action::Done);
        assert_eq!(next(Page::ArtifactBag(10,2700),Goal::ReopenBag,true,false),Action::Close);
        assert_eq!(next(Page::Character,Goal::Character,false,false),Action::Done);
        assert_eq!(next(Page::ArtifactBag(10,2700),Goal::Character,false,false),Action::Close);
        assert_eq!(next(Page::World,Goal::Character,false,false),Action::OpenCharacter);
    }
    #[test]
    fn pending_or_unidentified_pages_never_trigger_recovery_keys() {
        assert_eq!(next(Page::ArtifactDetail,Goal::Bag,false,true),Action::Stop);
        assert_eq!(next(Page::ArtifactBag(10,2700),Goal::ReopenBag,true,true),Action::Close);
        assert_eq!(next(Page::World,Goal::ReopenBag,true,true),Action::OpenBag);
        assert_eq!(next(Page::Filter,Goal::Bag,false,false),Action::Stop);
        assert_eq!(next(Page::Uncertain,Goal::World,false,false),Action::Stop);
        assert!(!arrived(Action::Close,Page::Character,Page::OtherBag));
        assert!(!arrived(Action::OpenCharacter,Page::World,Page::EquipmentList));
        assert!(arrived(Action::Back,Page::EquipmentList,Page::Character));
    }
}
