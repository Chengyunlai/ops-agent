# Ops Agent website

The public product and documentation site for Ops Agent, based on AstroPaper
v6.1.0. It is an independent Node module and is not part of the Python/uv
workspace.

```bash
corepack enable
pnpm install --frozen-lockfile
pnpm dev
pnpm check
```

The production build targets GitHub Project Pages at
`https://chengyunlai.github.io/ops-agent/`. Keep links and assets base-aware;
the site is deployed below `/ops-agent/`, not at the domain root.

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for AstroPaper attribution.
