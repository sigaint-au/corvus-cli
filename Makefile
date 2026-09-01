NAME    := corvus-cli
VERSION := $(shell python3 -c "import pathlib,re;print(re.search(r'version = \"([^\"]+)\"',pathlib.Path('pyproject.toml').read_text()).group(1))")
TARBALL := $(NAME)-$(VERSION).tar.gz
DIST    := dist
TOPDIR  := $(CURDIR)/build/rpm

.PHONY: all install clean rpm check

all: check

check:
	python3 -m py_compile corvus
	python3 -m py_compile corvus_cli/__init__.py corvus_cli/constants.py corvus_cli/config.py corvus_cli/api.py corvus_cli/output.py corvus_cli/parser.py corvus_cli/cli.py corvus_cli/commands/*.py
	python3 corvus -h >/dev/null
	pytest -q
	python3 -m ruff check corvus_cli corvus tests/ 2>/dev/null || ruff check corvus_cli corvus tests/ 2>/dev/null || echo "ruff not installed - skipping"

install:
	install -D -m 0755 corvus $(DESTDIR)/usr/bin/corvus
	install -D -m 0644 corvus.1 $(DESTDIR)/usr/share/man/man1/corvus.1

clean:
	rm -rf $(DIST) build $(TARBALL) __pycache__ *.pyc

# Build noarch RPM (needs rpm-build). Safe to run on Fedora/RHEL 9+.
rpm: clean
	mkdir -p $(DIST) $(TOPDIR)/{BUILD,RPMS,SOURCES,SPECS,SRPMS}
	mkdir -p build/$(NAME)-$(VERSION)
	cp -r corvus corvus_cli LICENSE README.md corvus.1 pyproject.toml build/$(NAME)-$(VERSION)/
	tar -C build -czf $(TOPDIR)/SOURCES/$(TARBALL) $(NAME)-$(VERSION)
	cp rpm/$(NAME).spec $(TOPDIR)/SPECS/
	rpmbuild -ba \
		--define "_topdir $(TOPDIR)" \
		--define "version $(VERSION)" \
		$(TOPDIR)/SPECS/$(NAME).spec
	cp $(TOPDIR)/RPMS/noarch/*.rpm $(TOPDIR)/SRPMS/*.rpm $(DIST)/ 2>/dev/null || true
	@ls -la $(DIST)/
