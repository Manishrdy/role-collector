.PHONY: setup install playwright ollama run review test lint format clean migrate help

VENV ?= .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

help:
	@echo "Targets:"
	@echo "  setup       - create venv, install deps, install playwright chromium"
	@echo "  install     - pip install -r requirements.txt into venv"
	@echo "  playwright  - install Chromium for Playwright"
	@echo "  ollama      - pull qwen3:8b via ollama"
	@echo "  migrate     - create SQLite schema if not exists"
	@echo "  run         - run the agent end-to-end (LangGraph workflow)"
	@echo "  review      - launch the Streamlit review dashboard"
	@echo "  test        - run pytest"
	@echo "  lint        - ruff + mypy"
	@echo "  format      - ruff format"
	@echo "  clean       - remove caches and venv"

$(VENV)/bin/activate:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip

install: $(VENV)/bin/activate
	$(PIP) install -r requirements.txt

playwright: install
	$(VENV)/bin/playwright install chromium

setup: install playwright
	@echo ""
	@echo "venv ready. next steps:"
	@echo "  1. cp .env.example .env  and fill in Langfuse keys"
	@echo "  2. make ollama   # pulls qwen3:8b (~5GB)"
	@echo "  3. make migrate  # creates data/jobs.db"
	@echo "  4. make run"

ollama:
	@command -v ollama >/dev/null 2>&1 || { echo "ollama not found. install from https://ollama.com"; exit 1; }
	ollama pull qwen3:8b

migrate:
	$(PY) -m job_agent.db.migrate

run:
	$(PY) -m job_agent.cli run

review:
	$(VENV)/bin/streamlit run ui/streamlit_app.py

test:
	$(VENV)/bin/pytest

lint:
	$(VENV)/bin/ruff check src tests
	$(VENV)/bin/mypy

format:
	$(VENV)/bin/ruff format src tests
	$(VENV)/bin/ruff check --fix src tests

clean:
	rm -rf $(VENV) .mypy_cache .pytest_cache .ruff_cache **/__pycache__
