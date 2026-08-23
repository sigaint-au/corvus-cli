NAME    := corvus-cli
VERSION := 1.0.0
TARBALL := $(NAME)-$(VERSION).tar.gz
DIST    := dist
TOPDIR  := $(CURDIR)/build/rpm

.PHONY: all install clean rpm check

all: check

check:
	python3 -m py_compile corvus
	python3 corvus -h >/dev/null
	python3 -m pytest
	python3 -m ruff check corvus tests/

install:
	install -D -m 0755 corvus $(DESTDIR)/usr/bin/corvus
	install -D -m 0644 corvus.1 $(DESTDIR)/usr/share/man/man1/corvus.1

clean:
	rm -rf $(DIST) build $(TARBALL) __pycache__ *.pyc

# Build noarch RPM (needs rpm-build). Safe to run on Fedora/RHEL 9+.
rpm: clean
	mkdir -p $(DIST) $(TOPDIR)/{BUILD,RPMS,SOURCES,SPECS,SRPMS}
	mkdir -p build/$(NAME)-$(VERSION)
	cp corvus LICENSE README.md corvus.1 build/$(NAME)-$(VERSION)/
	tar -C build -czf $(TOPDIR)/SOURCES/$(TARBALL) $(NAME)-$(VERSION)
	cp rpm/$(NAME).spec $(TOPDIR)/SPECS/
	rpmbuild -ba \
		--define "_topdir $(TOPDIR)" \
		--define "dist .el9" \
		$(TOPDIR)/SPECS/$(NAME).spec
	cp $(TOPDIR)/RPMS/noarch/*.rpm $(TOPDIR)/SRPMS/*.rpm $(DIST)/
	@ls -la $(DIST)/
