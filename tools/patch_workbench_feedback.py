"""Scoped inventory reads and diagnostics on the pinned Workbench adapter."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]/'vendor/goodscanner'
def edit(name,old,new):
    p=ROOT/name;s=p.read_text(encoding='utf-8')
    if s.count(old)!=1:raise RuntimeError(f'Feedback patch context mismatch: {name}: {old[:80]}')
    p.write_text(s.replace(old,new),encoding='utf-8')
def main():
    scroll='genshin/src/scanner/common/backpack_scanner.rs'
    edit(scroll,'fn scroll_states_similar(first: &ScrollVisualState, second: &ScrollVisualState) -> Option<bool> {',r'''// Identify the narrow neutral scrollbar thumb, not its animated surroundings.
fn workbench_thumb_rows(columns:&[Vec<u8>])->Option<(usize,usize)> {
    if columns.len()<4 || columns.iter().any(|c|c.len()!=columns[0].len() || c.len()%3!=0){return None;}
    let rows=columns[0].len()/3;let mut runs=Vec::new();let mut start=None;
    for row in 0..=rows {
        let bright=if row==rows {false} else {
            let values:Vec<(u8,u8)>=columns.iter().map(|c|{let p=&c[row*3..row*3+3];(*p.iter().min().unwrap(),*p.iter().max().unwrap())}).collect();
            (1..values.len()-2).any(|left| ((left+2)..=(left+8).min(values.len()-1)).any(|right|{
                let minimum=values[left..right].iter().map(|p|p.0).min().unwrap();
                values[left..right].iter().all(|&(lo,hi)|lo>=150 && hi-lo<=45)
                    && minimum as u16>=values[left-1].0.max(values[right].0) as u16+12
            }))
        };
        if bright && start.is_none(){start=Some(row);}
        if !bright {if let Some(begin)=start.take(){if row-begin>=3 {runs.push((begin,row-1));}}}
    }
    if runs.len()==1 {Some(runs[0])} else {None}
}

fn scroll_states_similar(first: &ScrollVisualState, second: &ScrollVisualState) -> Option<bool> {''')
    edit(scroll,'''    Some(
        visual_samples_similar(&first.grid, &second.grid)
            && !scrollbar_band_moved(&first.scrollbar_band, &second.scrollbar_band)?,
    )''','''    if let (Some(a),Some(b))=(workbench_thumb_rows(&first.scrollbar_band),workbench_thumb_rows(&second.scrollbar_band)) {
        return Some(a==b);
    }
    Some(
        visual_samples_similar(&first.grid, &second.grid)
            && !scrollbar_band_moved(&first.scrollbar_band, &second.scrollbar_band)?,
    )''')
    edit(scroll,'''        for _ in 0..3 {
            utils::sleep(SCROLL_STABLE_INTERVAL_MS);
            let current = self.capture_scroll_state_with_retry()?;
            match scroll_states_similar(&previous, &current) {
                Some(true) => return Ok(current),
                Some(false) => previous = current,''','''        let mut steady=0;
        for _ in 0..12 {
            if self.ctrl.is_cancelled(){return Err(anyhow!("Cancelled while awaiting scroll settling"));}
            utils::sleep(SCROLL_STABLE_INTERVAL_MS);
            let current = self.capture_scroll_state_with_retry()?;
            match scroll_states_similar(&previous, &current) {
                Some(true) => {steady+=1;if steady>=2{return Ok(current);}previous=current;},
                Some(false) => {steady=0;previous=current;},''')
    p=ROOT/scroll
    with p.open('a',encoding='utf-8') as f:f.write(r'''
#[cfg(test)]
mod workbench_scroll_tests {
    use super::*;
    fn band(start:usize,end:usize)->Vec<Vec<u8>> {
        let mut b=vec![vec![85;120*3];6];
        for c in 2..4 {for y in start..end {b[c][y*3..y*3+3].copy_from_slice(&[205,203,200]);}}
        b
    }
    fn state(start:usize,end:usize,grid:u8)->ScrollVisualState {
        ScrollVisualState{grid:vec![grid;GRID_ROWS*GRID_COLS*9],grid_cells:vec![vec![grid;9];GRID_ROWS*GRID_COLS],scrollbar_band:band(start,end)}
    }
    #[test] fn stable_thumb_ignores_decorative_grid_changes(){assert_eq!(scroll_states_similar(&state(30,90,30),&state(30,90,200)),Some(true));}
    #[test] fn moving_thumb_never_passes_as_stable(){assert_eq!(scroll_states_similar(&state(30,90,30),&state(32,92,30)),Some(false));}
    #[test] fn flat_or_ambiguous_bands_do_not_invent_a_thumb(){assert_eq!(workbench_thumb_rows(&vec![vec![200;360];6]),None);let mut b=band(10,30);for c in 2..4 {for y in 50..70{b[c][y*3..y*3+3].copy_from_slice(&[205,203,200]);}}assert_eq!(workbench_thumb_rows(&b),None);}
}
''')
    edit('genshin/src/server.rs','"enhancementVersion":1','"enhancementVersion":2,"window":crate::manager::workbench_enhance::game_window_state()')
    edit('genshin/src/manager/models.rs','pub struct ScanRequest {','''pub struct ScanRequest {
    #[serde(default, rename="preserveFilters")]
    pub preserve_filters: bool,''')
    edit('genshin/src/server.rs','        let mut config = self.scan_defaults.clone();','''        std::env::set_var("WORKBENCH_PRESERVE_FILTERS",if request.preserve_filters {"1"} else {"0"});
        let mut config = self.scan_defaults.clone();''')
    edit('genshin/src/scanner/common/backpack_scanner.rs','    if tab != "artifact" {','''    if std::env::var("WORKBENCH_PRESERVE_FILTERS").as_deref()==Ok("1") {return;}
    if tab != "artifact" {''')
    file='genshin/src/scanner/artifact/scanner.rs'
    edit(file,'        let count_ocr_guard = pools.artifact().v5().get();','''        let count_ocr_guard = pools.artifact().v5().get();
        if std::env::var("WORKBENCH_PRESERVE_FILTERS").as_deref()==Ok("1") {
            let state=crate::manager::workbench_enhance::game_window_state();
            anyhow::ensure!(state["supported"]==true,"Game must be 1920x1080");
            ctrl.focus_game_window();
            BackpackScanner::new(ctrl).read_item_count(&count_ocr_guard).map_err(|_|anyhow::anyhow!("请先打开背包圣遗物页面，再扫描当前筛选；没有清除筛选"))?;
        }''')
    edit(file,'if std::env::var_os("WORKBENCH_GOOD_TOKEN").is_some() && !self.config.keep_five_star_filter {',
        'if std::env::var_os("WORKBENCH_GOOD_TOKEN").is_some() && !self.config.keep_five_star_filter && std::env::var("WORKBENCH_PRESERVE_FILTERS").as_deref()!=Ok("1") {')
    edit(file,'''            let complete=outcome.termination==backpack_scanner::ScanTermination::Exhausted
                && start_at==0 && self.config.max_count==0 && seen==total''','''            let scoped=std::env::var("WORKBENCH_PRESERVE_FILTERS").as_deref()==Ok("1");
            let ended=outcome.termination==backpack_scanner::ScanTermination::Exhausted || (scoped && matches!(outcome.termination,backpack_scanner::ScanTermination::EmptyCell|backpack_scanner::ScanTermination::UnchangedPage));
            let complete=ended
                && start_at==0 && self.config.max_count==0 && (scoped || seen==total)''')
    edit(file,'"complete":complete,"expected":total,"visited":seen,"five_star":five_star,','''"complete":complete,"expected":if scoped {seen}else{total},"visited":seen,"five_star":five_star,
                "inventory_header_count":total,"account_complete":!scoped && complete,"scope":if scoped {"observed_filter_results"}else{"full_inventory"},''')
if __name__=='__main__':
    main()
    import runpy
    runpy.run_path(str(Path(__file__).with_name('patch_workbench_equip.py')),run_name='__main__')
