import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The build lands inside the Python package, so `jobagent dashboard` serves it with no Node on the machine that runs the agent.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../src/jobagent/dashboard/static", emptyOutDir: true, sourcemap: false },
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },   // `npm run dev` against a running `jobagent dashboard`
  test: { environment: "jsdom", globals: true, setupFiles: ["./src/test/setup.js"] },
});
