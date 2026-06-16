VENV   := .venv
PYTHON := $(VENV)/bin/python3
PIP    := $(VENV)/bin/pip

-include .env
export

.PHONY: install report-2w report-monthly report-last-month \
        collect-2w collect-monthly collect-last-month \
        analyze-2w analyze-monthly analyze-last-month \
        clean uninstall help

help:
	@echo ""
	@echo "  Self-Reporting — Performance Self-Assessment"
	@echo "  ─────────────────────────────────────────────"
	@echo "  make install              Create venv and install dependencies"
	@echo ""
	@echo "  Full report (GitHub + Jira + Claude AI analysis):"
	@echo "  make report-2w            Last 2 weeks"
	@echo "  make report-monthly       Current month"
	@echo "  make report-last-month    Last complete month"
	@echo ""
	@echo "  Data collection only (no AI analysis):"
	@echo "  make collect-2w"
	@echo "  make collect-monthly"
	@echo "  make collect-last-month"
	@echo ""
	@echo "  Re-run AI analysis (retry on existing data):"
	@echo "  make analyze-2w"
	@echo "  make analyze-monthly"
	@echo "  make analyze-last-month"
	@echo ""
	@echo "  Language override (default: English):"
	@echo "  make report-2w LANG=es"
	@echo "  make report-monthly LANG=en"
	@echo ""
	@echo "  Token setup: cp .env.example .env  then edit .env"
	@echo ""

$(VENV):
	python3 -m venv $(VENV)

install: $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

# ── Language flag ─────────────────────────────────────────────────────────────

ifdef LANG
LANG_FLAG := --language $(LANG)
else
LANG_FLAG :=
endif

# ── Full report (GitHub + Jira + Claude AI) ──────────────────────────────────

report-2w: $(VENV)
	$(PYTHON) main.py report --period biweekly $(LANG_FLAG)

report-monthly: $(VENV)
	$(PYTHON) main.py report --period monthly $(LANG_FLAG)

report-last-month: $(VENV)
	$(PYTHON) main.py report --period last-month $(LANG_FLAG)

# ── Data collection only ─────────────────────────────────────────────────────

collect-2w: $(VENV)
	$(PYTHON) main.py collect --period biweekly

collect-monthly: $(VENV)
	$(PYTHON) main.py collect --period monthly

collect-last-month: $(VENV)
	$(PYTHON) main.py collect --period last-month

# ── Re-run AI analysis (retry-friendly) ──────────────────────────────────────

analyze-2w: $(VENV)
	$(PYTHON) main.py analyze --period biweekly $(LANG_FLAG)

analyze-monthly: $(VENV)
	$(PYTHON) main.py analyze --period monthly $(LANG_FLAG)

analyze-last-month: $(VENV)
	$(PYTHON) main.py analyze --period last-month $(LANG_FLAG)

# ── Utilities ─────────────────────────────────────────────────────────────────

clean:
	rm -rf output/ logs/

uninstall:
	rm -rf $(VENV)
