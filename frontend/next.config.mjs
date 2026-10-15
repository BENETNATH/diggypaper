import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./src/i18n.ts");

/** @type {import('next').NextConfig} */
const nextConfig = {
  poweredByHeader: false,
  // En dev, on proxy /api/v1 vers le backend FastAPI local pour éviter les
  // problèmes CORS (le client appelle en same-origin /api/v1/...).
  // En prod, c'est nginx qui fait le proxy (same-origin), donc ce rewrite est
  // inoffensif (il pointe vers le même backend).
  async rewrites() {
    const apiOrigin = process.env.NEXT_PUBLIC_API_ORIGIN ?? "http://localhost:8000";
    return [
      {
        source: "/api/v1/:path*",
        destination: `${apiOrigin}/api/v1/:path*`,
      },
    ];
  },
};

export default withNextIntl(nextConfig);
