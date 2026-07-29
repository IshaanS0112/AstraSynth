import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Dev server proxies both the API and the statically served hazard maps
      // so the browser sees a single origin and CORS stays out of the way.
      "/api": { target: "http://localhost:8000", changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") },
      "/static": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
});
