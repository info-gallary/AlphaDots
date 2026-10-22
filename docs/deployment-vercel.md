# Deploying the dashboard to Vercel

The dashboard in `web/` is a standard Next.js app and deploys to Vercel as is. This page
covers what gets deployed, what does not, and how to choose where its data comes from.

## What runs where

| Component | Vercel | Why |
|---|---|---|
| Dashboard (`web/`, Next.js) | Yes | Request/response app; its `/api/*` routes run as Node functions. |
| Redpanda, PostgreSQL | No | Stateful, long-lived servers. |
| Preprocessor, sentiment, signal engine | No | Long-running Kafka consumers. Serverless functions are request-scoped and cannot hold a consumer group. |
| FastAPI read API | Not on Vercel, by design | Its read model is fed continuously by a Kafka projection. |

So the pipeline runs where it already runs (Docker Compose locally; Kubernetes or AWS
later, per the milestones), and the dashboard picks one of two data sources.

## Data sources

The browser only ever calls the dashboard's own `/api/...` routes. On the server, those
routes answer in one of two ways:

| Mode | When | Behaviour |
|---|---|---|
| **Snapshot** (default) | `ALPHADOTS_API_URL` is unset | Answers from `web/src/data/snapshot.json`, the real pipeline's read model over the synthetic fixtures, plus one research brief per issuer and one monitor run recorded at export. The top bar shows a **Snapshot** chip. Nothing to configure. |
| **Live** | `ALPHADOTS_API_URL` is set | Proxies each request to that API, adding `ALPHADOTS_API_TOKEN` as a bearer token if set. The UI polls for new data. |

### How the snapshot stays honest

- `scripts/export_snapshot.py` replays the fixtures through the real service code
  in-process and dumps the API's read model. It is not hand-written data.
- The research briefs and monitor summary in the snapshot are real runs of the LangGraph
  graphs (deterministic mock model) against that same read model. Their `available_at` is
  fixed to the end of the replayed window and `latency_ms` is stored as null, since a
  timing would change on every export; the UI shows "not recorded".
- The snapshot mode answers point-in-time queries in TypeScript
  (`web/src/server/snapshot-api.ts`), a port of the API's read rules.
- The same script records the real FastAPI app's responses to 36 requests
  (`web/test/parity.golden.json`): point-in-time cut-offs, a retired ticker, pagination
  cursors and every error path. `pnpm test` in `web/` requires identical answers.
- `tests/e2e/test_dashboard_snapshot.py` fails if the committed snapshot no longer
  matches what the pipeline produces. After changing any stage, run `make snapshot`.

## Deploy with the Vercel dashboard

1. Push the repository to GitHub, GitLab or Bitbucket.
2. In Vercel, choose **Add New → Project** and import the repository.
3. Set **Root Directory** to `web`. This is the one setting that matters; Vercel cannot
   read it from a config file.
4. Leave the framework (Next.js), install and build commands as detected.
   `web/vercel.json` pins them to `pnpm install --frozen-lockfile` and `pnpm build`.
5. Deploy. With no environment variables set, the dashboard runs in snapshot mode.

Node.js 22 is selected from `engines.node` in `web/package.json`.

## Deploy with the Vercel CLI

```bash
cd web
vercel link          # creates the project; its root directory is this folder
vercel               # preview deployment
vercel --prod        # production deployment
```

## Attaching a live API

Set these under **Project Settings → Environment Variables**, then redeploy:

| Variable | Required | Notes |
|---|---|---|
| `ALPHADOTS_API_URL` | for live mode | Base URL of a running AlphaDots API, e.g. `https://alphadots-api.example.com`. Must be reachable from Vercel's servers; `localhost` will not work. |
| `ALPHADOTS_API_TOKEN` | if the API requires it | Server-side only; never sent to the browser. |

To demo against your local Docker stack, expose port 18000 through a tunnel you
control and set the tunnel URL. Start the API with `ALPHADOTS_API_TOKEN` set when you do,
so the tunnel is not an open endpoint.

## Security properties of the `/api` routes

- The upstream origin comes from configuration only, and the path must match an
  allowlist (`v1/...` or `health/live|ready`), so the route cannot act as an open proxy.
- Reads are `GET`. The one write is `POST /api/v1/research/jobs` (ask the agent for a
  brief), accepted only in live mode, only as `application/json` (so a cross-site HTML
  form cannot trigger it), with `Sec-Fetch-Site: cross-site` rejected, a 1 KiB body cap,
  and only `symbol` and `as_of` forwarded. Snapshot mode answers it with 503.
- Redirects from the upstream are refused, and calls time out after 10 seconds.
- The bearer token is added on the server. `/api/source` reports the mode but never the
  upstream address.
- Every page sends `X-Content-Type-Options`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`, `Permissions-Policy` and HSTS.
- News text is untrusted. React escapes it everywhere and no route renders raw HTML.
  Fixture source URLs use the reserved `.invalid` domain and are shown as text, not links.

## Caching

Snapshot responses are immutable for the life of a deployment, so successful ones carry
`s-maxage=3600, stale-while-revalidate=86400` and are served from Vercel's CDN after the
first request. Live responses are `no-store`.

## Verify before deploying

```bash
cd web
pnpm install --frozen-lockfile
pnpm lint && pnpm typecheck && pnpm test   # parity with the real API
pnpm build                                 # the same build Vercel runs
pnpm start                                 # http://localhost:3000, snapshot mode
```

`pnpm build` is what Vercel runs. After it, you can confirm the snapshot ships inside the
API function bundle: `.next/server/app/api/[...path]/route.js.nft.json` lists
`src/data/snapshot.json`.

## Limits worth knowing

- Snapshot mode is a static view: the as-of slider, filters, charts, drill-downs and the
  recorded research briefs all work, and nothing new arrives. Generating a new brief
  needs a live API.
- The snapshot is about 600 KiB and is read once per function instance.
- Vercel is not used for the pipeline, so nothing here demonstrates its throughput or
  latency. No performance figures are claimed (milestone M6).
