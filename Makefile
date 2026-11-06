# StoreLens — see SPEC.md §5.3
PY      ?= uv run python
UVICORN ?= uv run uvicorn
PORT    ?= 8000
export STORELENS_PACING ?= 1

.PHONY: llm-check browsers setup data dev backend frontend test eval record demo preflight screenshots backup-video deck demo-script all-demo-assets build stop

setup:            ## install Python + JS deps + the Chromium browser used by preflight/screenshots/video
	uv sync --extra dev --extra llm
	cd frontend && npm install --no-audit --no-fund
	$(MAKE) browsers

browsers:         ## Chromium for Playwright (one-time)
	uv run playwright install chromium

data:             ## generate the mock data (seed 42, 1,500 stores)
	$(PY) scripts/generate_store_ops_mock_data.py --out data/raw --seed 42 --stores 1500

backend:
	cd backend && $(UVICORN) app.main:app --port $(PORT) --reload

frontend:
	cd frontend && npm run dev

dev:              ## backend :8000 + frontend :5173 (vite proxies /api)
	$(MAKE) -j2 backend frontend

build:            ## production frontend build (served by FastAPI)
	cd frontend && npm run build

test:             ## unit + agent + API tests (template mode)
	$(PY) -m pytest -q

eval:             ## golden-set eval -> eval/out/report.md + summary.json
	$(PY) eval/run_eval.py --mode template
	-$(PY) eval/run_eval.py --mode live

llm-check:        ## one real round-trip (text + tool call) to the configured live LLM
	$(PY) scripts/llm_check.py

record:           ## live provider (or template) -> replay cache for every demo question × persona
	$(PY) scripts/record_replay.py

demo: build       ## production build, replay mode, opens the browser
	@(sleep 4; (command -v xdg-open >/dev/null && xdg-open http://localhost:$(PORT)/) || (command -v open >/dev/null && open http://localhost:$(PORT)/) || true) &
	cd backend && STORELENS_CLOCK=running $(UVICORN) app.main:app --port $(PORT)

stop:
	-pkill -f "uvicorn app.main:app"

preflight:        ## data present, manifest IDs resolve, replay coverage 100%, e2e in template mode, LLM ping (warn)
	$(PY) demo/preflight.py

screenshots:      ## capture every demo step (2x DPR) -> demo/out/screens + deck/assets
	$(PY) demo/screenshots.py

backup-video:     ## Playwright drives the §11.2 flow with captions -> demo/out/storelens_demo.mp4
	$(PY) demo/record_backup.py

demo-script:      ## regenerate demo/demo_script.md with live numbers from the API
	$(PY) demo/make_demo_script.py

deck:             ## deck/out/StoreLens.pptx (+ .pdf via LibreOffice if available)
	$(PY) deck/build_deck.py

all-demo-assets: record demo-script screenshots backup-video eval deck
