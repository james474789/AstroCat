// AstroCat desktop shell (D1, docs/design/20261027-D1-desktop-apps.md).
//
// Shows a splash page, starts the bundled Python launcher (which runs Postgres, Redis, the API
// and the Celery workers), then points the window at the local API's desktop sign-in URL.
// The launcher talks back with one JSON line per event on stdout, and takes "restart" / "quit"
// on stdin; closing stdin also stops it.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde_json::Value;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Emitter, Manager, RunEvent, WindowEvent};
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};
use tauri_plugin_opener::OpenerExt;

/// How long the launcher gets to stop Postgres and Redis cleanly before it is killed.
const SHUTDOWN_GRACE: Duration = Duration::from_secs(45);

#[derive(Default)]
struct Backend {
    child: Mutex<Option<Child>>,
    stdin: Mutex<Option<ChildStdin>>,
}

/// Same folder as the launcher's default_data_root(): %LOCALAPPDATA%\AstroCat or
/// ~/Library/Application Support/AstroCat.
fn data_dir(app: &AppHandle) -> PathBuf {
    app.path()
        .local_data_dir()
        .unwrap_or_else(|_| PathBuf::from("."))
        .join("AstroCat")
}

fn runtime_dir(app: &AppHandle) -> Result<PathBuf, String> {
    let res = app.path().resource_dir().map_err(|e| e.to_string())?;
    let dev = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("resources").join("runtime");
    for candidate in [res.join("runtime"), res.join("resources").join("runtime"), dev] {
        if candidate.join("backend").is_dir() {
            return Ok(candidate);
        }
    }
    Err(format!("The bundled runtime is missing from {}", res.display()))
}

fn python(runtime: &Path) -> PathBuf {
    if cfg!(windows) {
        runtime.join("python.exe")
    } else {
        runtime.join("bin").join("python3")
    }
}

fn spawn_backend(app: &AppHandle) -> Result<(), String> {
    let runtime = runtime_dir(app)?;
    let mut cmd = Command::new(python(&runtime));
    cmd.args(["-m", "app.desktop.launcher"])
        .current_dir(runtime.join("backend"))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    let mut child = cmd
        .spawn()
        .map_err(|e| format!("Could not start the AstroCat backend: {e}"))?;
    let stdout = child.stdout.take().ok_or("The backend has no stdout")?;
    let state = app.state::<Backend>();
    *state.stdin.lock().unwrap() = child.stdin.take();
    *state.child.lock().unwrap() = Some(child);

    let handle = app.clone();
    std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if let Ok(event) = serde_json::from_str::<Value>(&line) {
                on_event(&handle, &event);
            }
        }
    });
    Ok(())
}

fn on_event(app: &AppHandle, event: &Value) {
    let field = |k: &str| event.get(k).and_then(Value::as_str).unwrap_or_default().to_string();
    match field("event").as_str() {
        "status" => {
            let _ = app.emit("backend-status", field("message"));
        }
        "ready" => {
            if let (Some(win), Ok(url)) = (
                app.get_webview_window("main"),
                field("login_url").parse::<tauri::Url>(),
            ) {
                let _ = win.navigate(url);
            }
        }
        "error" => {
            let _ = app.emit("backend-error", field("message"));
        }
        _ => {}
    }
}

fn send(app: &AppHandle, command: &str) {
    if let Some(stdin) = app.state::<Backend>().stdin.lock().unwrap().as_mut() {
        let _ = stdin.write_all(format!("{command}\n").as_bytes());
        let _ = stdin.flush();
    }
}

/// Ask the launcher to stop, give it SHUTDOWN_GRACE, then kill it.
fn shutdown(app: &AppHandle) {
    let state = app.state::<Backend>();
    // take() into locals so no MutexGuard temporary outlives `state` (edition 2021 keeps a
    // tail expression's temporaries alive past the function's locals: E0597)
    let stdin = state.stdin.lock().unwrap().take();
    let child = state.child.lock().unwrap().take();
    if let Some(mut stdin) = stdin {
        let _ = stdin.write_all(b"quit\n");
    } // stdin is dropped here: EOF also means quit
    if let Some(mut child) = child {
        let deadline = Instant::now() + SHUTDOWN_GRACE;
        while Instant::now() < deadline {
            match child.try_wait() {
                Ok(Some(_)) => return,
                Ok(None) => std::thread::sleep(Duration::from_millis(200)),
                Err(_) => break,
            }
        }
        let _ = child.kill();
    }
}

fn quit(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.hide();
    }
    let app = app.clone();
    std::thread::spawn(move || {
        shutdown(&app);
        app.exit(0);
    });
}

fn show_main(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.show();
        let _ = win.unminimize();
        let _ = win.set_focus();
    }
}

#[tauri::command]
fn open_logs(app: AppHandle) {
    let logs = data_dir(&app).join("logs");
    let _ = app.opener().open_path(logs.to_string_lossy(), None::<&str>);
}

fn write_image_paths(config: &Path, paths: &[String]) -> Result<(), String> {
    let text = std::fs::read_to_string(config).map_err(|e| e.to_string())?;
    let mut cfg: Value = serde_json::from_str(&text).map_err(|e| e.to_string())?;
    cfg["image_paths"] = Value::from(paths.to_vec());
    let tmp = config.with_extension("json.tmp");
    let body = serde_json::to_string_pretty(&cfg).map_err(|e| e.to_string())?;
    std::fs::write(&tmp, body).map_err(|e| e.to_string())?;
    std::fs::rename(&tmp, config).map_err(|e| e.to_string())
}

/// The picked folders replace the library list; the launcher restarts the app processes with it.
fn choose_library_folders(app: &AppHandle) {
    let config = data_dir(app).join("config.json");
    if !config.exists() {
        app.dialog()
            .message("AstroCat is still starting. Try again in a moment.")
            .kind(MessageDialogKind::Info)
            .show(|_| {});
        return;
    }
    let handle = app.clone();
    app.dialog()
        .file()
        .set_title("Select every folder AstroCat should index")
        .pick_folders(move |folders| {
            let Some(folders) = folders else { return };
            let paths: Vec<String> = folders
                .into_iter()
                .filter_map(|f| f.into_path().ok())
                .map(|p| p.to_string_lossy().into_owned())
                .collect();
            if paths.is_empty() {
                return;
            }
            match write_image_paths(&config, &paths) {
                Ok(()) => send(&handle, "restart"),
                Err(e) => {
                    handle
                        .dialog()
                        .message(format!("Could not save the library folders: {e}"))
                        .kind(MessageDialogKind::Error)
                        .show(|_| {});
                }
            }
        });
}

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Open AstroCat", true, None::<&str>)?;
    let folders = MenuItem::with_id(app, "folders", "Choose library folders…", true, None::<&str>)?;
    let logs = MenuItem::with_id(app, "logs", "Open logs folder", true, None::<&str>)?;
    let separator = PredefinedMenuItem::separator(app)?;
    let quit_item = MenuItem::with_id(app, "quit", "Quit AstroCat", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &folders, &logs, &separator, &quit_item])?;

    let mut tray = TrayIconBuilder::with_id("main").tooltip("AstroCat").menu(&menu);
    if let Some(icon) = app.default_window_icon() {
        tray = tray.icon(icon.clone());
    }
    tray.on_menu_event(|app, event| match event.id.as_ref() {
        "open" => show_main(app),
        "folders" => choose_library_folders(app),
        "logs" => open_logs(app.clone()),
        "quit" => quit(app),
        _ => {}
    })
    .build(app)?;
    Ok(())
}

fn main() {
    tauri::Builder::default()
        // a second launch focuses the running app instead of fighting it for the database
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| show_main(app)))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(Backend::default())
        .invoke_handler(tauri::generate_handler![open_logs])
        .setup(|app| {
            let handle = app.handle().clone();
            build_tray(&handle)?;
            if let Err(e) = spawn_backend(&handle) {
                let _ = handle.emit("backend-error", e);
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                quit(window.app_handle());
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building the AstroCat app")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                shutdown(app);
            }
        });
}
