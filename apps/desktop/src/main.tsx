import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { openLinksExternally, reportUiError } from "./api";
import App from "./App";
import "./styles.css";

openLinksExternally();
window.addEventListener("error", (e) => reportUiError(e.error));
window.addEventListener("unhandledrejection", (e) => reportUiError(e.reason));

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
