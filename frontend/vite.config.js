import { execSync } from "node:child_process";

import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

function resolveDevProxyTarget(env) {
  if (env.VITE_DEV_PROXY_TARGET) {
    return env.VITE_DEV_PROXY_TARGET;
  }

  try {
    const publishedPort = execSync("docker compose port backend 8000", {
      stdio: ["ignore", "pipe", "ignore"],
    })
      .toString()
      .trim()
      .match(/:(\d+)$/)?.[1];

    if (publishedPort) {
      return `http://127.0.0.1:${publishedPort}`;
    }
  } catch {
    return undefined;
  }

  return undefined;
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const proxyTarget = resolveDevProxyTarget(env);

  return {
    plugins: [react()],

    // Frontend code uses the /api prefix by default.
    // In local dev, proxy that prefix to either:
    // 1. VITE_DEV_PROXY_TARGET, or
    // 2. the backend container published by `docker compose`.
    server: proxyTarget
      ? {
          proxy: {
            "/api": {
              target: proxyTarget,
              changeOrigin: true,
              rewrite: (path) => path.replace(/^\/api/, ""),
            },
          },
        }
      : undefined,
  };
});
