import { defineAstroPaperConfig } from "./src/types/config";
import deployment from "./deployment.config.json";

export default defineAstroPaperConfig({
  site: {
    url: deployment.origin,
    title: "Ops Agent",
    description:
      "本地运行的 Kubernetes 终端工作台：实时监盘、日志排查与基于真实证据的只读诊断。",
    author: "yun",
    profile: "https://github.com/Chengyunlai",
    ogImage: "default-og.jpg",
    lang: "zh-CN",
    timezone: "Asia/Shanghai",
    dir: "ltr",
  },
  posts: {
    perPage: 4,
    perIndex: 4,
    scheduledPostMargin: 15 * 60 * 1000,
  },
  features: {
    lightAndDarkMode: true,
    dynamicOgImage: false,
    showArchives: false,
    showBackButton: true,
    editPost: {
      enabled: true,
      url: "https://github.com/Chengyunlai/ops-agent/edit/main/website/",
    },
    search: "pagefind",
  },
  socials: [
    {
      name: "github",
      url: "https://github.com/Chengyunlai/ops-agent",
      linkTitle: "在 GitHub 查看 Ops Agent",
    },
  ],
  shareLinks: [],
});
