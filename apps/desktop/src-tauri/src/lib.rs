mod core;

use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let state = core::CoreState::new();
    tauri::Builder::default()
        .manage(state.clone())
        .setup(|app| {
            let state = app.state::<std::sync::Arc<core::CoreState>>().inner().clone();
            core::launch(app.handle().clone(), state);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![core::core_status, core::restart_core])
        .build(tauri::generate_context!())
        .expect("error while building Piyo AI")
        .run(move |_app, event| {
            if let tauri::RunEvent::Exit = event {
                state.stop();
            }
        });
}
