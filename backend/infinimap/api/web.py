"""Serving the built frontend from the API process.

One process, one port, one origin. The frontend asks for `/api/v1/...` as a
*relative* path (see `frontend/src/api/client.ts`), so same-origin is not merely
convenient - it is the only arrangement that needs no configuration at all, and
`cors_origins` is dead weight for the bundled UI.

Where the bundle comes from, in order:

  1. `web_root` in api.toml, for an operator serving a build of their own
  2. `web/` inside this package -- what a wheel carries, put there at build time
  3. `frontend/dist` in a source checkout, so `npm run build` just works in dev
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .config import Config

log = logging.getLogger("infinimap.api")

#: Inside the package: `infinimap/api/web/`. Present only in a wheel whose build
#: ran `npm run build` first.
BUNDLED_WEB = Path(__file__).parent / "web"

#: A source checkout: backend/infinimap/api/web.py -> repo root -> frontend/dist
SOURCE_WEB = Path(__file__).parents[3] / "frontend" / "dist"


def find_web_root(cfg: Config) -> Path | None:
    """The directory to serve, or None if there is no build to serve."""
    if cfg.web_root is not None:
        root = Path(cfg.web_root)
        if not (root / "index.html").is_file():
            log.warning("web_root %s has no index.html; serving no UI", root)
            return None
        return root

    for candidate in (BUNDLED_WEB, SOURCE_WEB):
        if (candidate / "index.html").is_file():
            return candidate
    return None


def mount_web(app: FastAPI, cfg: Config) -> Path | None:
    """Mount the UI at `/`, or install a page explaining its absence.

    Must be called *after* every API route is registered: a mount at `/` matches
    any path, and Starlette takes the first match, so anything added afterwards
    would be shadowed.
    """
    root = find_web_root(cfg)

    if root is None:
        log.info("no frontend bundle found; serving API only")

        @app.get("/", include_in_schema=False)
        def _no_ui() -> HTMLResponse:
            return HTMLResponse(_NO_BUNDLE_HTML)

        return None

    app.mount("/", StaticFiles(directory=root, html=True), name="web")
    log.info("serving frontend from %s", root)
    return root


_NO_BUNDLE_HTML = """<!doctype html>
<title>infinimap - no UI bundled</title>
<style>
  body { font: 15px/1.6 system-ui, sans-serif; max-width: 42rem;
         margin: 4rem auto; padding: 0 1.5rem; }
  code { background: #8881; padding: .15em .4em; border-radius: .25em; }
  pre  { background: #8881; padding: .8em 1em; border-radius: .4em;
         overflow-x: auto; }
</style>
<h1>infinimap</h1>
<p>The API is running, but no frontend bundle was found, so there is no user
   interface to serve. This is expected when infinimap was installed from source
   rather than from a released wheel &mdash; <code>pip</code> cannot run
   <code>npm</code>.</p>
<p>Build it:</p>
<pre>cd frontend
npm ci
npm run build</pre>
<p>&hellip;or point <code>web_root</code> in <code>api.toml</code> at an existing
   build.</p>
<p>The API itself is unaffected: <a href="/docs">/docs</a> for the interactive
   schema, <a href="/api/v1/fabrics">/api/v1/fabrics</a> for the fabrics it can
   see.</p>
"""
