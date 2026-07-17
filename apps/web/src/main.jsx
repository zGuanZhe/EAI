import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App.jsx";
import { AppProviders } from "./app/AppProviders.jsx";
import { DesktopBootstrap } from "./app/DesktopBootstrap.jsx";

createRoot(document.getElementById("root")).render(
  <AppProviders>
    <DesktopBootstrap><App /></DesktopBootstrap>
  </AppProviders>
);
