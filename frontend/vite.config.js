import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],

  // --- Dev server proxy ----------------------------------------------------
  // During development, Vite runs on port 5173 and our FastAPI backend runs
  // on port 8000. Without a proxy, the browser would block requests from
  // localhost:5173 to localhost:8000 due to CORS (Cross-Origin Resource Sharing).
  //
  // This proxy tells Vite: "any request starting with /api should be forwarded
  // to http://localhost:8000." The `rewrite` strips the /api prefix so that
  // /api/chat becomes /chat on the backend.
  //
  // The frontend code uses "/api/chat" → Vite rewrites to "http://localhost:8000/chat"
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000", // our FastAPI backend
        changeOrigin: true,              // set the Host header to the target
        rewrite: (path) => path.replace(/^\/api/, ""), // /api/chat → /chat
      },
    },
  },
});
