/** @type {import('next').NextConfig} */
const API_URL = process.env.API_URL || "http://localhost:8081";

const nextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${API_URL}/:path*`,
      },
    ];
  },
};

export default nextConfig;
