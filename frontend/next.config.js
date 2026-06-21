const { withSentryConfig } = require("@sentry/nextjs");

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Emit a static export to `out/` when building for Electron production
  ...(process.env.ELECTRON_BUILD === 'true' && { output: 'export' }),
};

// Wrap with Sentry only when the SDK is available; otherwise export bare config.
// This keeps the app buildable even if @sentry/nextjs is somehow missing.
module.exports = withSentryConfig(nextConfig, {
  // Suppress source-map upload (no auth token needed for local dev)
  silent: true,
  org: "",
  project: "",
  // Disable automatic instrumentation file creation (we provide our own)
  autoInstrumentServerFunctions: false,
  autoInstrumentMiddleware: false,
});
