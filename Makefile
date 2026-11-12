.PHONY: test lint clean

test:
	uv run --locked pytest -q

lint:
	uv run --locked ruff check
	uv run --locked ty check

clean:
	rm -rf build dist .work/tmp .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -not -path './.venv/*' -prune -exec rm -rf {} +
