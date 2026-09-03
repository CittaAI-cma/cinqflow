import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  env: {
    CINQFLOW_API: process.env.CINQFLOW_API ?? "http://localhost:8000",
  },
  experimental: {
    serverActions: {
      bodySizeLimit: "50mb",
    },
  },
};

export default nextConfig;
