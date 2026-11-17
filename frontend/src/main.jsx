import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import { TipProvider } from "./tooltip.jsx";
import "./styles.css";

createRoot(document.getElementById("root")).render(
  <TipProvider>
    <App />
  </TipProvider>,
);
