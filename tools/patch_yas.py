"""Reproducible local changes against upstream 614245f; do not rerun on a patched tree."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "vendor/yas"


def edit(name, old, new):
    path = ROOT / name
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"Upstream context mismatch: {name}: {old[:70]}")
    path.write_text(text.replace(old, new), encoding="utf-8")


edit("yas/Cargo.toml", 'ort = { version = "2.0.0-rc.10", optional = true }',
     'ort = { version = "=2.0.0-rc.10", optional = true, default-features = false, features = ["std", "ndarray", "load-dynamic"] }')
base = "yas-genshin/src/scanner/artifact_scanner/"
edit(base + "scan_result.rs", "#[derive(Debug, Hash, Clone, PartialEq, Eq)]", "#[derive(Debug, Hash, Clone, PartialEq, Eq, serde::Serialize)]")
edit(base + "scan_result.rs", "    pub level: i32,", "    pub level: i32,\n    pub pending: [bool; 4],\n    pub special: bool,")
worker = base + "artifact_scanner_worker.rs"
edit(worker, "    fn get_model_for_backend", "    pub fn get_model_for_backend")
edit(worker, "    fn scan_item_image", "    pub fn scan_item_image")
text = (ROOT / worker).read_text(encoding="utf-8")
start, end = text.index("    fn parse_level"), text.index("    /// 处理词条文本")
text = text[:start] + '''    fn parse_level(s: &str) -> Result<i32> {
        let trimmed = s.trim();
        let number = trimmed.strip_prefix('+').unwrap_or(trimmed);
        anyhow::ensure!(!number.is_empty() && number.chars().all(|c| c.is_ascii_digit()), "Unreadable level: {:?}", s);
        let level = number.parse::<i32>()?;
        anyhow::ensure!((0..=20).contains(&level), "Level out of range: {}", level);
        Ok(level)
    }

''' + text[end:]
(ROOT / worker).write_text(text, encoding="utf-8")
edit(worker, "                level: 0,", "                level: 0,\n                pending: [false; 4],\n                special: false,")
edit(worker, '''            level: {
                let mut lv = Self::parse_level(&str_level)?;
                if has_unactivated {
                    lv = 4;
                }
                lv
            },''', '''            level: Self::parse_level(&str_level)?,
            pending: [u0, u1, u2, u3],
            special: shift_offset != 0.0,''')
edit(worker, "match s.find('（')", "match s.find(['（', '('])")

scanner = base + "artifact_scanner.rs"
text = (ROOT / scanner).read_text(encoding="utf-8")
start = text.index("        let max_count = Self::MAX_COUNT as i32;")
end = text.index("        let im =", start)
text = text[:start] + '''        if count > 0 {
            anyhow::ensure!(count <= 10000, "Unreasonable scan count");
            return Ok(count);
        }

''' + text[end:]
start = text.index("        let filtered: String =")
end = text.index("    pub fn scan", start)
text = text[:start] + '''        let re = regex::Regex::new(r"^\\s*(?:圣遗物)?\\s*(\\d+)\\s*/\\s*(\\d+)\\s*$")?;
        let caps = re.captures(&s).ok_or_else(|| anyhow::anyhow!("Unreadable inventory count: {:?}", s))?;
        let count = caps[1].parse::<i32>()?;
        let capacity = caps[2].parse::<i32>()?;
        // Temporary overflow above the displayed capacity is possible.
        anyhow::ensure!(count > 0 && count <= 10000 && capacity > 0 && capacity <= 10000, "Invalid inventory count/capacity");
        Ok(count)
    }

    pub fn read_current(&self) -> Result<Vec<GenshinArtifactScanResult>> {
        let worker = ArtifactScannerWorker::new(self.window_info.clone(), self.scanner_config.clone())?;
        // Lock is not inferred from a potentially unrelated grid position.
        let item = SendItem { panel_image: self.capture_panel()?, star: self.get_star()?, list_image: None };
        Ok(vec![worker.scan_item_image(item, false, 1)?])
    }

    pub fn observe_layout(&self, layout_path: &str) -> Result<()> {
        let image = self.capturer.capture_rect(self.game_info.window)?;
        image.save("game.png")?;
        let layout: serde_json::Value = serde_json::from_slice(&std::fs::read(layout_path)?)?;
        let model = Self::get_image_to_text("ppocrv5")?;
        let mut texts = serde_json::Map::new();
        for region in layout["regions"].as_array().ok_or_else(|| anyhow::anyhow!("Missing regions"))? {
            let name = region["name"].as_str().ok_or_else(|| anyhow::anyhow!("Missing region name"))?;
            let rect: Vec<u32> = region["rect"].as_array().ok_or_else(|| anyhow::anyhow!("Missing rect"))?.iter()
                .map(|v| v.as_u64().map(|n| n as u32).ok_or_else(|| anyhow::anyhow!("Invalid coordinate")))
                .collect::<Result<_>>()?;
            anyhow::ensure!(rect.len() == 4 && rect[2] > 0 && rect[3] > 0 && rect[0] + rect[2] <= image.width() && rect[1] + rect[3] <= image.height(), "Region outside game client");
            let crop = image::imageops::crop_imm(&image, rect[0], rect[1], rect[2], rect[3]).to_image();
            texts.insert(name.to_string(), serde_json::Value::String(model.image_to_text(&crop, false)?));
        }
        let value = serde_json::json!({"window": {"left": self.game_info.window.left, "top": self.game_info.window.top,
            "width": self.game_info.window.width, "height": self.game_info.window.height}, "text": texts});
        std::fs::write("observation.json", serde_json::to_vec_pretty(&value)?)?;
        Ok(())
    }

''' + text[end:]
text = text.replace("        let worker = ArtifactScannerWorker::new(\n", '        std::fs::write("scan-count.json", serde_json::to_vec(&serde_json::json!({"requested": count}))?)?;\n        let worker = ArtifactScannerWorker::new(\n')
(ROOT / scanner).write_text(text, encoding="utf-8")

app = "yas-genshin/src/application/artifact_scanner.rs"
edit(app, "        cmd\n", '''        cmd.arg(clap::Arg::new("read-current").long("read-current").action(clap::ArgAction::SetTrue))
            .arg(clap::Arg::new("observe-layout").long("observe-layout").num_args(1))
''')
edit(app, "        let result = scanner.scan()?;", '''        if let Some(layout) = arg_matches.get_one::<String>("observe-layout") {
            return scanner.observe_layout(layout);
        }
        let result = if arg_matches.get_flag("read-current") { scanner.read_current()? } else { scanner.scan()? };
        std::fs::write("observed-artifacts.json", serde_json::to_vec_pretty(&result)?)?;
        let mut observations = Vec::new();
        for raw in &result {
            if let Ok(artifact) = GenshinArtifact::try_from(raw) {
                let mut stats = Vec::new();
                for (index, stat) in [&artifact.sub_stat_1, &artifact.sub_stat_2, &artifact.sub_stat_3, &artifact.sub_stat_4].iter().enumerate() {
                    if let Some(stat) = stat {
                        let key = stat.name.to_good();
                        let value = if key.ends_with('_') { stat.value * 100.0 } else { stat.value };
                        stats.push(serde_json::json!({"key": key, "value": value, "pending": raw.pending[index]}));
                    }
                }
                observations.push(serde_json::json!({"index": raw.index, "name": raw.name,
                    "setKey": artifact.set_name.to_good(), "slotKey": artifact.slot.to_good(),
                    "mainStatKey": artifact.main_stat.name.to_good(), "level": raw.level,
                    "rarity": raw.star, "lock": raw.lock, "equip_raw": raw.equip,
                    "special": raw.special, "substats": stats}));
            }
        }
        std::fs::write("enhancer-artifacts.json", serde_json::to_vec_pretty(&observations)?)?;''')
edit("yas-application/src/bin/yas_artifact.rs", '            error!("error: {:?}", e);\n            press_any_key_to_continue();',
     '            error!("error: {:?}", e);\n            std::process::exit(1);')

# Fix GDI ownership/cleanup; capture is always from screen into memory.
capture = ROOT / "yas/src/capture/winapi_capturer.rs"
text = capture.read_text(encoding="utf-8")
start, end = text.index("unsafe fn unsafe_capture"), text.index("pub struct WinapiCapturer")
text = text[:start] + '''unsafe fn unsafe_capture(rect: Rect<i32>) -> Result<Vec<u8>> {
    if rect.width <= 0 || rect.height <= 0 || rect.width > 16384 || rect.height > 16384 {
        return Err(anyhow!("Invalid capture dimensions"));
    }
    let dc_window = GetDC(null_mut());
    if dc_window.is_null() { return Err(anyhow!("GetDC failed")); }
    let dc_mem = CreateCompatibleDC(dc_window);
    if dc_mem.is_null() { ReleaseDC(null_mut(), dc_window); return Err(anyhow!("CreateCompatibleDC failed")); }
    let hbm = CreateCompatibleBitmap(dc_window, rect.width, rect.height);
    if hbm.is_null() { DeleteDC(dc_mem); ReleaseDC(null_mut(), dc_window); return Err(anyhow!("CreateCompatibleBitmap failed")); }
    let original = SelectObject(dc_mem, hbm as *mut c_void);
    let result = (|| {
        if original.is_null() || original as isize == -1 { return Err(anyhow!("SelectObject failed")); }
        if BitBlt(dc_mem, 0, 0, rect.width, rect.height, dc_window, rect.left, rect.top, SRCCOPY) == 0 {
            return Err(anyhow!("BitBlt failed"));
        }
        // GetDIBits requires the bitmap not to be selected in a DC.
        SelectObject(dc_mem, original);
        let mut info: BITMAPINFO = std::mem::zeroed();
        info.bmiHeader.biSize = size_of::<BITMAPINFOHEADER>() as u32;
        info.bmiHeader.biWidth = rect.width;
        info.bmiHeader.biHeight = rect.height;
        info.bmiHeader.biPlanes = 1;
        info.bmiHeader.biBitCount = 32;
        info.bmiHeader.biCompression = BI_RGB;
        let mut buffer = vec![0u8; rect.width as usize * rect.height as usize * 4];
        let rows = GetDIBits(dc_window, hbm, 0, rect.height as u32, buffer.as_mut_ptr() as *mut c_void, &mut info, DIB_RGB_COLORS);
        if rows != rect.height { return Err(anyhow!("GetDIBits returned incomplete image")); }
        Ok(buffer)
    })();
    if !original.is_null() && original as isize != -1 { SelectObject(dc_mem, original); }
    DeleteObject(hbm as *mut c_void);
    DeleteDC(dc_mem);
    ReleaseDC(null_mut(), dc_window);
    result
}

''' + text[end:]
capture.write_text(text, encoding="utf-8")
(ROOT / "yas-genshin/src/bin/yas_readonly.rs").write_text(
    (Path(__file__).parent / "yas_readonly.rs").read_text(encoding="utf-8"), encoding="utf-8")
print("yas source patched")
