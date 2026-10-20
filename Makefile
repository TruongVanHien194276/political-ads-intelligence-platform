.PHONY: check fmt test week4 week5 week6 week9 week10 week11 week12

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

week12:
	data/tmp/week11/venv/bin/python scripts/week12_benchmark_baselines.py --model naive
	data/tmp/week11/venv/bin/python scripts/week12_benchmark_baselines.py --model lightgbm
	OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 VECLIB_MAXIMUM_THREADS=2 /usr/bin/time -l data/tmp/week12/venv/bin/python scripts/week12_tft.py 2> data/tmp/week12/tft_time.txt
	OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 VECLIB_MAXIMUM_THREADS=2 HF_HOME="$(CURDIR)/data/tmp/week12/hf-cache" /usr/bin/time -l data/tmp/week12/venv/bin/python scripts/week12_chronos.py 2> data/tmp/week12/chronos_time.txt
	data/tmp/week12/venv/bin/python scripts/week12_evaluate.py
	data/tmp/week12/venv/bin/python scripts/week12_charts.py
	data/tmp/week12/venv/bin/python scripts/week12_evidence.py
