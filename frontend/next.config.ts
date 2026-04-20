import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  allowedDevOrigins: [
    'localhost:3000',
    '127.0.0.1:3000',
    '10.[0-9]+\.[0-9]+\.[0-9]+:3000',
    '192.168.[0-9]+\.[0-9]+:3000',
    '172\.(1[6-9]|2[0-9]|3[0-1])\.[0-9]+\.[0-9]+:3000'
  ]
};

export default nextConfig;
