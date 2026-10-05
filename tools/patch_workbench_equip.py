"""One equipment traversal with immediate per-slot ownership receipts."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]/'vendor/goodscanner'

def main():
    path=ROOT/'genshin/src/server.rs';s=path.read_text(encoding='utf-8')
    old='"enhancementVersion":2';assert s.count(old)==1
    path.write_text(s.replace(old,old+',"inlineEquipVerification":true'),encoding='utf-8')
    path=ROOT/'genshin/src/manager/ui_actions.rs';s=path.read_text(encoding='utf-8')
    old='''        let (verdict,details)=full_match_from_panel_verbose(&panel,target,ocr,mappings)?;
        let owner_img=min_channel_preprocess(&workbench_crop(&frame,SEL_EQUIP_OWNER_RECT));'''
    assert s.count(old)==1
    s=s.replace(old,'''        let (verdict,details)=full_match_from_panel_verbose(&panel,target,ocr,mappings)?;
        // A complete, different item is not a transient OCR failure.
        if !save_failure && verdict==MatchVerdict::CleanReject {return Ok(false);}
        let owner_img=min_channel_preprocess(&workbench_crop(&frame,SEL_EQUIP_OWNER_RECT));''')
    path.write_text(s,encoding='utf-8')
    path=ROOT/'genshin/src/manager/equip_manager.rs';s=path.read_text(encoding='utf-8')
    filter_begin=s.index('            // Apply set filter if it differs from what\'s active')
    filter_end=s.index('            // Click slot tab',filter_begin)
    filtering=s[filter_begin:filter_end]
    s=s[:filter_begin]+s[filter_end:]
    marker='            if target.preflight_only {'
    assert s.count(marker)==1
    s=s.replace(marker,'            if target.preflight_only || target.verify_only {\n'+filtering+'            }\n'+marker)
    begin=s.index('            // Check if the currently equipped artifact already matches (live OCR check)')
    end=s.index('\n        }\n\n        // Return to character detail screen',begin)
    s=s[:begin]+r'''            // Single traversal: current target -> locate if needed -> equip once
            // -> same-slot receipt. No whole-loadout preflight/verification pass.
            let outcome:anyhow::Result<InstructionStatus>=(|| {
                if ui_actions::workbench_verify_equipped(ctrl,&target.artifact,&target.target_location,
                        ocr,character_ocr,&self.mappings,false)? {
                    return Ok(InstructionStatus::AlreadyCorrect);
                }
                WORKBENCH_FILTER_AFTER_CURRENT_CHECK
                let selected=ui_actions::check_current_artifact_matches(ctrl,&target.artifact,ocr,&self.mappings)?;
                if !selected {
                    anyhow::ensure!(ui_actions::find_artifact_in_grid(ctrl,&target.artifact,ocr,&self.mappings,false)?,
                        "Exact equipment target not found; no equip click");
                }
                anyhow::ensure!(!ctrl.is_cancelled(),"Cancelled before equip click");
                let changed=ui_actions::equip_selected_artifact(ctrl,ocr,"workbench_inline")?;
                anyhow::ensure!(ui_actions::workbench_verify_equipped(ctrl,&target.artifact,&target.target_location,
                        ocr,character_ocr,&self.mappings,true)?,
                    "Equipment result not confirmed; equip click will not be replayed");
                Ok(if changed {InstructionStatus::Success} else {InstructionStatus::AlreadyCorrect})
            })();
            match outcome {
                Ok(status)=>{results.insert(target.result_id.clone(),InstructionResult::outcome(target.result_id.clone(),status));},
                Err(e)=>{
                    results.insert(target.result_id.clone(),InstructionResult::failure(target.result_id.clone(),InstructionStatus::UiError,
                        "本部位换装或复核未完成，停止后续操作，不重复点击装备。",
                        "This slot was not confirmed; remaining operations stopped without replaying equip.",Some(&e)));
                    break;
                },
            }
'''.replace('                WORKBENCH_FILTER_AFTER_CURRENT_CHECK',filtering)+s[end:]
    path.write_text(s,encoding='utf-8')

if __name__=='__main__':
    main()
    import runpy
    runpy.run_path(str(Path(__file__).with_name('patch_workbench_focus.py')),run_name='__main__')
