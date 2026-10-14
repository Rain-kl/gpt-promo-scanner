# Promo Scanner · Local Control Panel

[简体中文](README.md) | **English**

A local web panel for the existing ChatGPT Business promo scanner. Configure connections, control jobs, inspect live progress and logs, and export results in your browser.

The Chinese interface follows [Impeccable](https://github.com/pbakaus/impeccable)'s Operate guidance. FastAPI serves a plain HTML/CSS/JavaScript frontend. Python dependencies use **uv, pyproject.toml, and uv.lock**; Node is not required to run the app.

## Start

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and use Python 3.11+ (the repository selects Python 3.12 by default).

```bash
git clone https://github.com/Rain-kl/gpt-promo-scanner.git
cd gpt-promo-scanner
uv sync --frozen
uv run python -m promo_dashboard
```

Enter `http://127.0.0.1:8765` in a local browser. Use `--port 8888` to select another port.

No configuration-file editing is needed. Start with **预览候选** (preview candidates), which does not need credentials or a proxy. Configure real connections in **连接设置** before making authenticated requests.

## Features

- Workspace: region and task selection, offline preview, start/pause/resume/stop.
- Results: real progress, elapsed time, eligible/existing/error/skipped counts, searchable and paginated rows, incremental logs, checkout links.
- History: review retained jobs, logs and results; delete finished records.
- Reference catalog: search and region filters, with the repository's historical update date clearly shown.
- JSON and CSV exports that respect the current search and status filters.

**Discovery** combines base names and country suffixes. **Known-code scanning** reuses checkout and price logic and can switch Clash nodes. **Matrix discovery** crosses base names and regions and also tries names without suffixes. Discovery/matrix candidates are deduplicated. Every mode supports offline previews; individual jobs are limited to 5,000 candidates.

Discovery and matrix jobs use the current outbound connection; the selected region generates candidate suffixes. Use known-code scanning for automatic region node switching.

## Browser settings

All settings are editable in the panel: ChatGPT Access Token, outbound proxy (empty for direct access), Clash Unix socket or HTTP API, controller secret, rule/global groups, automatic switching, region keywords, request interval and timeout, default mode/region, price collection, built-in sources, custom codes and custom base names.

Blank credential fields preserve saved values; explicit checkboxes clear them. Panel settings override inherited proxy environment variables in workers. Every job receives an isolated snapshot, so changes affect new jobs.

The controller must be reachable from the machine running the panel. Discovery and offline previews do not require Clash. Token validity is checked before live execution. Authentication failures, rate limiting, or repeated connection errors stop the job rather than producing false “not found” results.

One job runs at a time. Pause takes effect after the current request completes. Stop interrupts execution and retains existing results. The runner attempts to restore the original Clash node; check the node yourself after a forced termination.

Settings, credentials, jobs, bounded logs and incremental results are stored in the ignored `.dashboard/` directory; the SQLite database uses mode `0600`. Credentials are not returned by settings endpoints and are redacted from logs/results/exports. Protect the local directory and backups because credentials are stored on disk.

The server binds only to loopback addresses and checks request origins and Host headers. It is intended for a local single user. A restart marks unfinished jobs as interrupted and preserves saved results. Reference codes/prices are historical, not newly verified. Prices use the existing scanner's estimates; the checkout page determines the final amount. The panel does not complete payments.

## Development

```bash
uv sync --frozen
uv run pytest -q
uv run ruff check promo_dashboard tests config.py
uv run ruff format --check promo_dashboard tests config.py
node --check promo_dashboard/static/app.js
```

Browser checks use Chromium and offline previews only:

```bash
uv run playwright install chromium
uv run python tests/browser_smoke.py
```

The script prefers a system Chromium and otherwise uses Playwright's installed browser. It covers settings, previews, filters, exports, history, controls, and mobile layouts. Screenshots go into ignored `test-artifacts/`.

Existing CLI entry points remain available through uv:

```bash
uv run python auto_scan.py --list
uv run python discover_codes.py --list
```

Legacy CLI environment/TOML configuration remains compatible; the dashboard uses its own saved settings.

[MIT](LICENSE). The bundled Manrope font uses the [SIL Open Font License](promo_dashboard/static/fonts/OFL.txt).
