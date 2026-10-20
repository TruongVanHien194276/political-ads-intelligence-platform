.PHONY: check fmt test week4 week5 week6 week9 week10 week11

check:
	python -m ruff check .
	python -m ruff format --check .
	python -m pytest -q

fmt:
	python -m ruff check --fix .
	python -m ruff format .

test:
	python -m pytest -q

week4:
	data/tmp/week4/venv/bin/python scripts/week4_load.py
	WEEK4_DB_PATH="$(CURDIR)/data/tmp/week4/warehouse.duckdb" data/tmp/week4/venv/bin/dbt build --project-dir transform --profiles-dir transform --exclude path:models/gold
	data/tmp/week4/venv/bin/python scripts/week4_promote.py

week5: week4
	WEEK4_DB_PATH="$(CURDIR)/data/tmp/week4/warehouse.duckdb" data/tmp/week4/venv/bin/dbt build --project-dir transform --profiles-dir transform --select tag:gold

week6:
	.venv/bin/python scripts/week6_publish.py

week9:
	.venv/bin/python scripts/week9_analysis.py

week10:
	.venv/bin/python scripts/week10_export_powerbi.py
	.venv/bin/python scripts/week10_verify_snapshot.py

week11:
	data/tmp/week11/venv/bin/python scripts/week11_train.py
	data/tmp/week11/venv/bin/python scripts/week11_evidence.py
