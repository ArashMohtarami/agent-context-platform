# Host the public introduction and guide

The static website introduces Agent Context Platform and links to the complete
self-contained HTML usage guide. It runs independently of the Context API.
No platform credential, database connection or model is needed to serve it.

## Build the public files

From the repository root, use the project's Python 3.12 environment with its
pinned application dependencies installed. Add the documentation-only renderer:

```bash
.venv/bin/python -m pip install -r docs/guide/requirements.txt
.venv/bin/python website/build.py
```

The script rebuilds `docs/usage-guide.html` from the current contracts and the
explicit public documentation inventory in `docs/guide/public-sources.json`.
It writes the introduction, assets, guide, MIT license and reviewed linked
examples/source files to `dist/site/`. It checks local file links and HTML
anchors and writes a hash inventory at `dist/site/site-manifest.json`.
The site also bundles `downloads/agent-context-platform-source.tar.gz` and
`downloads/SHA256SUMS`, produced by the same audited deterministic source exporter.
This archive includes frontend source, package/lockfile, Node pin and dependency
notices/licenses, not generated bundles, node_modules, browser traces, Git history,
secret files or private plans. The site manifest covers its archive/checksum bytes.
The download is a review artifact, not an approved release or automatic publication;
see [verification](verification.md). The output directory is ignored by Git and
can be rebuilt.
Exported files use mode `0644` and directories use `0755`, independent of your
shell's umask, so the unprivileged Nginx process can read the static content.

For a release-specific check, pass a private name or phrase locally with
`--forbid-text PRIVATE_NAME`; repeat the option for additional values. The
checker also rejects absolute home-directory paths and excluded source
references. Keep audit values local rather than storing them in the repository.

Guide compilation constructs API schemas without starting the application or
its lifespan. It does not load a model, contact a database, read secret files
or use the network. The site export rejects hidden files, symlinks and files
outside its explicit public file rules. The guide embeds only documents in its
public-source inventory.

To reuse a guide you have just rebuilt, use
`.venv/bin/python website/build.py --skip-guide`.

## Preview locally

You can open `website/index.html` directly from the checkout. Its guide and
license links point to the files in the repository. The build adjusts those
links for the exported `dist/site/` directory, which is the directory to serve.

```bash
.venv/bin/python -m http.server 8765 \
  --bind 127.0.0.1 --directory dist/site
```

Open `http://127.0.0.1:8765/` and
`http://127.0.0.1:8765/docs/usage-guide.html`.
Stop the development server with Ctrl+C.

## Serve on your public IP

Copy the repository to your host, build the site there, then run the separate
website Compose file from the repository root:

```bash
docker compose --env-file /dev/null -f deploy/website/compose.yml config --quiet
docker compose --env-file /dev/null -f deploy/website/compose.yml up -d --wait
```

This publishes host port 80 on all host interfaces. Allow inbound TCP 80 in
your host/provider firewall. If the host is behind a router, forward TCP 80
to it. Visit `http://YOUR_PUBLIC_IP/`, replacing the placeholder with your
actual reachable address. This repository does not contain a preselected
public IP or GitHub repository URL.

The website container mounts **only `dist/site/`** as its document root and
its public Nginx configuration. It has no access to `secrets/`, `.env`, `.git`,
database volumes, the application network or the checkout as a whole. Missing
build output fails the mount instead of silently creating an empty directory.
Directory listing is disabled, unknown paths return 404, and the server never
proxies `/mcp` or `/api` to a live backend. The `--env-file /dev/null` option
keeps the documentation server independent of the platform's private `.env`.

If port 80 is occupied, choose a different port:

```bash
WEBSITE_PORT=8088 docker compose --env-file /dev/null \
  -f deploy/website/compose.yml up -d --wait
```

Then visit `http://YOUR_PUBLIC_IP:8088/`. Bind only to loopback when placing
the site behind a reverse proxy:

```bash
WEBSITE_BIND_IP=127.0.0.1 WEBSITE_PORT=8088 \
  docker compose --env-file /dev/null -f deploy/website/compose.yml up -d --wait
```

For a domain, point its DNS record to your host and terminate HTTPS at your
chosen reverse proxy. If you later expose the actual MCP service, follow
[the deployment guide](deployment.md) for its separate authenticated HTTPS
endpoint. The public documentation server does not expose that service.

## Update or stop

Rebuild `dist/site/` after changing public documentation, skills, contracts or
the landing page. The read-only directory mount serves the new files. Verify
the introduction and guide after rebuilding.

```bash
curl --fail http://127.0.0.1/
curl --fail --head http://127.0.0.1/docs/usage-guide.html
docker compose --env-file /dev/null -f deploy/website/compose.yml ps
docker compose --env-file /dev/null -f deploy/website/compose.yml logs --tail 30
docker compose --env-file /dev/null -f deploy/website/compose.yml down
```

The static site works without JavaScript. JavaScript adds accessible workflow
tabs and copy buttons; examples are illustrative and never execute tool calls.
Clipboard access may be unavailable over plain HTTP on an IP, so copy buttons
select the commands for manual copying when necessary. The guide remains
downloadable as one HTML file with its existing offline search and examples.
