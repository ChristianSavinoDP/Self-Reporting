VENV   := .venv
PYTHON := $(VENV)/bin/python3
PIP    := $(VENV)/bin/pip

-include .env
export

.PHONY: install \
        report-2w report-monthly report-last-month report-yearly report-historic report-custom \
        collect-2w collect-monthly collect-last-month collect-yearly collect-historic collect-custom \
        analyze-2w analyze-monthly analyze-last-month analyze-yearly analyze-historic analyze-custom \
        html-2w html-monthly html-last-month html-yearly html-historic html-custom \
        clean uninstall help

help:
	@echo ""
	@echo "  Self-Reporting: Performance Self-Assessment"
	@echo "  ─────────────────────────────────────────────"
	@echo "  make install              Create venv and install dependencies"
	@echo ""
	@echo "  Full report (GitHub + Jira + Claude AI analysis + HTML):"
	@echo "  make report-2w            Last 2 weeks"
	@echo "  make report-monthly       Current month"
	@echo "  make report-last-month    Last complete month"
	@echo "  make report-yearly        Year to date"
	@echo "  make report-historic      All time"
	@echo "  make report-custom START=2026-01-01 END=2026-03-31"
	@echo ""
	@echo "  Data collection only (no AI analysis):"
	@echo "  make collect-2w | collect-monthly | collect-last-month"
	@echo "  make collect-yearly | collect-historic"
	@echo "  make collect-custom START=2026-01-01 END=2026-03-31"
	@echo ""
	@echo "  Re-run AI analysis (retry on existing data):"
	@echo "  make analyze-2w | analyze-monthly | analyze-last-month"
	@echo "  make analyze-yearly | analyze-historic"
	@echo "  make analyze-custom START=2026-01-01 END=2026-03-31"
	@echo ""
	@echo "  Render existing report to standalone HTML:"
	@echo "  make html-2w | html-monthly | html-last-month"
	@echo "  make html-yearly | html-historic"
	@echo "  make html-custom START=2026-01-01 END=2026-03-31"
	@echo ""
	@echo "  Language override (default: English):"
	@echo "  make report-2w REPORT_LANG=es"
	@echo "  make report-monthly REPORT_LANG=en"
	@echo ""
	@echo "  Token setup: cp .env.example .env  then edit .env"
	@echo ""

$(VENV):
	python3 -m venv $(VENV)

install: $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

# ── Language flag ─────────────────────────────────────────────────────────────
# Use REPORT_LANG, not LANG: LANG is the shell's locale env var (e.g. C.UTF-8)
# and make would inherit it, silently forcing a bogus --language value.

ifdef REPORT_LANG
LANG_FLAG := --language $(REPORT_LANG)
else
LANG_FLAG :=
endif

# ── Full report (GitHub + Jira + Claude AI + HTML) ───────────────────────────

report-2w: $(VENV)
	$(PYTHON) main.py report --period biweekly $(LANG_FLAG)

report-monthly: $(VENV)
	$(PYTHON) main.py report --period monthly $(LANG_FLAG)

report-last-month: $(VENV)
	$(PYTHON) main.py report --period last-month $(LANG_FLAG)

report-yearly: $(VENV)
	$(PYTHON) main.py report --period yearly $(LANG_FLAG)

report-historic: $(VENV)
	$(PYTHON) main.py report --period historic $(LANG_FLAG)

report-custom: $(VENV)
ifndef START
	$(error START is required. Usage: make report-custom START=2026-01-01 END=2026-03-31)
endif
ifndef END
	$(error END is required. Usage: make report-custom START=2026-01-01 END=2026-03-31)
endif
	$(PYTHON) main.py report --period custom --start-date $(START) --end-date $(END) $(LANG_FLAG)

# ── Data collection only ─────────────────────────────────────────────────────

collect-2w: $(VENV)
	$(PYTHON) main.py collect --period biweekly

collect-monthly: $(VENV)
	$(PYTHON) main.py collect --period monthly

collect-last-month: $(VENV)
	$(PYTHON) main.py collect --period last-month

collect-yearly: $(VENV)
	$(PYTHON) main.py collect --period yearly

collect-historic: $(VENV)
	$(PYTHON) main.py collect --period historic

collect-custom: $(VENV)
ifndef START
	$(error START is required. Usage: make collect-custom START=2026-01-01 END=2026-03-31)
endif
ifndef END
	$(error END is required. Usage: make collect-custom START=2026-01-01 END=2026-03-31)
endif
	$(PYTHON) main.py collect --period custom --start-date $(START) --end-date $(END)

# ── Re-run AI analysis (retry-friendly) ──────────────────────────────────────

analyze-2w: $(VENV)
	$(PYTHON) main.py analyze --period biweekly $(LANG_FLAG)

analyze-monthly: $(VENV)
	$(PYTHON) main.py analyze --period monthly $(LANG_FLAG)

analyze-last-month: $(VENV)
	$(PYTHON) main.py analyze --period last-month $(LANG_FLAG)

analyze-yearly: $(VENV)
	$(PYTHON) main.py analyze --period yearly $(LANG_FLAG)

analyze-historic: $(VENV)
	$(PYTHON) main.py analyze --period historic $(LANG_FLAG)

analyze-custom: $(VENV)
ifndef START
	$(error START is required. Usage: make analyze-custom START=2026-01-01 END=2026-03-31)
endif
ifndef END
	$(error END is required. Usage: make analyze-custom START=2026-01-01 END=2026-03-31)
endif
	$(PYTHON) main.py analyze --period custom --start-date $(START) --end-date $(END) $(LANG_FLAG)

# ── Render existing report to HTML ───────────────────────────────────────────

html-2w: $(VENV)
	$(PYTHON) main.py html --period biweekly

html-monthly: $(VENV)
	$(PYTHON) main.py html --period monthly

html-last-month: $(VENV)
	$(PYTHON) main.py html --period last-month

html-yearly: $(VENV)
	$(PYTHON) main.py html --period yearly

html-historic: $(VENV)
	$(PYTHON) main.py html --period historic

html-custom: $(VENV)
ifndef START
	$(error START is required. Usage: make html-custom START=2026-01-01 END=2026-03-31)
endif
ifndef END
	$(error END is required. Usage: make html-custom START=2026-01-01 END=2026-03-31)
endif
	$(PYTHON) main.py html --period custom --start-date $(START) --end-date $(END)

# ── Utilities ─────────────────────────────────────────────────────────────────

clean:
	rm -rf output/ logs/

uninstall:
	rm -rf $(VENV)
