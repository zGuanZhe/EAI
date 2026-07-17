import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiRoot = env.VITE_EAI_API_ROOT || "/api/vnext";
  const apiTarget = env.VITE_EAI_API_TARGET || "http://127.0.0.1:8001";
  return {
    plugins: [react()],
    build: {
      rollupOptions: {
        output: {
          manualChunks(id) {
            if (!id.includes("node_modules")) return undefined;
            if (id.includes("react-markdown") || id.includes("remark-") || id.includes("micromark") || id.includes("mdast") || id.includes("hast")) return "markdown";
            if (id.includes("lucide-react")) return "icons";
            if (id.includes("@tanstack")) return "query";
            if (id.includes("react") || id.includes("scheduler")) return "react";
            return "vendor";
          }
        }
      }
    },
    server: {
      proxy: {
        [apiRoot]: apiTarget
      }
    }
  };
});
