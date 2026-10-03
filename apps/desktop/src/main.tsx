import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { openLinksExternally } from "./api";
import App from "./App";
import "./styles.css";

openLinksExternally();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
