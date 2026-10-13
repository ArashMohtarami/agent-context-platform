# Agent Context Platform website

A portable introduction and complete offline field guide. The source page
uses plain HTML, CSS and JavaScript without a framework or external assets.
No runtime backend, API key, analytics or external client is needed to serve it.

Build from the repository root with the project's installed Python environment:

```bash
.venv/bin/python -m pip install -r docs/guide/requirements.txt
.venv/bin/python website/build.py
```

Output: `dist/site/`. Host only that directory.
The build regenerates the public guide, exports allowed linked files, verifies
local links and anchors, and writes `site-manifest.json` with SHA-256 hashes.
An optional repeated `--forbid-text PRIVATE_NAME` rejects private names or
phrases supplied locally without recording those names in public source.

Preview and public-IP Nginx/container instructions are in
[docs/website.md](../docs/website.md). The website Compose configuration is
[deploy/website/compose.yml](../deploy/website/compose.yml) and runs separately
from the API/database deployment.

Edit `index.html` and `assets/` for the introduction. Edit `docs/guide/` for the
complete guide. Public-source additions require an explicit update to
`docs/guide/public-sources.json`; files are never embedded by globbing the
documentation directory. A failed export preserves previously built public
files, and a successful build replaces files while keeping the mounted output
directory stable.
