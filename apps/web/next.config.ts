import type { NextConfig } from "next";
// Docker consumes the standalone bundle; Vercel manages its own Next.js output
// tracing and must not receive the standalone override.
const config: NextConfig = process.env.VERCEL ? {} : { output: "standalone" };
export default config;
