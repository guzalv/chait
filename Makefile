VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(PY) -m pytest

.PHONY: test test-api test-integration test-ui deps test-deps server docker help

server: deps ## Start the server
	$(PY) server.py

test: test-deps test-api test-integration test-ui ## Run all tests

test-api: test-deps ## API tests (no server needed)
	$(PYTEST) tests/test_api.py -v

test-integration: test-deps ## Integration tests with mock agents (no server needed)
	$(PYTEST) tests/test_integration.py -v

test-ui: test-deps ## UI tests (requires running server + chromium)
	$(PYTEST) tests/test_ui.py -v

deps: $(VENV) ## Install runtime dependencies
	$(PIP) install -q -r requirements.txt

test-deps: deps
	$(PIP) install -q pytest httpx

$(VENV):
	python3 -m venv $(VENV)

docker: ## Build and run via Docker
	docker build -t chait . && docker run -p 3100:3100 -e CHAIT_HUMAN_USER=admin -e CHAIT_HUMAN_PASS=changeme chait

help: ## Show this help
	@grep -E '^[a-z][a-z-]*:.*##' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'
