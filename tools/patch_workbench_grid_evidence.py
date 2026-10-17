"""Keep occupancy evidence available without enabling duplicate shortcuts."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]/'vendor/goodscanner'

def edit(name,old,new):
    p=ROOT/name;s=p.read_text(encoding='utf-8')
    if s.count(old)!=1:raise RuntimeError(f'Grid evidence patch context mismatch: {name}: {old[:80]}')
    p.write_text(s.replace(old,new),encoding='utf-8')

def main():
    file='genshin/src/scanner/common/backpack_scanner.rs'
    edit(file,'''    /// Enable duplicate grid cell detection. When true, at the start of each
    /// page the grid cells are sampled.''','''    /// Enable duplicate grid cell detection. Page samples are also collected
    /// independently when detect_empty_cells is enabled.''')
    edit(file,'/// Panel pool rect — substats + set name region whose pixel sum changes', '''impl BackpackScanConfig {
    /// Occupancy detection consumes the same page evidence as duplicate checks.
    /// Keeping duplicate shortcuts disabled must not remove occupancy evidence.
    pub(crate) fn capture_grid_evidence(&self,capture:impl FnOnce()->Result<RgbImage>)->Result<Option<RgbImage>> {
        if self.detect_grid_duplicates || self.detect_empty_cells {capture().map(Some)} else {Ok(None)}
    }
}

/// Panel pool rect — substats + set name region whose pixel sum changes''')
    edit(file,'''                if config.detect_grid_duplicates {
                    match self.capture_game_with_retry() {
                        Ok(grid_img) => {''','''                {
                    match config.capture_grid_evidence(||self.capture_game_with_retry()) {
                        Ok(Some(grid_img)) => {''')
    edit(file,'''                        Err(e) if config.detect_empty_cells => {''','''                        Ok(None) => {},
                        Err(e) if config.detect_empty_cells => {''')
    edit(file,'''    #[test]
    fn seeded_first_cell_remains_preselected_without_a_successful_probe() {''','''    #[test]
    fn occupancy_samples_keep_identical_cards_and_distinguish_an_empty_tail() {
        let config=BackpackScanConfig{delay_scroll:0,panel_wait:PanelWaitMode::Fingerprint{timeout_ms:100,initial_wait_ms:0},extra_delay:0,
            detail_panel_rect:None,grid_vote_schedule:GridVoteSchedule::for_page,probe_last_cell_per_page:false,
            detect_grid_duplicates:false,detect_empty_cells:true,min_items_before_visual_end:0};
        let scaler=CoordScaler::new(1920,1080);
        let mut frame=RgbImage::from_pixel(1920,1080,Rgb([25,35,45]));
        fill_grid_cell(&mut frame,&scaler,0,0,Rgb([200,140,70]));
        fill_grid_cell(&mut frame,&scaler,0,1,Rgb([200,140,70]));
        let frame=config.capture_grid_evidence(||Ok(frame)).unwrap().expect("occupancy evidence must exist");
        let samples=sample_grid_cells(&frame,&scaler,0,1,1,0,3);
        assert_eq!(samples.len(),3);assert!(samples.iter().all(|s|!s.is_empty()));
        let mut occupied=Vec::new();remember_occupied_samples(&mut occupied,&samples[0]);
        assert!(occupied.iter().any(|s|visual_samples_similar(s,&samples[1])));
        assert!(!occupied.iter().any(|s|visual_samples_similar(s,&samples[2])));
        assert!(!config.detect_grid_duplicates); // Both occupied cards remain scan targets.
    }

    #[test]
    fn seeded_first_cell_remains_preselected_without_a_successful_probe() {''')
    # Exercise the actual configuration factory with the actual capture gate.
    file='genshin/src/scanner/artifact/scanner.rs'
    edit(file,'''    #[test] fn full_inventory_keeps_count_audit_and_fast_traversal(){''','''    #[test] fn filtered_scan_captures_occupancy_evidence_with_duplicate_shortcuts_disabled(){
        let config=workbench_scan_config(&GoodArtifactScannerConfig::default(),true);
        assert!(!config.detect_grid_duplicates);
        let mut captures=0;
        let frame=config.capture_grid_evidence(||{captures+=1;Ok(RgbImage::from_pixel(8,8,image::Rgb([17,23,41])))}).unwrap().unwrap();
        assert_eq!(captures,1);assert_eq!(frame.get_pixel(0,0).0,[17,23,41]);
        assert!(config.capture_grid_evidence(||Err(anyhow::anyhow!("capture unavailable"))).is_err());
    }
    #[test] fn unfiltered_scan_does_not_add_page_capture_work(){
        let config=workbench_scan_config(&GoodArtifactScannerConfig::default(),false);
        assert!(config.capture_grid_evidence(||panic!("unexpected full-scan page capture")).unwrap().is_none());
    }
    #[test] fn duplicate_detection_keeps_its_existing_page_evidence(){
        let mut config=workbench_scan_config(&GoodArtifactScannerConfig::default(),false);
        config.detect_grid_duplicates=true;
        assert!(config.capture_grid_evidence(||Ok(RgbImage::new(1,1))).unwrap().is_some());
    }
    #[test] fn full_inventory_keeps_count_audit_and_fast_traversal(){''')

if __name__=='__main__':main()
