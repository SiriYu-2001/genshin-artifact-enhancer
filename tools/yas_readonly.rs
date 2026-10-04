// Read-only adapter: no scan iteration, input injection, or enhancement commands.
use anyhow::Result;
use yas::game_info::GameInfoBuilder;
use yas::window_info::load_window_info_repo;
use yas_scanner_genshin::application::ArtifactScannerApplication;
use yas_scanner_genshin::scanner::GenshinArtifactScanner;
use yas::ocr::{ImageToText, PPOCRModel};
use yas::capture::{Capturer, GenericCapturer};
use std::io::{BufRead, Write};

struct RegionModels {
    general: PPOCRModel,
    game_text: Box<dyn ImageToText<image::RgbImage> + Send + Sync>,
}

fn region_model() -> Result<RegionModels> {
    let general = match (std::env::var_os("YAS_GENERAL_MODEL"), std::env::var_os("YAS_GENERAL_DICT")) {
        (Some(model), Some(dict)) => PPOCRModel::new_from_file(model, dict)?,
        (None, None) => {
            let bytes = include_bytes!("../scanner/artifact_scanner/models/PP-OCRv5_mobile_rec.onnx");
            let dict = include_str!("../scanner/artifact_scanner/models/ppocrv5_dict.txt");
            let mut words: Vec<String> = dict.lines().map(|s| s.to_string()).collect();
            words.push(" ".to_string());
            PPOCRModel::new(bytes, words)?
        },
        _ => anyhow::bail!("Both YAS_GENERAL_MODEL and YAS_GENERAL_DICT are required"),
    };
    Ok(RegionModels {
        general,
        game_text: Box::new(yas::ocr::yas_ocr_model!(
            "../scanner/artifact_scanner/models/model_training.onnx",
            "../scanner/artifact_scanner/models/index_2_word.json")?),
    })
}

fn regions(image: &image::RgbImage, layout_path: &str, model: &RegionModels) -> Result<serde_json::Value> {
    let layout: serde_json::Value = serde_json::from_slice(&std::fs::read(layout_path)?)?;
    let mut texts = serde_json::Map::new();
    for region in layout["regions"].as_array().ok_or_else(|| anyhow::anyhow!("Missing regions"))? {
        let name = region["name"].as_str().ok_or_else(|| anyhow::anyhow!("Missing name"))?;
        let rect: Vec<u32> = region["rect"].as_array().ok_or_else(|| anyhow::anyhow!("Missing rect"))?.iter()
            .map(|v| v.as_u64().map(|n| n as u32).ok_or_else(|| anyhow::anyhow!("Bad coordinate"))).collect::<Result<_>>()?;
        anyhow::ensure!(rect.len() == 4 && rect[2] > 0 && rect[3] > 0 && rect[0] + rect[2] <= image.width() && rect[1] + rect[3] <= image.height(), "Region outside image");
        let mut crop = image::imageops::crop_imm(image, rect[0], rect[1], rect[2], rect[3]).to_image();
        if region["trim_text"].as_bool().unwrap_or(false) {
            // The calibrated stat line has neutral, light text. Excess blank
            // width degrades recognition of short lines. Locate the end of
            // the contiguous text run without changing any retained pixels.
            let ink: Vec<bool> = (0..crop.width()).map(|x| {
                (0..crop.height()).filter(|&y| {
                    let p = crop.get_pixel(x, y);
                    let low = *p.0.iter().min().unwrap();
                    let high = *p.0.iter().max().unwrap();
                    low >= 140 && high - low < 65
                }).count() >= 2
            }).collect();
            if let Some(start) = ink.iter().position(|&v| v) {
                if start < 24 {
                    let mut last = start;
                    let mut gap = 0;
                    for x in start + 1..ink.len() {
                        if ink[x] { last = x; gap = 0; } else { gap += 1; }
                        if gap >= 16 { break; }
                    }
                    let width = (last as u32 + 7).min(crop.width());
                    if width >= 40 {
                        crop = image::imageops::crop_imm(&crop, 0, 0, width, crop.height()).to_image();
                    }
                }
            }
        }
        if region["remove_gold"].as_bool().unwrap_or(false) {
            // The current level is white; the prospective level increment is gold.
            for pixel in crop.pixels_mut() {
                if pixel[0] as i32 - pixel[2] as i32 > 40 && pixel[1] as i32 - pixel[2] as i32 > 20 {
                    *pixel = image::Rgb([25,25,25]);
                }
            }
        }
        let text = if region["backend"] == "yas" { model.game_text.image_to_text(&crop, false)? }
                   else { model.general.image_to_text(&crop, false)? };
        texts.insert(name.to_string(), serde_json::Value::String(text));
    }
    Ok(serde_json::json!({"text": texts}))
}

fn read_saved_image(input: &str, layout_path: &str) -> Result<()> {
    let image = image::io::Reader::open(input)?.with_guessed_format()?.decode()?.to_rgb8();
    let model = region_model()?;
    let result = regions(&image, layout_path, &model)?;
    std::fs::write("observation.json", serde_json::to_vec_pretty(&result)?)?;
    Ok(())
}

fn main() -> Result<()> {
    let matches = ArtifactScannerApplication::build_command()
        .arg(clap::Arg::new("ocr-image").long("ocr-image").num_args(1))
        .arg(clap::Arg::new("ocr-batch").long("ocr-batch").num_args(1))
        .arg(clap::Arg::new("serve").long("serve").action(clap::ArgAction::SetTrue)).get_matches();
    if let Some(input) = matches.get_one::<String>("ocr-batch") {
        let cases: Vec<serde_json::Value> = serde_json::from_slice(&std::fs::read(input)?)?;
        anyhow::ensure!(!cases.is_empty(), "Empty batch");
        let started = std::time::Instant::now();
        let model = region_model()?;
        let load_ms = started.elapsed().as_secs_f64() * 1000.0;
        let mut results = Vec::new();
        for (index, case) in cases.iter().enumerate() {
            let frame = image::open(case["image"].as_str().ok_or_else(|| anyhow::anyhow!("Image required"))?)?.to_rgb8();
            let layout = case["layout"].as_str().ok_or_else(|| anyhow::anyhow!("Layout required"))?;
            if index == 0 { regions(&frame, layout, &model)?; }
            let started = std::time::Instant::now();
            let value = regions(&frame, layout, &model)?;
            results.push(serde_json::json!({"id": case["id"], "text": value["text"],
                "region_ms": started.elapsed().as_secs_f64() * 1000.0}));
        }
        std::fs::write("batch-observations.json", serde_json::to_vec_pretty(&serde_json::json!({
            "load_ms": load_ms, "results": results}))?)?;
        return Ok(());
    }
    if let Some(input) = matches.get_one::<String>("ocr-image") {
        return read_saved_image(input, matches.get_one::<String>("observe-layout").ok_or_else(|| anyhow::anyhow!("Layout required"))?);
    }
    let game = GameInfoBuilder::new().add_local_window_name("原神").add_local_window_name("Genshin Impact").build()?;
    let repo = load_window_info_repo!(
        "../../window_info/windows1366x768.json",
        "../../window_info/windows1024x768.json",
        "../../window_info/windows1600x900.json",
        "../../window_info/windows1280x960.json",
        "../../window_info/windows1440x900.json",
        "../../window_info/windows2100x900.json",
        "../../window_info/windows2560x1440.json",
        "../../window_info/windows3440x1440.json",
        "../../window_info/windows3840x2160.json"
    );
    if matches.get_flag("serve") {
        let model = region_model()?;
        let capturer = GenericCapturer::new()?;
        for line in std::io::stdin().lock().lines() {
            let request: serde_json::Value = serde_json::from_str(&line?)?;
            if request["kind"] == "shutdown" { break; }
            let result: Result<serde_json::Value> = (|| {
                let handles: Vec<_> = yas::utils::iterate_window().into_iter().filter(|&h| {
                    matches!(yas::utils::get_window_title(h).as_deref(), Some("原神") | Some("Genshin Impact"))
                }).collect();
                anyhow::ensure!(handles.len() == 1, "Game window not unique");
                let rect = yas::utils::get_client_rect(handles[0])?;
                anyhow::ensure!(rect.width == 1920 && rect.height == 1080, "Uncalibrated game resolution");
                let output = std::path::PathBuf::from(request["output_dir"].as_str().ok_or_else(|| anyhow::anyhow!("Output directory required"))?);
                std::fs::create_dir_all(&output)?;
                if request["kind"] == "observe" {
                    let frame = capturer.capture_rect(rect)?;
                    frame.save(output.join("game.png"))?;
                    let mut value = regions(&frame, request["layout"].as_str().ok_or_else(|| anyhow::anyhow!("Layout required"))?, &model)?;
                    value["window"] = serde_json::json!({"left": rect.left, "top": rect.top, "width": rect.width, "height": rect.height});
                    std::fs::write(output.join("observation.json"), serde_json::to_vec_pretty(&value)?)?;
                    Ok(value)
                } else if request["kind"] == "read-current" {
                    let mut current_game = game.clone();
                    current_game.window = rect;
                    let scanner = GenshinArtifactScanner::from_arg_matches(&repo, &matches, current_game)?;
                    let records = scanner.read_current()?;
                    std::fs::write(output.join("observed-artifacts.json"), serde_json::to_vec_pretty(&records)?)?;
                    Ok(serde_json::to_value(records)?)
                } else { anyhow::bail!("Unknown read-only command") }
            })();
            let reply = match result {
                Ok(data) => serde_json::json!({"id": request["id"], "ok": true, "data": data}),
                Err(error) => serde_json::json!({"id": request["id"], "ok": false, "error": format!("{:#}", error)}),
            };
            println!("{}", reply);
            std::io::stdout().flush()?;
        }
        return Ok(());
    }
    let scanner = GenshinArtifactScanner::from_arg_matches(&repo, &matches, game)?;
    if let Some(layout) = matches.get_one::<String>("observe-layout") {
        scanner.observe_layout(layout)?;
    } else if matches.get_flag("read-current") {
        std::fs::write("observed-artifacts.json", serde_json::to_vec_pretty(&scanner.read_current()?)?)?;
    } else {
        anyhow::bail!("Only --observe-layout or --read-current supported");
    }
    Ok(())
}
