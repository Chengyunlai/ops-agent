# AstroPaper 集成 GitHub Pages 调研

> 调研日期：2026-08-04
> AstroPaper 基线：官方仓库 `main` 的
> [`fb336a6`](https://github.com/satnaing/astro-paper/tree/fb336a6c8540f5b0e38b6858fa4004473acda722)
> 资料范围：AstroPaper 官方仓库、Astro 官方部署文档、GitHub Pages 官方文档。

## 推荐结论

可以采用 AstroPaper，但应把它定位为 Ops Agent 的轻量产品站与文章入口，而不是
把全部工程文档强行迁移成博客。

推荐在仓库新增独立的顶层 `website/`：将 AstroPaper 源码作为受控模板代码纳入
仓库，产品介绍、快速开始和截图作为页面，版本说明或实践文章放 Posts；现有
`docs/adr/`、`docs/plans/`、贡献与安全规范继续保持各自的工程文档职责。不要使用
Git submodule，也不要让 Node 构建进入 Python 的 `apps/` 或 `packages/`。

首个发布地址建议使用 GitHub Project Pages：

```text
https://chengyunlai.github.io/ops-agent/
```

对应配置应为：

```ts
// website/astro-paper.config.ts
site: {
  url: "https://chengyunlai.github.io",
  // title / description / author ...
}

// website/astro.config.ts
export default defineConfig({
  site: config.site.url,
  base: "/ops-agent",
  // AstroPaper 原有配置 ...
});
```

工作流使用 Astro 官方推荐的 `withastro/action`，指定 `path: ./website`，提交
`pnpm-lock.yaml`，固定 Node 24 和选定的 pnpm 版本。GitHub 仓库 Pages 的 Source
选择 **GitHub Actions**。自定义域名暂不作为首发阻塞项；以后启用时再添加
`public/CNAME`、把 `site` 改为自定义域名并移除 `base`。

## 已核对事实

### 许可证

AstroPaper 根目录 [`LICENSE`](https://github.com/satnaing/astro-paper/blob/fb336a6c8540f5b0e38b6858fa4004473acda722/LICENSE)
是 MIT License，版权声明为 `Copyright (c) 2023 Sat Naing`。官方
[`README` 的 License 章节](https://github.com/satnaing/astro-paper/tree/fb336a6c8540f5b0e38b6858fa4004473acda722#-license)
同样声明 MIT，但展示的版权年份为 2026；两处年份并不一致。

MIT 允许使用、修改和再发行，但要求在软件副本或实质部分中保留原版权与许可
声明。因此集成时应：

- 保留一份 AstroPaper 原始 MIT 声明及 `2023 Sat Naing` 版权信息；
- 明确 AstroPaper 来源，不把上游主题代码误写为 Ops Agent 独立原创；
- Ops Agent 自身继续使用 Apache-2.0，第三方主题声明单独放入
  `website/THIRD_PARTY_NOTICES.md` 或等价位置，避免覆盖根许可证。

这两个许可证可以在同一仓库中并存；这里的关键维护义务是保留上游 MIT 声明。

### Node 与包管理器

当前 AstroPaper [`package.json`](https://github.com/satnaing/astro-paper/blob/fb336a6c8540f5b0e38b6858fa4004473acda722/package.json)
声明：

- Node.js `>=22.12.0`；
- Astro `^7.0.3`；
- 没有 `packageManager` 字段；
- 构建脚本依次运行 `astro check`、`astro build` 和 Pagefind 索引。

官方 README 同时给 pnpm、npm、yarn、bun 的模板创建方式，但后续本地运行和命令
表以 pnpm 为主；仓库也提交了
[`pnpm-lock.yaml`](https://github.com/satnaing/astro-paper/blob/fb336a6c8540f5b0e38b6858fa4004473acda722/pnpm-lock.yaml)。

Astro 官方 GitHub Pages 指南说明，`withastro/action` 会从 lockfile 自动识别 npm、
yarn、pnpm 或 bun，并明确要求提交自动生成的 lockfile；Action 也允许显式设置
`node-version`、`package-manager` 和 `build-cmd`。见
[Astro 官方 GitHub Pages 部署文档](https://docs.astro.build/en/guides/deploy/github/#how-to-deploy)。

建议本项目只支持一种站点开发链路：Node 24 + pnpm。除了提交 lockfile，还应在
`package.json` 固定实际使用的 pnpm 版本，并让本地 CI 与 Pages 使用相同版本，避免
“上游允许多包管理器”演变为四套锁文件和不可复现构建。

### 内容与配置结构

AstroPaper 官方
[`README` 的 Project Structure](https://github.com/satnaing/astro-paper/tree/fb336a6c8540f5b0e38b6858fa4004473acda722#-project-structure)
列出以下核心结构：

```text
public/                    静态文件与默认 OG 图片
src/content/pages/         独立内容页面，例如 about.md
src/content/posts/         Markdown / MDX 文章；可按子目录形成 URL
src/components/            页面组件
src/layouts/               布局
src/pages/                 Astro 路由页面
astro-paper.config.ts      用户级站点、文章和功能配置
astro.config.ts            Astro 构建配置
```

[`src/content.config.ts`](https://github.com/satnaing/astro-paper/blob/fb336a6c8540f5b0e38b6858fa4004473acda722/src/content.config.ts)
使用 Astro Content Collections：

- Posts 读取 `src/content/posts/**/*.{md,mdx}`；以下划线开头的文件不加载；
- Post 必填 `pubDatetime`、`title`、`description`，支持作者、更新时间、draft、tags、
  OG 图、canonical URL 等字段；
- Pages 读取 `src/content/pages/**/*.{md,mdx}`，必填 `title`。

[`astro-paper.config.ts`](https://github.com/satnaing/astro-paper/blob/fb336a6c8540f5b0e38b6858fa4004473acda722/astro-paper.config.ts)
集中管理站点 URL、标题、描述、作者、语言、时区、分页、主题/搜索、编辑链接、
社交与分享链接。AstroPaper 的
[`astro.config.ts`](https://github.com/satnaing/astro-paper/blob/fb336a6c8540f5b0e38b6858fa4004473acda722/astro.config.ts)
目前把 `config.site.url` 传给 Astro 的 `site`，但没有设置 `base`。

这套信息架构天然偏向“博客 + 少量独立页面”。适合 Ops Agent 产品介绍、安装、
截图、发布文章和排障实践；它不直接提供版本化文档、深层侧边栏或完整 API 参考
模型。因此不建议把所有 ADR、计划、贡献规范转换成 Posts。

## GitHub Project Pages 部署

### 为什么是 `/ops-agent/`

GitHub Pages 区分用户/组织站点和项目站点。项目站点默认发布到：

```text
https://<owner>.github.io/<repository>/
```

见 GitHub 官方的
[GitHub Pages 站点类型说明](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages#types-of-github-pages-sites)。
因此 `Chengyunlai/ops-agent` 的默认项目站点路径是 `/ops-agent/`，不是域名根 `/`。

Astro 官方文档要求：

- `site` 设置为 `https://<username>.github.io`；
- 普通项目仓库设置 `base: "/<repository>"`；
- 只有仓库名本身为 `<username>.github.io` 时才可以省略 `base`；
- 配置 `base` 后，内部链接必须带上 base 前缀。

来源：[Astro GitHub Pages：site 与 base 配置](https://docs.astro.build/en/guides/deploy/github/#how-to-deploy)、
[Astro `site` 配置参考](https://docs.astro.build/en/reference/configuration-reference/#site)、
[Astro `base` 配置参考](https://docs.astro.build/en/reference/configuration-reference/#base)。

AstroPaper 当前已经在内部使用 `import.meta.env.BASE_URL` 和 `withBase` 辅助函数处理
部分路径，但新增的产品页面、图片、导航、编辑链接和 Markdown 中的绝对路径仍需
逐项验证。不要假设加一行 `base` 后所有自定义内容都会自动正确。

### GitHub Actions 工作流

Astro 官方推荐使用官方维护的 `withastro/action` 构建并上传 Pages artifact，再用
`actions/deploy-pages` 发布。当前官方示例使用：

- `actions/checkout@v7`；
- `withastro/action@v6`；
- `actions/deploy-pages@v5`；
- `contents: read`、`pages: write`、`id-token: write` 权限；
- `github-pages` Environment；
- push 到 `main` 与手工 `workflow_dispatch` 触发。

完整来源：[Astro 官方工作流](https://docs.astro.build/en/guides/deploy/github/#how-to-deploy)，
其官方文档源码为
[`withastro/docs` GitHub Pages 指南](https://github.com/withastro/docs/blob/3aee442fa4c2008d169fab13a27a5eb51d8a6775/src/content/docs/en/guides/deploy/github.mdx)。

本项目是 monorepo，工作流应显式告诉 Action 站点不在仓库根目录：

```yaml
name: Deploy website

on:
  push:
    branches: [main]
    paths:
      - "website/**"
      - ".github/workflows/deploy-website.yml"
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: withastro/action@v6
        with:
          path: ./website
          node-version: 24
          package-manager: pnpm@<已验证并固定的版本>

  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - name: Deploy to GitHub Pages
        id: deployment
        uses: actions/deploy-pages@v5
```

然后在仓库 **Settings → Pages → Build and deployment → Source** 选择
**GitHub Actions**。GitHub 官方也说明，自定义工作流发布时应选择 GitHub Actions
作为发布源；见
[配置 Pages 发布源](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)。

工作流应沿用项目现有 PR 审核：PR 中先执行站点 type-check/build，只有合并到 main
才部署。不要让失败的站点构建绕过现有必需检查。

## 自定义域名是可选的第二阶段

Astro 官方流程要求自定义域名启用时：

1. 在域名提供商处配置 DNS；
2. 新增 `website/public/CNAME`，内容只有域名；
3. 把 Astro `site` 改为完整自定义域名；
4. 删除 `base: "/ops-agent"`；
5. 移除内部链接中的项目路径前缀。

来源：[Astro：Change your GitHub URL to a custom domain](https://docs.astro.build/en/guides/deploy/github/#change-your-github-url-to-a-custom-domain)。

GitHub 还建议验证自定义域名以防止接管，并在 DNS 正常后启用 HTTPS；见
[管理自定义域名](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/managing-a-custom-domain-for-your-github-pages-site)、
[验证自定义域名](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/verifying-your-custom-domain-for-github-pages)
和
[为 Pages 启用 HTTPS](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/securing-your-github-pages-site-with-https)。

建议先稳定发布 `/ops-agent/`，不要让域名购买、DNS、证书和链接迁移阻塞首版。
未来切换域名必须作为一次完整迁移测试，而不是只增加 CNAME。

## 主要维护风险与控制方式

| 风险 | 依据与影响 | 建议控制 |
| --- | --- | --- |
| 上游升级需要人工合并 | AstroPaper 是通过 `create astro --template` 创建的完整源码模板，不是运行时主题包；本地修改后无法像普通依赖一样无冲突升级。见 [Running Locally](https://github.com/satnaing/astro-paper/tree/fb336a6c8540f5b0e38b6858fa4004473acda722#-running-locally) | 记录导入的上游 commit；只吸收安全、兼容性和明确需要的上游变化，不追求持续镜像 main。 |
| 第二套技术栈 | 当前主题要求 Node `>=22.12.0`、Astro 7、Tailwind 4、pnpm/Pagefind，与 Python 应用生命周期不同 | `website/` 独立 lockfile、命令和 CI；根 Makefile 只提供薄入口，不把 Node 依赖装进 Python 包。 |
| `/ops-agent/` 子路径 | Astro 官方警告配置 `base` 后所有内部链接都必须带 base；绝对 `/image`、`/posts`、搜索和自定义组件容易在本地根路径正常、线上失效 | CI 在生产 base 下构建；检查生成 HTML 中的根绝对链接，并对首页、文章、图片、RSS、sitemap、Pagefind 做部署后冒烟测试。 |
| SEO 与分享元数据 | AstroPaper 用 `site` 生成 canonical、RSS、sitemap、OG 图；错误地把 `site` 配成含 `/ops-agent/` 的值或遗漏 base 会产生错误 URL | 遵循 Astro 的 `site = origin`、`base = /ops-agent` 模型；验证 canonical、OG、RSS 与 sitemap 的实际 URL。 |
| 许可证年份不一致 | LICENSE 为 MIT ©2023，README 显示 MIT ©2026 | 以 LICENSE 原文履行保留义务，并在第三方声明记录上游 commit，不自行“统一”上游版权年份。 |
| 依赖与 Action 漂移 | `package.json` 没有固定包管理器版本；Pages 构建还依赖多个 GitHub Actions major | 固定 pnpm，提交 lockfile；PR 中运行 build；定期但分批更新依赖和 Actions，保留人工审核。 |
| 自定义域名切换破坏路径 | Astro 官方要求启用域名时移除 base 和相关链接前缀 | 将域名迁移做成独立 PR；先预览、检查 DNS/HTTPS，再切换生产 URL。 |
| 内容职责混乱 | AstroPaper 的官方内容模型是 Posts + 少量 Pages，并非完整产品文档系统 | 产品站只承载稳定用户内容和文章；工程 ADR、贡献、安全、发布流程继续在仓库文档中维护，通过站点链接，不复制两份事实源。 |

## 推荐落地边界

```text
website/
├── package.json
├── pnpm-lock.yaml
├── astro.config.ts
├── astro-paper.config.ts
├── THIRD_PARTY_NOTICES.md
├── public/
└── src/
    ├── content/
    │   ├── pages/       # 关于、安全边界、安装等稳定页面
    │   └── posts/       # 发布说明、使用实践、重要更新
    ├── components/
    ├── layouts/
    └── pages/           # 产品首页与必要路由

docs/                    # 现有工程文档继续保留
.github/workflows/
├── website-ci.yml       # PR: format/lint/check/build/link checks
└── deploy-website.yml   # main: build + GitHub Pages deploy
```

集成顺序建议：

1. 以当前固定 commit 导入 AstroPaper 到 `website/`，补第三方许可声明；
2. 删除上游示例文章、社交账号和分析配置，先做 Ops Agent 首页与安装页；
3. 配置 `site`/`base`，统一所有链接和图片的 base-aware 写法；
4. 加 PR 构建检查，并验证本地生产构建；
5. 通过独立部署工作流发布 `/ops-agent/`；
6. 对首页、文章、图片、搜索、RSS、sitemap、canonical 做线上冒烟测试；
7. 只有产品站稳定后再评估自定义域名。

## 一手资料索引

- [AstroPaper 官方仓库](https://github.com/satnaing/astro-paper)
- [AstroPaper README（调研基线）](https://github.com/satnaing/astro-paper/tree/fb336a6c8540f5b0e38b6858fa4004473acda722)
- [AstroPaper LICENSE](https://github.com/satnaing/astro-paper/blob/fb336a6c8540f5b0e38b6858fa4004473acda722/LICENSE)
- [AstroPaper package.json](https://github.com/satnaing/astro-paper/blob/fb336a6c8540f5b0e38b6858fa4004473acda722/package.json)
- [AstroPaper 内容 schema](https://github.com/satnaing/astro-paper/blob/fb336a6c8540f5b0e38b6858fa4004473acda722/src/content.config.ts)
- [AstroPaper 用户配置](https://github.com/satnaing/astro-paper/blob/fb336a6c8540f5b0e38b6858fa4004473acda722/astro-paper.config.ts)
- [Astro 官方 GitHub Pages 部署指南](https://docs.astro.build/en/guides/deploy/github/)
- [Astro 官方配置参考](https://docs.astro.build/en/reference/configuration-reference/)
- [GitHub Pages 基础与站点类型](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages)
- [GitHub Pages 发布源](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)
- [GitHub Pages 自定义域名](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/about-custom-domains-and-github-pages)
