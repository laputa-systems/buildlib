.PHONY: test lint coverage clean

test:
	uv run --locked pytest -q

lint:
	uv run --locked ruff check
	uv run --locked ty check

coverage:
	mkdir -p .work/coverage
	uv run --locked coverage erase
	uv run --locked coverage run -m pytest -q
	uv run --locked coverage combine
	uv run --locked coverage report
	uv run --locked coverage json -o .work/coverage/coverage.json

clean:
	rm -rf build dist .work/tmp .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -not -path './.venv/*' -prune -exec rm -rf {} +
