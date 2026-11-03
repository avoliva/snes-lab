.PHONY: check test test-rust test-py lint

check: lint test

test: test-rust test-py

test-rust:
	cargo test --workspace

test-py:            ## rebuilds the Rust extension first
	uv sync --reinstall-package snes-kit -q
	uv run pytest

lint:
	cargo fmt --all --check
	cargo clippy --all-targets -- -D warnings
	uv run ruff check src tests scripts crates/snes-kit-py/python
	uv run ruff format --check src tests scripts crates/snes-kit-py/python
	uv run mypy --strict crates/snes-kit-py/python/snes_kit
