.PHONY: check fmt test

check:
	python -m ruff check .
	python -m ruff format --check .
	python -m pytest -q

fmt:
	python -m ruff check --fix .
	python -m ruff format .

test:
	python -m pytest -q