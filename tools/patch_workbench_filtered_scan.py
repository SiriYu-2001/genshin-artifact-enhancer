"""Enable filtered-list termination and unknown-total progress on the pinned adapter."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]/'vendor/goodscanner'
def edit(name,old,new):
    p=ROOT/name;s=p.read_text(encoding='utf-8')
    if s.count(old)!=1:raise RuntimeError(f'Filtered scan patch context mismatch: {name}: {old[:80]}')
    p.write_text(s.replace(old,new),encoding='utf-8')
def main():
    file='genshin/src/scanner/artifact/scanner.rs'
    edit(file,'        let scan_config = BackpackScanConfig {','        let scoped = std::env::var("WORKBENCH_PRESERVE_FILTERS").as_deref()==Ok("1");\n        let scan_config = workbench_scan_config(&self.config, scoped);\n        /* Original configuration moved into the tested policy helper.\n        let scan_config = BackpackScanConfig {')
    edit(file,'        // Per-page 3-pass voting state (shared across artifact / weapon / manager).','        */\n        // Per-page 3-pass voting state (shared across artifact / weapon / manager).')
    edit(file,'        let total = total_count as usize;','        let total = total_count as usize;\n        let progress_total = workbench_progress_total(total, scoped);')
    edit(file,'            pf(0, total, "", "");','            pf(0, progress_total, "", "");')
    edit(file,'                        pf(idx + 1, total, "", "");','                        pf(idx + 1, progress_total, "", "");')
    # Remove the moved block rather than leaving a second misleading policy.
    p=ROOT/file;s=p.read_text(encoding='utf-8');start=s.index('        /* Original configuration moved into the tested policy helper.');end=s.index('        */',start)+len('        */\n');s=s[:start]+s[end:];p.write_text(s,encoding='utf-8')
    with p.open('a',encoding='utf-8') as f:f.write('''
fn workbench_progress_total(header:usize,scoped:bool)->usize {if scoped {0}else{header}}
fn workbench_scan_config(config:&GoodArtifactScannerConfig,scoped:bool)->BackpackScanConfig {
    BackpackScanConfig{delay_scroll:config.delay_scroll,panel_wait:PanelWaitMode::Fingerprint{timeout_ms:config.panel_timeout,initial_wait_ms:config.initial_wait},
        extra_delay:config.extra_delay,detail_panel_rect:Some(ARTIFACT_DETAIL_PANEL_RECT),grid_vote_schedule:GridVoteSchedule::for_page,
        probe_last_cell_per_page:false,detect_grid_duplicates:false,detect_empty_cells:scoped,min_items_before_visual_end:0}
}
#[cfg(test)] mod workbench_filtered_scan_tests {
    use super::*;
    #[test] fn scoped_scan_uses_visual_end_even_when_header_says_2143(){
        let config=workbench_scan_config(&GoodArtifactScannerConfig::default(),true);
        assert!(config.detect_empty_cells);assert_eq!(config.min_items_before_visual_end,0);
        assert!(!config.detect_grid_duplicates);assert_eq!(workbench_progress_total(2143,true),0);
    }
    #[test] fn full_inventory_keeps_count_audit_and_fast_traversal(){
        let config=workbench_scan_config(&GoodArtifactScannerConfig::default(),false);
        assert!(!config.detect_empty_cells);assert_eq!(workbench_progress_total(2143,false),2143);
    }
}
''')
if __name__=='__main__':main()
