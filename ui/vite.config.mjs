import {defineConfig} from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  build: {outDir: "dist"},
  optimizeDeps: {include: ["react", "react-dom/client"]},
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "^/(api|media)": {
        target: "http://127.0.0.1:8766",
        changeOrigin: true,
        configure(proxy) {
          proxy.on("proxyReq", (outgoing, incoming) => {
            // Preserve the API origin gate for untrusted browser requests.
            if (["http://127.0.0.1:5173", "http://localhost:5173"].includes(incoming.headers.origin)) {
              outgoing.setHeader("Origin", "http://127.0.0.1:8766");
            }
          });
        },
      },
    },
  },
  plugins: [react()],
});
