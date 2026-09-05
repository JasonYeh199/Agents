.PHONY: api web test demo
api:
	cd apps/api && uvicorn app.main:app --reload --port 8000
web:
	cd apps/web && npm run dev
test:
	cd apps/api && pytest
demo:
	cd apps/api && python -m app.cli --company nvidia --period FY2025-Q4 --language zh-TW
