# C128DCR boards: build, test and package.  See AGENTS.md for what each build checks.
PYTHON  ?= python3
VERSION ?= v1.0
BOARDS  := c128dcr/sram c128dcr/mmu-riser

.PHONY: help build-c128dcr rebuild-c128dcr test release-c128dcr clean-dist

help:
	@echo "make build-c128dcr     full route + checks for both C128DCR boards (minutes)"
	@echo "make rebuild-c128dcr   same, reusing route_cache.pkl (copper unchanged)"
	@echo "make test              shared-code regression tests"
	@echo "make release-c128dcr   package built outputs into dist/c128dcr-$(VERSION).zip"
	@echo "                       (VERSION=v1.1 to rename; does not build)"

build-c128dcr:
	for b in $(BOARDS); do (cd $$b && $(PYTHON) build.py) || exit 1; done

rebuild-c128dcr:
	for b in $(BOARDS); do (cd $$b && $(PYTHON) build.py --reuse) || exit 1; done

test:
	$(PYTHON) common/test_drc.py

release-c128dcr:
	$(PYTHON) tools/package_release.py --version $(VERSION)

clean-dist:
	rm -rf dist
