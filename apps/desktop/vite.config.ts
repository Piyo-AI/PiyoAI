import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Port 1420 is what Tauri expects, and what the core's CORS allow-list contains.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: { port: 1420, strictPort: true, host: "127.0.0.1" },
});
