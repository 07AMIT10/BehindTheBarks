PY ?= .venv/bin/python
.PHONY: dev-backend dev-frontend dev demo test-web
dev-backend:
	$(PY) -m uvicorn backend.main:app --reload --port 8000
dev-frontend:
	cd frontend && npm run dev
dev:
	$(MAKE) -j2 dev-backend dev-frontend
demo:
	DEMO_MODE=1 $(PY) -m uvicorn backend.main:app --port 8000
test-web:
	$(PY) -m pytest -q tests/web
