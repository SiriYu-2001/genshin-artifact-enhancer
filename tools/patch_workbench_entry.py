from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]/'vendor/goodscanner'
TOOLS=Path(__file__).resolve().parent
def edit(name,before,after):
    path=ROOT/'genshin/src'/name
    s=path.read_text(encoding='utf-8')
    if s.count(before)!=1:raise RuntimeError(f'Unexpected context in {name}: {before[:90]}')
    path.write_text(s.replace(before,after),encoding='utf-8')

def main():
    import shutil
    src=ROOT/'genshin/src/manager'
    (src/'workbench_entry.rs').write_bytes((TOOLS/'workbench_entry.rs').read_bytes())
    shutil.copytree(TOOLS/'workbench-entry-assets',src/'workbench-entry-assets',dirs_exist_ok=True)
    edit('manager/mod.rs','pub mod workbench_enhance;','pub mod workbench_enhance;\npub mod workbench_entry;')
    edit('scanner/common/backpack_scanner.rs','fn parse_item_count_text(text:',
         'pub(crate) fn parse_item_count_text(text:')
    edit('scanner/artifact/scanner.rs','''        if std::env::var("WORKBENCH_PRESERVE_FILTERS").as_deref()==Ok("1") {
            let state=crate::manager::workbench_enhance::game_window_state();
            anyhow::ensure!(state["supported"]==true,"Game must be 1920x1080");
            ctrl.focus_game_window();
            BackpackScanner::new(ctrl).read_item_count(&count_ocr_guard).map_err(|_|anyhow::anyhow!("请先打开背包圣遗物页面，再扫描当前筛选；没有清除筛选"))?;
        }

        let total_count = if !skip_open_backpack {''',
         '''        let workbench = std::env::var_os("WORKBENCH_GOOD_TOKEN").is_some();
        let total_count = if workbench {
            use crate::manager::workbench_entry::{navigate,Goal,Page};
            ctrl.focus_game_window();
            // Reopening the bag resets the grid to row 1 without changing user filters.
            let Page::ArtifactBag(count, _) = navigate(ctrl,&count_ocr_guard,Goal::ReopenBag)?
                else { unreachable!("artifact entry goal") };
            count
        } else if !skip_open_backpack {''')
    edit('scanner/artifact/scanner.rs',
         '''            backpack_scanner::select_tab_and_read_count(ctrl,"artifact",self.config.delay_tab,&count_ocr_guard,false,self.config.dump_images)?.0''',
         '''            backpack_scanner::dismiss_five_star_filter(ctrl,self.config.delay_tab,self.config.dump_images);
            use crate::manager::workbench_entry::{navigate,Goal,Page};
            let Page::ArtifactBag(count, _) = navigate(ctrl,&count_ocr_guard,Goal::Bag)?
                else { unreachable!("artifact entry goal") };
            count''')
    edit('manager/workbench_enhance.rs','''    let count=if let Ok((n,_))=BackpackScanner::new(ctrl).read_item_count(ocr){n} else {
        backpack_scanner::open_backpack_to_tab(ctrl,"artifact",1000,350,ocr,false,false)?.0
    };''','''    use super::workbench_entry::{navigate,Goal,Page};
    let Page::ArtifactBag(count, _) = navigate(ctrl,ocr,Goal::Bag)?
        else { unreachable!("artifact entry goal") };
    backpack_scanner::dismiss_five_star_filter(ctrl,350,false);''')
    edit('manager/equip_manager.rs','''        // Open character screen (same logic as ensure_character_screen used by tests)
        ctrl.return_to_main_ui(8);
        yas::utils::sleep(d_action() * 5 / 8);

        if let Err(e) = ui_actions::ensure_character_screen(ctrl, ocr, &self.mappings) {''',
         '''        let opened = if std::env::var_os("WORKBENCH_GOOD_TOKEN").is_some() {
            super::workbench_entry::navigate(ctrl, ocr, super::workbench_entry::Goal::Character).map(|_| ())
        } else {
            ctrl.return_to_main_ui(8);
            yas::utils::sleep(d_action() * 5 / 8);
            ui_actions::ensure_character_screen(ctrl, ocr, &self.mappings)
        };
        if let Err(e) = opened {''')
    edit('manager/equip_manager.rs','''        // Leave character screen after all operations
        let _ = ui_actions::leave_character_screen(ctrl);''',
         '''        // Do not send cleanup keys after failed entry or an unverified equip.
        if std::env::var_os("WORKBENCH_GOOD_TOKEN").is_some() {
            if targets.iter().all(|target| results.get(&target.result_id)
                .is_some_and(|result| matches!(result.status,InstructionStatus::Success|InstructionStatus::AlreadyCorrect))) {
                let _ = super::workbench_entry::navigate(ctrl,&ocr,super::workbench_entry::Goal::World);
            }
        } else {
            let _ = ui_actions::leave_character_screen(ctrl);
        }''')
    edit('bin/workbench_goodscanner.rs','    if first=="--replay-equipment-scroll" {',
         '''    if first=="--replay-entry" {
        let cases:Vec<String>=serde_json::from_slice(&std::fs::read(args.next().ok_or_else(||anyhow::anyhow!("frame manifest required"))?)?)?;
        let model=genshin_scanner::scanner::common::ocr_factory::create_ocr_model("ppocrv6tiny")?;
        for filename in cases {
            let frame=image::open(filename)?.to_rgb8();
            println!("{:?}",genshin_scanner::manager::workbench_entry::identify(&frame,model.as_ref()));
        }
        return Ok(());
    }
    if first=="--replay-equipment-scroll" {''')

if __name__=='__main__':main()
