import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App, createApp } from "@/app/App";
import "@/shared/i18n";
import "./index.css";

const root = document.getElementById("root");
if (!root) throw new Error("index.html has no #root");

createRoot(root).render(
  <StrictMode>
    <App app={createApp()} />
  </StrictMode>,
);
