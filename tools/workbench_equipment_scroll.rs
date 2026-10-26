// Selection-page geometry already used by the deterministic equipment driver.
fn workbench_selection_candidate(level:i32,target:i32)->bool {
    !(0..=20).contains(&level) || level==target
}

pub fn workbench_selection_thumb(frame:&RgbImage)->Option<(usize,usize)> {
    if frame.dimensions()!=(1920,1080) {return None;}
    let mut runs=Vec::new();let mut begin=None;
    for y in 125..=920 {
        let bright=y<920 && (602..607).filter(|&x| {
            let p=frame.get_pixel(x,y).0;
            let lo=*p.iter().min().unwrap();let hi=*p.iter().max().unwrap();
            lo>=150 && hi-lo<=45
        }).count()>=4;
        if bright && begin.is_none(){begin=Some(y as usize);}
        if !bright {if let Some(b)=begin.take(){if y as usize-b>=15{runs.push((b,y as usize));}}}
    }
    runs.sort_by_key(|&(a,b)|std::cmp::Reverse(b-a));
    let first=*runs.first()?;
    if runs.get(1).is_some_and(|&(a,b)| (b-a)*3>first.1-first.0) {return None;}
    Some(first)
}

fn workbench_selection_moved(before:Option<(usize,usize)>,after:Option<(usize,usize)>)->Option<bool> {
    let (a,b)=(before?,after?);
    Some(a.0.abs_diff(b.0)>3 || a.1.abs_diff(b.1)>3)
}

fn workbench_scroll_selection(ctrl:&mut GenshinGameController)->Result<bool> {
    let before=workbench_selection_thumb(&ctrl.capture_game()?);
    for attempt in 0..2 {
        anyhow::ensure!(!ctrl.is_cancelled(),"Equipment search cancelled");
        ctrl.focus_game_window();
        let cx=SEL_FIRST_X+1.5*SEL_OFFSET_X;let cy=SEL_FIRST_Y+1.5*SEL_OFFSET_Y;
        ctrl.move_to(cx,cy);yas::utils::sleep(50);
        scroll_ticks_dir(ctrl,SEL_SCROLL_TICKS,1);
        let after=workbench_selection_thumb(&ctrl.capture_game()?);
        log_info!("[workbench equipment scroll] before={:?} after={:?} retry={}",
                  "[workbench equipment scroll] before={:?} after={:?} retry={}",before,after,attempt);
        if workbench_selection_moved(before,after)==Some(true){return Ok(true);}
        if attempt==1 && workbench_selection_moved(before,after)==Some(false)
                && after.is_some_and(|(_,end)|end>=908) {return Ok(false);}
    }
    anyhow::bail!("Equipment search scroll unverified; no equip click, list end not assumed")
}

#[cfg(test)]
mod workbench_equipment_scroll_tests {
    use super::*;
    #[test] fn equal_levels_and_unknown_levels_remain_candidates(){
        assert!((0..200).all(|_|workbench_selection_candidate(20,20)));
        assert!(workbench_selection_candidate(-1,20));assert!(workbench_selection_candidate(201,20));
        assert!(!workbench_selection_candidate(16,20));
    }
    #[test] fn identical_ocr_values_do_not_override_visual_movement(){
        assert_eq!(workbench_selection_moved(Some((128,300)),Some((301,473))),Some(true));
        assert_eq!(workbench_selection_moved(Some((740,918)),Some((740,918))),Some(false));
        assert_eq!(workbench_selection_moved(None,None),None);
        assert_eq!(workbench_selection_moved(Some((740,918)),None),None);
    }
    #[test] fn thumb_geometry_rejects_missing_or_ambiguous_tracks(){
        let mut frame=RgbImage::from_pixel(1920,1080,image::Rgb([35,65,110]));
        assert_eq!(workbench_selection_thumb(&frame),None);
        for y in 128..300 {for x in 602..607 {frame.put_pixel(x,y,image::Rgb([188,199,194]));}}
        assert_eq!(workbench_selection_thumb(&frame),Some((128,300)));
        for y in 650..820 {for x in 602..607 {frame.put_pixel(x,y,image::Rgb([188,199,194]));}}
        assert_eq!(workbench_selection_thumb(&frame),None);
    }
}
