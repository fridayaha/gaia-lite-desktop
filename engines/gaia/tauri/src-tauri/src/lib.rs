//! Gaia Lite 桌面壳——Tauri 2 + Python sidecar + axum 同源反代。
//!
//! 启动流程：
//!   1. 选空闲端口 P_backend（bind 127.0.0.1:0 拿 OS 分配，立刻释放）
//!   2. spawn sidecar：gaia-lite-backend，env PORT=P_backend + LITE_DB_PATH/WAREHOUSE
//!   3. 选空闲端口 P_proxy，起 axum 反代监听 127.0.0.1:P_proxy，转发到 P_backend
//!   4. webview 加载 http://127.0.0.1:P_proxy（同源，无 CORS）
//!   5. 退出时 kill sidecar
//!
//! sidecar 定位：开发模式从 tauri/sidecar/gaia-lite-backend/ 找；
//! 打包后从 app resource_dir/gaia-lite-backend/ 找（tauri.conf.json resources）。
//!
//! 不用 tauri-plugin-shell 的 Command（env 传递不便），直接 std::process::Command。

mod proxy;

use std::path::PathBuf;
use tokio::process::Child;
use std::sync::Arc;
use tauri::{Manager, RunEvent};
use tokio::sync::Mutex;

/// 选一个空闲端口：bind 127.0.0.1:0 让 OS 分配，拿端口后立刻关闭 socket。
/// 短暂的 TOCTOU 窗口可接受（桌面单用户场景，P_backend 在 spawn 前就被 sidecar 占住）。
fn pick_free_port() -> Result<u16, String> {
    let listener = std::net::TcpListener::bind("127.0.0.1:0")
        .map_err(|e| format!("bind free port failed: {e}"))?;
    Ok(listener.local_addr().unwrap().port())
}

/// sidecar 可执行路径（onefile + externalBin，文件名带平台 triplet）。
/// - 打包模式：app resource_dir/gaia-lite-backend-<triplet>[.exe]
/// - 开发模式：仓库 tauri/sidecar/gaia-lite-backend-<triplet>[.exe]
///
/// triplet 用 cfg! 宏编译期确定（与 cargo target 一致）：
///   mac-arm64: aarch64-apple-darwin
///   win-x64:   x86_64-pc-windows-msvc
/// externalBin 打包时按此命名，开发模式需把 onefile 产物重命名加 triplet 后缀。
fn sidecar_binary_name() -> String {
    let arch = if cfg!(target_arch = "aarch64") {
        "aarch64"
    } else if cfg!(target_arch = "x86_64") {
        "x86_64"
    } else {
        "unknown"
    };
    let (os, env, exe) = if cfg!(target_os = "macos") {
        ("apple", "darwin", "")
    } else if cfg!(target_os = "windows") {
        ("pc", "windows", ".exe")
    } else {
        ("unknown", "unknown", "")
    };
    format!("gaia-lite-backend-{arch}-{os}-{env}{exe}")
}

fn sidecar_binary_path(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    // externalBin 打包后 sidecar 落在主 bin 同目录（macOS Contents/MacOS/，
    // Windows 主 exe 同目录），文件名是 base 名（无 triplet——Tauri externalBin
    // 打包时去掉 source 的 triplet 后缀）。开发模式 source 带 triplet。
    let exe_suffix = if cfg!(target_os = "windows") { ".exe" } else { "" };
    let packed_name = format!("gaia-lite-backend{exe_suffix}");

    // 打包模式：current_exe 拿主 bin 路径，sidecar 在同目录（无 triplet）。
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let packed = dir.join(&packed_name);
            if packed.is_file() {
                return Ok(packed);
            }
        }
    }

    // 开发模式 fallback：tauri/sidecar/gaia-lite-backend-<triplet>（带 triplet，
    // externalBin 要求 source 文件名含 triplet）。
    let dev = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("sidecar")
        .join(sidecar_binary_name());
    if dev.is_file() {
        return Ok(dev);
    }

    // resource_dir fallback：兼容旧 resources 目录打包方式（已废弃，externalBin
    // 为主）。旧 onedir 配置 sidecar 在 resource_dir/gaia-lite-backend/。
    let resource = app
        .path()
        .resource_dir()
        .map_err(|e| format!("resolve resource_dir failed: {e}"))?;
    let legacy = resource.join("gaia-lite-backend").join(&packed_name);
    if legacy.is_file() {
        return Ok(legacy);
    }

    Err(format!(
        "sidecar binary not found. Tried:\n  {}\n  {}\n\
         开发模式：把 onefile 产物重命名为 {} 放 tauri/sidecar/。\n\
         打包模式：确认 tauri.conf.json bundle.externalBin 已打进（sidecar 应落 MacOS/ 同主 bin）。",
        dev.display(),
        legacy.display(),
        sidecar_binary_name(),
    ))
}

/// 前端 dist 目录。
/// - 打包模式：resource_dir/dist
/// - 开发模式：CARGO_MANIFEST_DIR/../dist（tauri/dist）
fn frontend_dist_path(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let resource = app
        .path()
        .resource_dir()
        .map_err(|e| format!("resource_dir failed: {e}"))?
        .join("dist");
    if resource.is_dir() {
        return Ok(resource);
    }
    let dev = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("dist");
    Ok(dev)
}

/// 用户数据目录（lite DB + warehouse）：~/.gaia-lite/
fn user_data_dir() -> PathBuf {
    let p = match std::env::var_os("HOME") {
        Some(home) => PathBuf::from(home).join(".gaia-lite"),
        None => std::env::temp_dir(),
    };
    let _ = std::fs::create_dir_all(&p);
    p
}

/// 启动 sidecar + axum 反代，返回 (proxy_url, sidecar_child)。
async fn start_backend_stack(
    app: &tauri::AppHandle,
) -> Result<(String, Child), String> {
    // sidecar 端口：pick 后立即 spawn（短暂 TOCTOU 窗口可接受——sidecar bind
    // 失败会报错，wait_health 超时即重试）。axum 端口直接 bind 127.0.0.1:0 拿
    // listener（不释放，无 TOCTOU），避免 pick 后被系统进程抢占（曾遇 rapportd
    // 抢同端口致 axum bind 失败）。
    let backend_port = pick_free_port()?;
    let sidecar_bin = sidecar_binary_path(app)?;
    let data_dir = user_data_dir();

    let mut cmd = tokio::process::Command::new(&sidecar_bin);
    cmd.env("PORT", backend_port.to_string());
    cmd.env("LITE_DB_PATH", data_dir.join("gaia-lite.db").to_string_lossy().to_string());
    cmd.env(
        "LITE_WAREHOUSE_PATH",
        data_dir.join("warehouse.duckdb").to_string_lossy().to_string(),
    );
    cmd.env("AUTHZ_DEV_MODE", "true");
    cmd.env("APP_LOG_LEVEL", "info");
    // kill_on_drop：Rust 壳正常 Drop 时 kill child（兜底，但外部 SIGTERM 不走 Drop）。
    cmd.kill_on_drop(true);
    // stdin piped：Rust 壳退出（任何路径，含 SIGTERM/SIGKILL）时 stdin 写端 fd 关闭，
    // sidecar 读到 EOF 自杀（入口脚本 gaia_lite_backend.py 监听 stdin EOF）。
    // 这是外部信号场景下最可靠的孤儿 sidecar 防护（Tauri 官方 sidecar 推荐）。
    cmd.stdin(std::process::Stdio::piped());
    // sidecar stdout/stderr 留给系统继承（开发时看日志；打包后写文件可选，C3 处理）
    cmd.stdout(std::process::Stdio::null());
    cmd.stderr(std::process::Stdio::inherit());

    let child = cmd.spawn().map_err(|e| {
        format!("spawn sidecar {} failed: {e}", sidecar_bin.display())
    })?;

    // 等 sidecar /health 通（最多 20s，PyInstaller onedir 冷启 + SQLite 建表）
    let backend_url = format!("http://127.0.0.1:{backend_port}");
    wait_health(&backend_url, std::time::Duration::from_secs(20)).await?;

    // 起 axum 反代：bind 127.0.0.1:0 让 OS 分配端口（listener 持有，无 TOCTOU）。
    let dist_dir = frontend_dist_path(app)?;
    let router = proxy::proxy_router(backend_url.clone(), dist_dir);
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .map_err(|e| format!("bind proxy failed: {e}"))?;
    let actual_port = listener.local_addr().unwrap().port();
    tokio::spawn(async move {
        if let Err(e) = axum::serve(listener, router).await {
            tracing::error!("axum serve failed: {e}");
        }
    });

    Ok((format!("http://127.0.0.1:{actual_port}"), child))
}

/// 等 sidecar /health 返回 200。
async fn wait_health(backend_url: &str, timeout: std::time::Duration) -> Result<(), String> {
    let client = reqwest::Client::builder()
        .build()
        .map_err(|e| e.to_string())?;
    let start = std::time::Instant::now();
    loop {
        if start.elapsed() > timeout {
            return Err(format!(
                "sidecar health check timeout ({timeout:?}) — backend_url={backend_url}"
            ));
        }
        match client.get(format!("{backend_url}/health")).send().await {
            Ok(r) if r.status().is_success() => return Ok(()),
            _ => tokio::time::sleep(std::time::Duration::from_millis(300)).await,
        }
    }
}

/// sidecar 子进程状态（退出时 kill）。
struct SidecarState {
    child: Arc<Mutex<Option<Child>>>,
}

#[tauri::command]
fn placeholder() -> String {
    "gaia-lite".into()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info".into()),
        )
        .init();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![placeholder])
        .setup(|app| {
            let app_handle = app.handle().clone();
            // 在 tokio runtime 里启动 backend stack，拿到 proxy_url 后导航 window。
            tauri::async_runtime::block_on(async move {
                match start_backend_stack(&app_handle).await {
                    Ok((proxy_url, child)) => {
                        app_handle.manage(SidecarState {
                            child: Arc::new(Mutex::new(Some(child))),
                        });
                        if let Some(window) = app_handle.get_webview_window("main") {
                            // 导航到 axum 反代地址（同源，前端零改动）
                            let _ = window.eval(&format!(
                                "window.location.replace({proxy_url:?});"
                            ));
                        }
                    }
                    Err(e) => {
                        tracing::error!("start_backend_stack failed: {e}");
                    }
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            // ExitRequested：用户请求退出（Cmd+Q / 关窗）——拒绝默认行为让 Exit handler 兜底，
            // 或直接 kill sidecar。这里两阶段：ExitRequested 时 kill，Exit 时再次确认。
            if let RunEvent::ExitRequested { .. } | RunEvent::Exit = event {
                if let Some(state) = app_handle.try_state::<SidecarState>() {
                    if let Some(mut child) = state.child.blocking_lock().take() {
                        // start_kill 同步发 SIGKILL（tokio Child）；kill_on_drop 也兜底。
                        let _ = child.start_kill();
                    }
                }
            }
        });
}
