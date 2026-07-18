#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::{Deserialize, Serialize};
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
use url::{Host, Url};

#[derive(Clone, Serialize)]
struct RuntimeInfo {
    api_root: String,
    session_token: String,
    mode: String,
    personal_dir: String,
    atlas_cache_dir: String,
    log_dir: String,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(default)]
struct ProviderConfig {
    provider: String,
    base_url: String,
    model: String,
    api_format: String,
    web_search_provider: String,
    web_search_base_url: String,
}

impl Default for ProviderConfig {
    fn default() -> Self {
        Self {
            provider: "openai".into(),
            base_url: "https://api.openai.com/v1".into(),
            model: "gpt-4.1-mini".into(),
            api_format: "responses".into(),
            web_search_provider: String::new(),
            web_search_base_url: String::new(),
        }
    }
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

fn provider_key(provider: &str) -> Option<String> {
    credential(provider).or_else(|| {
        let variable = if provider == "openrouter" {
            "OPENROUTER_API_KEY"
        } else {
            "OPENAI_API_KEY"
        };
        std::env::var(variable)
            .ok()
            .filter(|value| !value.trim().is_empty())
    })
}

fn web_search_key(provider: &str) -> Option<String> {
    if provider.is_empty() || provider == "searxng" {
        return None;
    }
    credential(&format!("web-search:{provider}"))
}

fn default_provider_config() -> ProviderConfig {
    let explicit_openai = std::env::var("OPENAI_BASE_URL").is_ok()
        || (provider_key("openai").is_some() && provider_key("openrouter").is_none());
    if explicit_openai {
        ProviderConfig {
            provider: "openai".into(),
            base_url: std::env::var("OPENAI_BASE_URL")
                .unwrap_or_else(|_| "https://api.openai.com/v1".into()),
            model: std::env::var("OPENAI_MODEL").unwrap_or_else(|_| "gpt-4.1-mini".into()),
            api_format: std::env::var("OPENAI_API_FORMAT").unwrap_or_else(|_| "responses".into()),
            web_search_provider: std::env::var("EAI_WEB_SEARCH_PROVIDER").unwrap_or_default(),
            web_search_base_url: std::env::var("EAI_WEB_SEARCH_BASE_URL").unwrap_or_default(),
        }
    } else {
        ProviderConfig {
            provider: "openrouter".into(),
            base_url: std::env::var("OPENROUTER_BASE_URL")
                .unwrap_or_else(|_| "https://openrouter.ai/api/v1".into()),
            model: std::env::var("OPENROUTER_MODEL").unwrap_or_else(|_| "openrouter/auto".into()),
            api_format: "chat".into(),
            web_search_provider: std::env::var("EAI_WEB_SEARCH_PROVIDER").unwrap_or_default(),
            web_search_base_url: std::env::var("EAI_WEB_SEARCH_BASE_URL").unwrap_or_default(),
        }
    }
}

fn validate_provider_config(mut config: ProviderConfig) -> Result<ProviderConfig, String> {
    config.provider = config.provider.trim().to_lowercase();
    config.base_url = config.base_url.trim().trim_end_matches('/').to_string();
    config.model = config.model.trim().to_string();
    config.api_format = config.api_format.trim().to_lowercase();
    config.web_search_provider = config.web_search_provider.trim().to_lowercase();
    config.web_search_base_url = config
        .web_search_base_url
        .trim()
        .trim_end_matches('/')
        .to_string();
    if !matches!(config.provider.as_str(), "openrouter" | "openai") {
        return Err("无效的 Provider".into());
    }
    if config.model.is_empty() || config.model.len() > 160 {
        return Err("模型名称不能为空且不能超过 160 个字符".into());
    }
    if !matches!(config.api_format.as_str(), "chat" | "responses") {
        return Err("API 格式必须是 chat 或 responses".into());
    }
    if !matches!(
        config.web_search_provider.as_str(),
        "" | "searxng" | "brave" | "tavily"
    ) {
        return Err("网页搜索 Provider 必须是 SearXNG、Brave 或 Tavily".into());
    }
    if config.web_search_provider == "brave" && config.web_search_base_url.is_empty() {
        config.web_search_base_url = "https://api.search.brave.com/res/v1/web/search".into();
    }
    if config.web_search_provider == "tavily" && config.web_search_base_url.is_empty() {
        config.web_search_base_url = "https://api.tavily.com/search".into();
    }
    if config.web_search_provider == "searxng" {
        let search_url = Url::parse(&config.web_search_base_url)
            .map_err(|_| "SearXNG 地址不是有效 URL".to_string())?;
        if !search_url.username().is_empty()
            || search_url.password().is_some()
            || search_url.query().is_some()
        {
            return Err("SearXNG 地址不能包含凭据或查询参数".into());
        }
        let search_loopback = match search_url.host() {
            Some(Host::Domain(host)) => host.eq_ignore_ascii_case("localhost"),
            Some(Host::Ipv4(address)) => address.is_loopback(),
            Some(Host::Ipv6(address)) => address.is_loopback(),
            None => false,
        };
        if search_url.scheme() != "https" && !(search_url.scheme() == "http" && search_loopback) {
            return Err("远程 SearXNG 必须使用 HTTPS；HTTP 仅允许 loopback".into());
        }
    }
    let parsed = Url::parse(&config.base_url).map_err(|_| "请求地址不是有效 URL".to_string())?;
    if !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.query().is_some()
        || parsed.fragment().is_some()
    {
        return Err("请求地址不能包含凭据、查询参数或片段".into());
    }
    let loopback = match parsed.host() {
        Some(Host::Domain(host)) => host.eq_ignore_ascii_case("localhost"),
        Some(Host::Ipv4(address)) => address.is_loopback(),
        Some(Host::Ipv6(address)) => address.is_loopback(),
        None => false,
    };
    if parsed.scheme() != "https" && !(parsed.scheme() == "http" && loopback) {
        return Err("远程请求地址必须使用 HTTPS；HTTP 仅允许 localhost 或 loopback".into());
    }
    Ok(config)
}

fn provider_config_path(app_data: &Path) -> PathBuf {
    app_data.join("provider-config.json")
}

fn read_provider_config(app_data: &Path) -> Result<Option<ProviderConfig>, String> {
    let path = provider_config_path(app_data);
    if !path.exists() {
        return Ok(None);
    }
    let raw = fs::read_to_string(path).map_err(|error| error.to_string())?;
    let config = serde_json::from_str(&raw).map_err(|_| "模型通道配置损坏".to_string())?;
    validate_provider_config(config).map(Some)
}

fn write_provider_config(app_data: &Path, config: &ProviderConfig) -> Result<(), String> {
    fs::create_dir_all(app_data).map_err(|error| error.to_string())?;
    let path = provider_config_path(app_data);
    let temporary = app_data.join("provider-config.json.tmp");
    let payload = serde_json::to_vec_pretty(config).map_err(|error| error.to_string())?;
    fs::write(&temporary, payload).map_err(|error| error.to_string())?;
    if path.exists() {
        fs::remove_file(&path).map_err(|error| error.to_string())?;
    }
    fs::rename(temporary, path).map_err(|error| error.to_string())
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
    let provider = read_provider_config(&app_data)?.unwrap_or_else(default_provider_config);

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
    command = command
        .env("OPENROUTER_API_KEY", "")
        .env("OPENAI_API_KEY", "")
        .env("EAI_WEB_SEARCH_PROVIDER", "")
        .env("EAI_WEB_SEARCH_BASE_URL", "")
        .env("EAI_WEB_SEARCH_API_KEY", "");
    if provider.provider == "openrouter" {
        command = command
            .env("OPENROUTER_BASE_URL", &provider.base_url)
            .env("OPENROUTER_MODEL", &provider.model);
        if let Some(value) = provider_key("openrouter") {
            command = command.env("OPENROUTER_API_KEY", value);
        }
    } else {
        command = command
            .env("OPENAI_BASE_URL", &provider.base_url)
            .env("OPENAI_MODEL", &provider.model)
            .env("OPENAI_API_FORMAT", &provider.api_format);
        if let Some(value) = provider_key("openai") {
            command = command.env("OPENAI_API_KEY", value);
        }
    }
    if !provider.web_search_provider.is_empty() {
        command = command
            .env("EAI_WEB_SEARCH_PROVIDER", &provider.web_search_provider)
            .env("EAI_WEB_SEARCH_BASE_URL", &provider.web_search_base_url);
        if let Some(value) = web_search_key(&provider.web_search_provider) {
            command = command.env("EAI_WEB_SEARCH_API_KEY", value);
        }
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
fn get_provider_config(app: AppHandle) -> Result<ProviderConfig, String> {
    let app_data = resolve_app_data_dir(&app)?;
    Ok(read_provider_config(&app_data)?.unwrap_or_else(default_provider_config))
}

#[tauri::command]
#[allow(clippy::too_many_arguments)]
async fn save_provider_config(
    app: AppHandle,
    provider: String,
    base_url: String,
    model: String,
    api_format: String,
    secret: Option<String>,
    web_search_provider: String,
    web_search_base_url: String,
    web_search_secret: Option<String>,
) -> Result<RuntimeInfo, String> {
    let config = validate_provider_config(ProviderConfig {
        provider,
        base_url,
        model,
        api_format,
        web_search_provider,
        web_search_base_url,
    })?;
    if let Some(value) = secret.filter(|value| !value.trim().is_empty()) {
        keyring::Entry::new("EAI Desktop", &config.provider)
            .map_err(|error| error.to_string())?
            .set_password(value.trim())
            .map_err(|error| error.to_string())?;
    }
    if provider_key(&config.provider).is_none() {
        return Err("请输入 API Key".into());
    }
    if let Some(value) = web_search_secret.filter(|value| !value.trim().is_empty()) {
        keyring::Entry::new(
            "EAI Desktop",
            &format!("web-search:{}", config.web_search_provider),
        )
        .map_err(|error| error.to_string())?
        .set_password(value.trim())
        .map_err(|error| error.to_string())?;
    }
    if matches!(config.web_search_provider.as_str(), "brave" | "tavily")
        && web_search_key(&config.web_search_provider).is_none()
    {
        return Err("请输入网页搜索 API Key".into());
    }
    let app_data = resolve_app_data_dir(&app)?;
    write_provider_config(&app_data, &config)?;
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
            get_provider_config,
            save_provider_config,
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
    use super::{
        copy_directory_if_missing, select_app_data_dir, validate_provider_config, ProviderConfig,
    };
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

    #[test]
    fn provider_urls_require_https_except_for_loopback() {
        let config = |base_url: &str| ProviderConfig {
            provider: "openai".into(),
            base_url: base_url.into(),
            model: "test-model".into(),
            api_format: "chat".into(),
            web_search_provider: String::new(),
            web_search_base_url: String::new(),
        };
        assert!(validate_provider_config(config("http://localhost:12345/v1")).is_ok());
        assert!(validate_provider_config(config("http://127.0.0.1:12345/v1")).is_ok());
        assert!(validate_provider_config(config("https://relay.example/v1")).is_ok());
        assert!(validate_provider_config(config("http://relay.example/v1")).is_err());
        assert!(validate_provider_config(config("https://user:secret@relay.example/v1")).is_err());
    }

    #[test]
    fn legacy_provider_config_defaults_web_search_fields() {
        let config: ProviderConfig = serde_json::from_str(
            r#"{"provider":"openai","base_url":"http://127.0.0.1:12345/v1","model":"local","api_format":"chat"}"#,
        )
        .expect("legacy provider config");
        assert!(config.web_search_provider.is_empty());
        assert!(config.web_search_base_url.is_empty());
        assert!(validate_provider_config(config).is_ok());
    }
}
