mod background;
mod core;
mod crash;
mod rollback;

use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let state = core::CoreState::new();
    let builder = tauri::Builder::default();
    #[cfg(desktop)]
    let builder = builder
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| background::show_main(app)))
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec![background::BACKGROUND_ARG]),
        ))
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init());
    builder
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_notification::init())
        .manage(state.clone())
        .manage(rollback::RollbackProgress::default())
        .on_window_event(background::on_window_event)
        .setup(|app| {
            let background = background::Background::load(app.handle());
            background::init(app.handle(), &background);
            app.manage(background);
            if let Some(note) = crash::note_path(app.handle()) {
                crash::install(note);
            }
            let state = app.state::<std::sync::Arc<core::CoreState>>().inner().clone();
            core::launch(app.handle().clone(), state);
            rollback::check_on_start(app.handle().clone());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![core::core_status,
            core::restart_core,
            background::background_status,
            background::set_background,
            rollback::begin_update,
            rollback::confirm_update_healthy,
            rollback::rollback_status,
            rollback::bad_versions
        ])
        .build(tauri::generate_context!())
        .expect("error while building Piyo AI")
        .run(move |_app, event| {
            if let tauri::RunEvent::Exit = event {
                state.stop();
            }
        });
}
