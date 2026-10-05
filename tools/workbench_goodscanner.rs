//! Headless host for the pinned GOODScanner library. No GUI automation or Agent.
use std::sync::{Arc, atomic::{AtomicBool, Ordering}};
use std::path::PathBuf;
use std::time::Duration;

struct FileLogger;
impl log::Log for FileLogger {
    fn enabled(&self, metadata: &log::Metadata) -> bool { metadata.level() <= log::Level::Info || metadata.target().contains("::manager::") }
    fn log(&self, record: &log::Record) {
        if self.enabled(record.metadata()) { eprintln!("{} {}", record.level(), record.args()); }
    }
    fn flush(&self) {}
}
static LOGGER: FileLogger = FileLogger;

fn main() -> anyhow::Result<()> {
    // Initialize before the HTTP diagnostic thread reads window dimensions.
    // Waiting until lazy GameInfo construction returns DPI-virtualized sizes.
    #[cfg(windows)] yas::utils::set_dpi_awareness();
    log::set_logger(&LOGGER).map_err(|_| anyhow::anyhow!("logger initialization failed"))?;
    log::set_max_level(log::LevelFilter::Debug);
    let mut args=std::env::args().skip(1);
    let first=args.next().ok_or_else(||anyhow::anyhow!("port or --replay-selection required"))?;
    if first=="--replay-enhancement" {
        let frame=image::open(args.next().ok_or_else(||anyhow::anyhow!("frame required"))?)?.to_rgb8();
        let request:genshin_scanner::manager::workbench_enhance::Request=serde_json::from_slice(&std::fs::read(args.next().ok_or_else(||anyhow::anyhow!("request required"))?)?)?;
        let backend=args.next().unwrap_or("ppocrv6tiny".into());
        let model=genshin_scanner::scanner::common::ocr_factory::create_ocr_model(&backend)?;
        let started=std::time::Instant::now();
        let observed=genshin_scanner::manager::workbench_enhance::observe_frame(&frame,model.as_ref(),&request)?;
        let mut result=serde_json::to_value(&observed)?;
        result["observationMs"]=serde_json::json!(started.elapsed().as_millis());
        result["visibleMaterialRarities"]=serde_json::to_value(genshin_scanner::manager::workbench_enhance::visible_materials(&frame,observed.material_count)?)?;
        println!("{}",result);
        return Ok(());
    }
    if first=="--replay-selection" {
        let frame=image::open(args.next().ok_or_else(||anyhow::anyhow!("frame required"))?)?.to_rgb8();
        let target:genshin_scanner::scanner::common::models::GoodArtifact=serde_json::from_slice(&std::fs::read(args.next().ok_or_else(||anyhow::anyhow!("target required"))?)?)?;
        let backend=args.next().unwrap_or("ppocrv4".into());
        let model=genshin_scanner::scanner::common::ocr_factory::create_ocr_model(&backend)?;
        let mappings=genshin_scanner::scanner::common::mappings::MappingManager::new(&Default::default())?;
        println!("{}",genshin_scanner::manager::ui_actions::workbench_replay_selection(&frame,&target,model.as_ref(),&mappings)?);
        return Ok(());
    }
    let port:u16=first.parse()?;
    let config_path=PathBuf::from(args.next().ok_or_else(|| anyhow::anyhow!("config required"))?);
    let shutdown_file=PathBuf::from(args.next().ok_or_else(|| anyhow::anyhow!("shutdown marker required"))?);
    anyhow::ensure!(args.next().is_none(), "unexpected arguments");
    anyhow::ensure!(std::env::var("WORKBENCH_GOOD_TOKEN").is_ok(), "local authorization missing");
    anyhow::ensure!(std::env::var_os("ORT_DYLIB_PATH").is_some(), "bundled ONNX runtime required");
    let mut config: genshin_scanner::cli::GoodUserConfig=serde_json::from_slice(&std::fs::read(config_path)?)?;
    config.lang="zh".into();config.save_on_cancel=false;
    yas::lang::set_lang("zh");
    let enabled=Arc::new(AtomicBool::new(true));let shutdown=Arc::new(AtomicBool::new(false));
    let flag=shutdown.clone();
    std::thread::spawn(move || {
        while !flag.load(Ordering::Relaxed) {
            if shutdown_file.exists() || std::env::var_os("WORKBENCH_STOP_FILE").map(|p|PathBuf::from(p).exists()).unwrap_or(false) {flag.store(true,Ordering::Relaxed);break;}
            std::thread::sleep(Duration::from_millis(50));
        }
    });
    std::thread::Builder::new().name("goodscanner-executor".into()).stack_size(32*1024*1024)
        .spawn(move || genshin_scanner::cli::run_server_core(&config,port,&Default::default(),enabled,shutdown,
                                                          true,true,false,false,None))?
        .join().map_err(|_|anyhow::anyhow!("GOODScanner executor panicked"))?
}
