"""Stop owned automation on focus loss before input or OCR of another app."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]/'vendor/goodscanner'
def main():
    path=ROOT/'genshin/src/scanner/common/game_controller.rs';s=path.read_text(encoding='utf-8')
    marker='// Focus methods.\nimpl GenshinGameController {'
    assert s.count(marker)==1
    s=s.replace(marker,'''// Workbench input/capture gate. A cancelled job never resumes when focus returns.
impl GenshinGameController {
    fn workbench_foreground(&self) -> bool {
        if self.cancel.is_cancelled() {return false;}
        if std::env::var_os("WORKBENCH_GOOD_TOKEN").is_none() {return true;}
        #[cfg(target_os="windows")] {
            let hwnd=self.game_info.hwnd as windows_sys::Win32::Foundation::HWND;
            let foreground=unsafe{windows_sys::Win32::UI::WindowsAndMessaging::GetForegroundWindow()};
            if !hwnd.is_null() && foreground==hwnd {return true;}
            self.cancel.cancel(yas::cancel::StopReason::GameLost);
            let job=std::env::var("WORKBENCH_ACTIVE_JOB").unwrap_or_default();
            if uuid::Uuid::parse_str(&job).is_ok() {
                let _=std::fs::create_dir_all("failures");
                let record=serde_json::json!({"jobId":job,"reason":"game_focus_lost","inputBlocked":true});
                let _=std::fs::write(format!("failures/input-{}.json",job),record.to_string());
            }
            log::warn!("Game focus lost; further input and desktop capture blocked");
            return false;
        }
        #[cfg(not(target_os="windows"))] {true}
    }
    fn workbench_capture_guard(&self) -> Result<()> {
        anyhow::ensure!(self.workbench_foreground(),"Game focus lost or task cancelled; capture blocked");
        Ok(())
    }
}

'''+marker)
    # Includes the second check after the mouse-move settling delay.
    assert s.count('if self.cancel.is_cancelled() { return; }')==6
    s=s.replace('if self.cancel.is_cancelled() { return; }','if !self.workbench_foreground() { return; }')
    s=s.replace('''        self.capturer.capture_rect(self.game_info.window)''','''        self.workbench_capture_guard()?;
        let image=self.capturer.capture_rect(self.game_info.window)?;
        self.workbench_capture_guard()?;
        Ok(image)''')
    s=s.replace('''        self.capturer
            .capture_relative_to(rect, self.game_info.window.origin())''','''        self.workbench_capture_guard()?;
        let image=self.capturer.capture_relative_to(rect, self.game_info.window.origin())?;
        self.workbench_capture_guard()?;
        Ok(image)''')
    old='''            let im = self
                .capturer
                .capture_relative_to(rect, self.game_info.window.origin())?;'''
    assert s.count(old)==2
    s=s.replace(old,'''            self.workbench_capture_guard()?;
'''+old+'''
            self.workbench_capture_guard()?;''')
    s=s.replace('''        self.capturer.capture_color(pos)''','''        self.workbench_capture_guard()?;
        let color=self.capturer.capture_color(pos)?;
        self.workbench_capture_guard()?;
        Ok(color)''')
    path.write_text(s,encoding='utf-8')
if __name__=='__main__':main()
