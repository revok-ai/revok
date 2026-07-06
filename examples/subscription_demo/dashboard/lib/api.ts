// All API requests are routed through Next.js rewrites/route handlers
// (/api/* → backend), so client code should use "/api" as the base URL
// rather than the bare backend address (which is a server-side env var).
export const API_URL = "/api";
