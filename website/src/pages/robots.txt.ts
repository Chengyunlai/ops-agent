import type { APIRoute } from "astro";
import { getAssetPath } from "@/utils/withBase";

const getRobotsTxt = (sitemapURL: URL) => `
User-agent: *
Allow: /

Sitemap: ${sitemapURL.href}
`;

export const GET: APIRoute = ({ site }) => {
  const sitemapURL = new URL(getAssetPath("sitemap-index.xml"), site);
  return new Response(getRobotsTxt(sitemapURL));
};
