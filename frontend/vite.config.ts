import { fileURLToPath } from "node:url";
import { defineConfig, loadEnv, type ProxyOptions } from "vite";
import react from "@vitejs/plugin-react";

const rootDir = fileURLToPath(new URL("..", import.meta.url));

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, rootDir, "");
  const apiKey = env.API_KEY ?? "";
  const apiProxy: ProxyOptions = {
    target: "http://127.0.0.1:8000",
    changeOrigin: true,
  };
  if (apiKey) {
    apiProxy.configure = (proxy) => {
      proxy.on("proxyReq", (proxyReq) => {
        proxyReq.setHeader("Authorization", `Bearer ${apiKey}`);
      });
    };
  }
  return {
    plugins: [react()],
    test: {
      environment: "jsdom",
      setupFiles: "./src/test/setup.ts",
    },
    server: {
      port: 5173,
      proxy: {
        "/api": apiProxy,
        "/health": {
          target: "http://127.0.0.1:8000",
          changeOrigin: true,
        },
      },
    },
  };
});
