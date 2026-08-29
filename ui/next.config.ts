import type { NextConfig } from "next";

// The UI holds no secrets and no business rules. It renders what the BFF says,
// and the BFF is the only thing that decides what a caller may see — which is
// why a crafted edit URL is denied at the server, not hidden in this menu.
const config: NextConfig = {
  reactStrictMode: true,
  env: {
    CINQFLOW_API: process.env.CINQFLOW_API ?? "http://127.0.0.1:8000",
  },
};

export default config;
