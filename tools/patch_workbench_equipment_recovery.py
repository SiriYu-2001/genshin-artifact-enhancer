"""Never infer equipment-list occupancy or pagination from artifact OCR values."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]/'vendor/goodscanner'


def main():
    p=ROOT/'genshin/src/manager/ui_actions.rs';s=p.read_text(encoding='utf-8')
    start=s.index('    let mut prev_fingerprint = String::new();',s.index('fn find_artifact_in_grid_inner('))
    end=s.index('    for page in 0..max_pages {',start)
    s=s[:start]+s[end:]
    for line in ('        let page_fingerprint: Arc<Mutex<String>> = Arc::new(Mutex::new(String::new()));\n',
                 '        let page_ended_flag = Arc::new(AtomicBool::new(false));\n',
                 '            let fp_out = page_fingerprint.clone();\n','            let ended = page_ended_flag.clone();\n'):
        assert s.count(line)==1,line;s=s.replace(line,'')
    begin=s.index('                    // Track full OCR fingerprint per cell for empty detection.')
    end=s.index('                    for (row, col, panel_img) in rx {',begin)
    s=s[:begin]+s[end:]
    begin=s.index('                        // Row 0: collect level texts as page fingerprint')
    end=s.index('                        checked.fetch_add(1, Ordering::SeqCst);',begin)
    s=s[:begin]+'''                        // Equal level/substat values are common on real artifacts.
                        // Every visible cell remains a candidate, including unreadable levels.
'''+s[end:]
    s=s.replace('                        let do_full = level_matches;','                        let do_full = workbench_selection_candidate(level,target.level);')
    s=s.replace('                                    if level_matches {\n                                        retries.lock().unwrap().push((row, col));','                                    if do_full {\n                                        retries.lock().unwrap().push((row, col));')
    s=s.replace('                        let level_matches = level == target.level;\n','')
    begin=s.index('        // Check end of list (empty cells)',s.index('fn find_artifact_in_grid_inner('))
    end=s.index('        // Scroll to next page',begin)
    s=s[:begin]+s[end:]
    begin=s.index('        // Scroll to next page',s.index('fn find_artifact_in_grid_inner('))
    end=s.index('\n    }\n\n    log_debug!',begin)
    s=s[:begin]+'''        // Only real scroll movement decides pagination. Equal OCR values,
        // equal retry counts and animated detail panels are not list boundaries.
        if !workbench_scroll_selection(ctrl)? {return Ok(false);}
'''+s[end:]
    # Append self-contained visual evidence helpers and regression tests.
    s+='\n'+Path(__file__).with_name('workbench_equipment_scroll.rs').read_text(encoding='utf-8')
    p.write_text(s,encoding='utf-8')
    p=ROOT/'genshin/src/manager/equip_manager.rs';s=p.read_text(encoding='utf-8')
    begin=s.index('            // Apply set filter',s.index('let outcome:anyhow::Result<InstructionStatus>'))
    end=s.index('                let selected=',begin)
    s=s[:begin]+'''                if active_filter.as_ref()!=Some(&needed_filter) {
                    if needed_filter.len()==1 {
                        anyhow::ensure!(ui_actions::apply_set_filter(ctrl,&needed_filter[0],&self.mappings,ocr)?,
                            "Equipment set filter unverified");
                    } else {
                        let keys:Vec<&str>=needed_filter.iter().map(|s|s.as_str()).collect();
                        anyhow::ensure!(ui_actions::apply_multi_set_filter(ctrl,&keys,&self.mappings,ocr)?==keys.len(),
                            "Equipment set filter unverified");
                    }
                    active_filter=Some(needed_filter.clone());
                }
'''+s[end:]
    p.write_text(s,encoding='utf-8')


if __name__=='__main__':main()
