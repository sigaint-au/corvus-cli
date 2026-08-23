Name:           corvus-cli
Version:        1.0.0
Release:        1%{?dist}
Summary:        CLI for Corvus machine API
License:        AGPL-3.0-or-later
URL:            https://git.sigaint.au/Sigaint/corvus-cli
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch
# RHEL 9+ / compatible
Requires:       python3 >= 3.9

%description
Command-line client for Corvus. Uses project machine tokens
(ss_…) against the /eso/v1 API to list, get, edit, and delete secrets.
Credentials from ~/.config/corvus/config or SS_URL / SS_TOKEN / SS_PROJECT.

%prep
%setup -q

%build
# pure script, nothing to compile

%install
install -D -m 0755 corvus %{buildroot}%{_bindir}/corvus
install -D -m 0644 corvus.1 %{buildroot}%{_mandir}/man1/corvus.1

%files
%license LICENSE
%doc README.md
%{_bindir}/corvus
%{_mandir}/man1/corvus.1*

%changelog
* Sat Aug 08 2026 Corvus CLI <cli@local> - 1.0.0-1
- Initial package
