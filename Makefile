.DEFAULT_GOAL := help
.PHONY: help install test lint format dev clean

VENV := .venv
BIN := $(VENV)/bin
PIP := $(BIN)/pip
DEV_DB := .rivermind-dev.db

help:  ## Show this help message
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install:  ## Create venv and install package with dev deps
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"
	@echo ""
	@echo "Done. Activate the venv: source $(BIN)/activate"

test:  ## Run pytest with coverage
	$(BIN)/pytest

lint:  ## Run ruff check, ruff format --check, and mypy
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .
	$(BIN)/mypy src/

format:  ## Autoformat with ruff
	$(BIN)/ruff format .

dev:  ## Run rivermind server against a local dev DB
	$(BIN)/python -m rivermind --port 8080 --db $(DEV_DB)

clean:  ## Remove caches, venv, build artifacts, and local dev DB
	rm -rf $(VENV) .pytest_cache .mypy_cache .ruff_cache
	rm -rf build dist
	rm -rf *.egg-info src/*.egg-info
	rm -rf htmlcov .coverage .coverage.*
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -f $(DEV_DB)
