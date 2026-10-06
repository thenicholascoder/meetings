const fs = require('fs');
const path = require('path');

function copyDir(source, dest) {
  fs.mkdirSync(dest, { recursive: true });
  for (const entry of fs.readdirSync(source, { withFileTypes: true })) {
    const from = path.join(source, entry.name);
    const to = path.join(dest, entry.name);
    if (entry.isDirectory()) {
      copyDir(from, to);
    } else {
      fs.copyFileSync(from, to);
    }
  }
}

// pdf.js renders decks in the browser. The worker and font data must be same-origin
// because the app sends a Cross-Origin-Embedder-Policy header.
function copyPdfjsAssets() {
  const pkg = path.join(__dirname, 'node_modules', 'pdfjs-dist');
  const worker = path.join(pkg, 'build', 'pdf.worker.min.mjs');
  if (!fs.existsSync(worker)) {
    return;
  }
  const publicDir = path.join(__dirname, 'public');
  fs.copyFileSync(worker, path.join(publicDir, 'pdf.worker.min.mjs'));
  const pdfModule = path.join(pkg, 'build', 'pdf.min.mjs');
  if (fs.existsSync(pdfModule)) {
    fs.copyFileSync(pdfModule, path.join(publicDir, 'pdf.min.mjs'));
  }
  for (const folder of ['cmaps', 'standard_fonts']) {
    const source = path.join(pkg, folder);
    if (fs.existsSync(source)) {
      copyDir(source, path.join(publicDir, 'pdfjs', folder));
    }
  }
}

copyPdfjsAssets();

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  reactStrictMode: false,
  // Egress Chrome loads /egress at http://host.docker.internal:3000 while
  // Next is running on the host. Next.js 15 rejects that host unless it is
  // listed here, and the recorder page never reaches START_RECORDING.
  allowedDevOrigins: ['host.docker.internal'],
  experimental: {
    // Stop waits until Chrome has opened the room, which can take longer
    // than the default 30s proxy.
    proxyTimeout: 90000,
  },
  eslint: {
    ignoreDuringBuilds: true,
  },
  typescript: {
    ignoreBuildErrors: false,
  },
  productionBrowserSourceMaps: true,
  images: {
    formats: ['image/webp'],
  },
  webpack: (config) => {
    config.resolve.alias.canvas = false;
    config.resolve.alias.encoding = false;
    config.module.rules.push({
      test: /\.mjs$/,
      enforce: 'pre',
      use: ['source-map-loader'],
      // @mediapipe/tasks-vision points at vision_bundle_mjs.js.map, which the
      // package does not publish. Skip it so the dev server does not warn.
      exclude: /[\\/]@mediapipe[\\/]tasks-vision[\\/]/,
    });
    return config;
  },
  async rewrites() {
    const api = process.env.API_INTERNAL_URL;
    if (!api) {
      return [];
    }
    return [
      {
        source: '/api/:path*',
        destination: `${api}/api/:path*`,
      },
    ];
  },
  headers: async () => {
    return [
      {
        source: '/(.*)',
        headers: [
          {
            key: 'Cross-Origin-Opener-Policy',
            value: 'same-origin',
          },
          {
            key: 'Cross-Origin-Embedder-Policy',
            value: 'credentialless',
          },
        ],
      },
    ];
  },
};

module.exports = nextConfig;
