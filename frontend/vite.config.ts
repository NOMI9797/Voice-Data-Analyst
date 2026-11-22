import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // `npm run dev` outside Docker: forward API calls to the backend
    proxy: { "/api": "http://localhost:8000" },
  },
});
