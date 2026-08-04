/* eslint-disable no-console -- verification output is the script interface */
import { readdir, readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import deployment from "../deployment.config.json" with { type: "json" };

const { base, origin } = deployment;
const siteUrl = new URL(`${base}/`, origin).href;
const dist = fileURLToPath(new URL("../dist/", import.meta.url));
const failures = [];

async function walk(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(
    entries.map(entry => {
      const target = path.join(directory, entry.name);
      return entry.isDirectory() ? walk(target) : [target];
    })
  );
  return nested.flat();
}

const files = await walk(dist);
const htmlFiles = files.filter(file => file.endsWith(".html"));

for (const file of htmlFiles) {
  const content = await readFile(file, "utf8");
  for (const match of content.matchAll(/\b(?:href|src)="(\/[^"#]*)"/g)) {
    const target = match[1];
    if (target !== base && !target.startsWith(`${base}/`)) {
      failures.push(`${path.relative(dist, file)}: root path ${target}`);
    }
  }
}

const requiredFiles = [
  "index.html",
  "docs/getting-started/index.html",
  "docs/configuration/index.html",
  "docs/tui/index.html",
  "docs/architecture/index.html",
  "pagefind/pagefind.js",
  "sitemap-index.xml",
  "rss.xml",
  "robots.txt",
];

const requiredFragments = {
  "index.html": [
    'id="menu-btn"',
    'data-label-open="打开菜单"',
    'id="theme-btn"',
    `href="${base}/search/"`,
  ],
  "search/index.html": [
    'id="pagefind-search"',
    `data-bundle-path="${base}/pagefind/"`,
  ],
  "posts/v0112/index.html": [
    "复制",
    "已复制",
    "放大图片",
    "图片预览",
    "关闭图片预览",
  ],
};

for (const required of requiredFiles) {
  if (!files.includes(path.join(dist, required))) {
    failures.push(`missing ${required}`);
  }
}

for (const [relativePath, fragments] of Object.entries(requiredFragments)) {
  const content = await readFile(path.join(dist, relativePath), "utf8");
  for (const fragment of fragments) {
    if (!content.includes(fragment)) {
      failures.push(`${relativePath}: missing UI contract ${fragment}`);
    }
  }
}

const robots = await readFile(path.join(dist, "robots.txt"), "utf8");
if (!robots.includes(`${siteUrl}sitemap-index.xml`)) {
  failures.push("robots.txt does not reference the project sitemap");
}

const rss = await readFile(path.join(dist, "rss.xml"), "utf8");
if (!rss.includes(siteUrl)) {
  failures.push("rss.xml does not reference the project base URL");
}

if (failures.length > 0) {
  console.error("Website verification failed:");
  for (const failure of failures) console.error(`- ${failure}`);
  process.exitCode = 1;
} else {
  console.log(
    `Website verification passed for ${htmlFiles.length} HTML pages.`
  );
}
