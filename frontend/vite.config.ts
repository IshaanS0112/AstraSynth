import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // three.js changes far less often than the app, so it gets its own
        // long-cached chunk. Both load only when mission control opens.
        manualChunks: { three: ["three"] },
      },
    },
    // three.js is one ~550 kB module that cannot be split further, and it is
    // lazy-loaded; the default 500 kB warning would fire on every build.
    chunkSizeWarningLimit: 700,
  },
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
