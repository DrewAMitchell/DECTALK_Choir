import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { invoke } from "@tauri-apps/api/core";
import App from "./App";
import "./styles.css";

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);

requestAnimationFrame(() => requestAnimationFrame(() => {
  window.setTimeout(() => void invoke("finish_startup").catch(() => undefined), 350);
}));
