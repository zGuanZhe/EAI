#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;
use std::{
    fs::{self, File},
    io::Write,
    path::{Path, PathBuf},
    sync::Mutex,
    time::Duration,
};
use tauri::{AppHandle, Manager, State};
use tauri_plugin_shell::{
    process::{CommandChild, CommandEvent},
    ShellExt,
};

#[derive(Clone, Serialize)]
struct RuntimeInfo {
    api_root: String,
    session_token: String,
    mode: String,
    personal_dir: String,
    atlas_cache_dir: String,
    log_dir: String,
}

#[derive(Default)]
struct BackendState {
    runtime: Mutex<Option<RuntimeInfo>>,
    child: Mutex<Option<CommandChild>>,
    error: Mutex<Option<String>>,
    instance_lock: Mutex<Option<File>>,
}

fn copy_directory_if_missing(source: &Path, target: &Path) -> Result<(), String> {
    if !source.exists() || target.exists() {
        return Ok(());
    }
    fs::create_dir_all(target).map_err(|error| error.to_string())?;
    for entry in fs::read_dir(source).map_err(|error| error.to_string())? {
        let entry = entry.map_err(|error| error.to_string())?;
        let destination = target.join(entry.file_name());
        if entry.path().is_dir() {
            copy_directory_if_missing(&entry.path(), &destination)?;
        } else if !destination.exists() {
            fs::copy(entry.path(), destination).map_err(|error| error.to_string())?;
        }
    }
    Ok(())
}

fn select_app_data_dir(
    default: PathBuf,
    test_mode: bool,
    override_dir: Option<String>,
) -> Result<PathBuf, String> {
    if !test_mode {
        return Ok(default);
    }
    let Some(raw) = override_dir.filter(|value| !value.trim().is_empty()) else {
        return Ok(default);
    };
    let path = PathBuf::from(raw);
    if !path.is_absolute() {
        return Err("EAI_DESKTOP_APP_DATA_DIR 必须是绝对路径".into());
    }
    Ok(path)
}

fn resolve_app_data_dir(app: &AppHandle) -> Result<PathBuf, String> {
    let default = app
        .path()
        .app_data_dir()
        .map_err(|error| error.to_string())?;
    select_app_data_dir(
        default,
        std::env::var("EAI_DESKTOP_TEST_MODE").ok().as_deref() == Some("1"),
        std::env::var("EAI_DESKTOP_APP_DATA_DIR").ok(),
    )
}

fn stop_backend(state: &BackendState) {
    if let Some(child) = state.child.lock().expect("backend child lock").take() {
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            let _ = std::process::Command::new("taskkill")
                .args(["/PID", &child.pid().to_string(), "/T", "/F"])
                .creation_flags(0x08000000)
                .status();
        }
        let _ = child.kill();
    }
    *state.runtime.lock().expect("runtime lock") = None;
}

fn credential(provider: &str) -> Option<String> {
    keyring::Entry::new("EAI Desktop", provider)
        .ok()
        .and_then(|entry| entry.get_password().ok())
        .filter(|value| !value.trim().is_empty())
}

async fn launch_backend(app: AppHandle) -> Result<(), String> {
    let state = app.state::<BackendState>();
    stop_backend(&state);
    *state.error.lock().map_err(|error| error.to_string())? = None;

    let app_data = resolve_app_data_dir(&app)?;
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|error| error.to_string())?;
    let personal_dir = app_data.join("data").join("personal");
    let log_dir = app_data.join("logs");
    let atlas_cache_dir = resource_dir.join("atlas-cache");
    let campaign_source_dir = resource_dir.join("third_party").join("ai-scientist-v2");
    copy_directory_if_missing(&resource_dir.join("personal-snapshot"), &personal_dir)?;
    fs::create_dir_all(&personal_dir).map_err(|error| error.to_string())?;
    fs::create_dir_all(&log_dir).map_err(|error| error.to_string())?;

    let token = uuid::Uuid::new_v4().simple().to_string();
    let ready_file = log_dir.join(format!("backend-ready-{token}.json"));
    let desktop_ready_file = log_dir.join("desktop-ready.json");
    let _ = fs::remove_file(&ready_file);
    let _ = fs::remove_file(&desktop_ready_file);
    let mut command = app
        .shell()
        .sidecar("eai-service")
        .map_err(|error| error.to_string())?
        .args([
            "--port",
            "0",
            "--session-token",
            &token,
            "--atlas-cache-dir",
            atlas_cache_dir.to_string_lossy().as_ref(),
            "--personal-dir",
            personal_dir.to_string_lossy().as_ref(),
            "--log-dir",
            log_dir.to_string_lossy().as_ref(),
            "--campaign-source-dir",
            campaign_source_dir.to_string_lossy().as_ref(),
            "--ready-file",
            ready_file.to_string_lossy().as_ref(),
            "--root",
            app_data.to_string_lossy().as_ref(),
        ]);
    if let Some(value) = credential("openrouter") {
        command = command.env("OPENROUTER_API_KEY", value);
    }
    if let Some(value) = credential("openai") {
        command = command.env("OPENAI_API_KEY", value);
    }

    let (mut receiver, child) = command.spawn().map_err(|error| error.to_string())?;
    *state.child.lock().map_err(|error| error.to_string())? = Some(child);
    let readiness_app = app.clone();
    let readiness_token = token.clone();
    let readiness_personal = personal_dir.clone();
    let readiness_atlas = atlas_cache_dir.clone();
    let readiness_logs = log_dir.clone();
    let readiness_marker = desktop_ready_file.clone();
    tauri::async_runtime::spawn(async move {
        for _ in 0..150 {
            if let Ok(text) = fs::read_to_string(&ready_file) {
                if let Ok(value) = serde_json::from_str::<serde_json::Value>(&text) {
                    if let Some(port) = value.get("port").and_then(|item| item.as_u64()) {
                        let runtime = RuntimeInfo {
                            api_root: format!("http://127.0.0.1:{port}/api/vnext"),
                            session_token: readiness_token,
                            mode: "desktop".into(),
                            personal_dir: readiness_personal.to_string_lossy().into_owned(),
                            atlas_cache_dir: readiness_atlas.to_string_lossy().into_owned(),
                            log_dir: readiness_logs.to_string_lossy().into_owned(),
                        };
                        *readiness_app
                            .state::<BackendState>()
                            .runtime
                            .lock()
                            .expect("runtime lock") = Some(runtime);
                        let _ = fs::write(&readiness_marker, r#"{"event":"ready"}"#);
                        let _ = fs::remove_file(&ready_file);
                        return;
                    }
                }
            }
            tokio_sleep().await;
        }
    });
    let app_handle = app.clone();
    let token_for_runtime = token.clone();
    tauri::async_runtime::spawn(async move {
        let log_path = log_dir.join("service.log");
        while let Some(event) = receiver.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => {
                    let line = String::from_utf8_lossy(&bytes).trim().to_string();
                    if let Ok(value) = serde_json::from_str::<serde_json::Value>(&line) {
                        if value.get("event").and_then(|item| item.as_str()) == Some("ready") {
                            if let Some(port) = value.get("port").and_then(|item| item.as_u64()) {
                                let runtime = RuntimeInfo {
                                    api_root: format!("http://127.0.0.1:{port}/api/vnext"),
                                    session_token: token_for_runtime.clone(),
                                    mode: "desktop".into(),
                                    personal_dir: personal_dir.to_string_lossy().into_owned(),
                                    atlas_cache_dir: atlas_cache_dir.to_string_lossy().into_owned(),
                                    log_dir: log_dir.to_string_lossy().into_owned(),
                                };
                                *app_handle
                                    .state::<BackendState>()
                                    .runtime
                                    .lock()
                                    .expect("runtime lock") = Some(runtime);
                                let _ = fs::write(&desktop_ready_file, r#"{"event":"ready"}"#);
                                continue;
                            }
                        }
                    }
                    append_log(&log_path, &line);
                }
                CommandEvent::Stderr(bytes) => {
                    append_log(&log_path, &String::from_utf8_lossy(&bytes))
                }
                CommandEvent::Error(message) => {
                    *app_handle
                        .state::<BackendState>()
                        .error
                        .lock()
                        .expect("error lock") = Some(message);
                }
                CommandEvent::Terminated(payload) => {
                    let message = format!("本地服务已退出：{:?}", payload.code);
                    *app_handle
                        .state::<BackendState>()
                        .error
                        .lock()
                        .expect("error lock") = Some(message);
                    *app_handle
                        .state::<BackendState>()
                        .runtime
                        .lock()
                        .expect("runtime lock") = None;
                    break;
                }
                _ => {}
            }
        }
    });
    Ok(())
}

fn append_log(path: &Path, line: &str) {
    if let Ok(mut file) = fs::OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(file, "{line}");
    }
}

#[cfg(windows)]
fn acquire_instance_lock(app: &AppHandle) -> Result<(), String> {
    use std::os::windows::fs::OpenOptionsExt;
    let app_data = resolve_app_data_dir(app)?;
    fs::create_dir_all(&app_data).map_err(|error| error.to_string())?;
    let file = fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .share_mode(0)
        .open(app_data.join("eai-desktop.lock"))
        .map_err(|_| "EAI Desktop 已在运行".to_string())?;
    *app.state::<BackendState>()
        .instance_lock
        .lock()
        .map_err(|error| error.to_string())? = Some(file);
    Ok(())
}

#[tauri::command]
async fn get_runtime_info(state: State<'_, BackendState>) -> Result<RuntimeInfo, String> {
    for _ in 0..150 {
        if let Some(runtime) = state
            .runtime
            .lock()
            .map_err(|error| error.to_string())?
            .clone()
        {
            return Ok(runtime);
        }
        if let Some(error) = state
            .error
            .lock()
            .map_err(|error| error.to_string())?
            .clone()
        {
            return Err(error);
        }
        tokio_sleep().await;
    }
    Err("本地研究服务启动超时".into())
}

async fn tokio_sleep() {
    tauri::async_runtime::spawn_blocking(|| std::thread::sleep(Duration::from_millis(100)))
        .await
        .ok();
}

#[tauri::command]
async fn restart_backend(app: AppHandle) -> Result<RuntimeInfo, String> {
    launch_backend(app.clone()).await?;
    get_runtime_info(app.state::<BackendState>()).await
}

#[tauri::command]
fn open_logs(app: AppHandle) -> Result<(), String> {
    let path = resolve_app_data_dir(&app)?.join("logs");
    std::process::Command::new("explorer")
        .arg(path)
        .spawn()
        .map_err(|error| error.to_string())?;
    Ok(())
}

#[tauri::command]
async fn set_provider_secret(
    app: AppHandle,
    provider: String,
    secret: String,
) -> Result<(), String> {
    if !matches!(provider.as_str(), "openrouter" | "openai") || secret.trim().is_empty() {
        return Err("无效的 Provider 或密钥".into());
    }
    keyring::Entry::new("EAI Desktop", &provider)
        .map_err(|error| error.to_string())?
        .set_password(secret.trim())
        .map_err(|error| error.to_string())?;
    launch_backend(app).await
}

#[tauri::command]
async fn clear_provider_secret(app: AppHandle, provider: String) -> Result<(), String> {
    if !matches!(provider.as_str(), "openrouter" | "openai") {
        return Err("无效的 Provider".into());
    }
    if let Ok(entry) = keyring::Entry::new("EAI Desktop", &provider) {
        let _ = entry.delete_credential();
    }
    launch_backend(app).await
}

fn main() {
    let app = tauri::Builder::default()
        .manage(BackendState::default())
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![
            get_runtime_info,
            restart_backend,
            open_logs,
            set_provider_secret,
            clear_provider_secret
        ])
        .setup(|app| {
            acquire_instance_lock(app.handle())?;
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                if let Err(error) = launch_backend(handle.clone()).await {
                    *handle
                        .state::<BackendState>()
                        .error
                        .lock()
                        .expect("error lock") = Some(error);
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build EAI Desktop");

    app.run(|handle, event| {
        if matches!(
            event,
            tauri::RunEvent::Exit | tauri::RunEvent::ExitRequested { .. }
        ) {
            stop_backend(&handle.state::<BackendState>());
        }
    });
}

#[cfg(test)]
mod tests {
    use super::{copy_directory_if_missing, select_app_data_dir};
    use std::fs;

    fn test_root(name: &str) -> std::path::PathBuf {
        std::env::temp_dir().join(format!("eai-desktop-{name}-{}", uuid::Uuid::new_v4()))
    }

    #[test]
    fn app_data_override_is_test_only_and_requires_an_absolute_path() {
        let default = test_root("default-data");
        let isolated = test_root("isolated-data");
        assert_eq!(
            select_app_data_dir(default.clone(), false, Some(isolated.display().to_string()),)
                .expect("production default"),
            default
        );
        assert_eq!(
            select_app_data_dir(default, true, Some(isolated.display().to_string()))
                .expect("test override"),
            isolated
        );
        assert!(select_app_data_dir(test_root("default"), true, Some("relative".into())).is_err());
    }

    #[test]
    fn copies_personal_seed_when_target_is_absent() {
        let root = test_root("seed-copy");
        let source = root.join("source");
        let target = root.join("target");
        fs::create_dir_all(source.join("threads")).expect("create source");
        fs::write(source.join("threads").join("thread.json"), "seed").expect("write seed");

        copy_directory_if_missing(&source, &target).expect("copy seed");

        assert_eq!(
            fs::read_to_string(target.join("threads").join("thread.json")).expect("read target"),
            "seed"
        );
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn never_overwrites_existing_personal_directory() {
        let root = test_root("seed-preserve");
        let source = root.join("source");
        let target = root.join("target");
        fs::create_dir_all(&source).expect("create source");
        fs::create_dir_all(&target).expect("create target");
        fs::write(source.join("thread.json"), "seed").expect("write seed");
        fs::write(target.join("thread.json"), "personal").expect("write personal");

        copy_directory_if_missing(&source, &target).expect("preserve target");

        assert_eq!(
            fs::read_to_string(target.join("thread.json")).expect("read target"),
            "personal"
        );
        let _ = fs::remove_dir_all(root);
    }
}
