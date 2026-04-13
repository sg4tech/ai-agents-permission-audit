.PHONY: init check-init lint typecheck test verify build-check security bandit complexity pylint-check coverage

PYTHON3 ?= python3

init:
	git pull origin master || true
	$(PYTHON3) -m venv .venv
	.venv/bin/pip install -U pip setuptools wheel -q
	.venv/bin/pip install -e ".[dev]"

check-init:
	@test -d .venv || $(MAKE) init

lint: check-init
	./.venv/bin/ruff check .

typecheck: check-init
	./.venv/bin/mypy src/

test: check-init
	./.venv/bin/python -m pytest tests/

build-check: check-init
	./.venv/bin/pip install --no-deps -e . -q

bandit: check-init
	./.venv/bin/bandit -r src/ -q --skip B404,B607,B603

complexity: check-init
	@echo "=== Cyclomatic complexity report (rank C and above) ==="
	@.venv/bin/radon cc src/ -n C -s || true
	@echo "=== Maintainability index (rank C — hard to maintain) ==="
	@.venv/bin/radon mi src/ -n C -s || true

pylint-check: check-init
	@echo "=== Pylint tier 1: hard-fail rules ==="
	./.venv/bin/pylint src/ --disable=all --enable=E0401,E0602,E1101,E1120,W0102,W0611,W0612,W0718,W1203,R0401,C0302
	@echo "=== Pylint tier 2: complexity warnings (advisory) ==="
	@.venv/bin/pylint src/ --disable=all --enable=R0912,R0913,R0914,R0915,R0902,W0401,C0411 || true

verify: lint typecheck test build-check bandit complexity pylint-check

coverage: check-init
	./.venv/bin/coverage erase
	./.venv/bin/coverage run -m pytest tests/
	./.venv/bin/coverage report -m
