import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import "./index.css";
import App from "./App";
import AccessGate from "./components/AccessGate";
import { queryClient } from "./hooks";
import { ToastProvider } from "./ui";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <AccessGate>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </AccessGate>
      </ToastProvider>
    </QueryClientProvider>
  </StrictMode>,
);
