Name:           secretserver-cli
Version:        1.0.0
Release:        1%{?dist}
Summary:        CLI for Sigaint Secret Server machine API
License:        MIT
URL:            https://github.com/example/secretserver-cli
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch
# RHEL 9+ / compatible
Requires:       python3 >= 3.9

%description
Command-line client for Sigaint Secret Server. Uses project machine tokens
(ss_…) against the /eso/v1 API to list, get, edit, and delete secrets.
Credentials from ~/.config/secretserver/config or SS_URL / SS_TOKEN / SS_PROJECT.

%prep
%setup -q

%build
# pure script, nothing to compile

%install
install -D -m 0755 secretserver %{buildroot}%{_bindir}/secretserver
install -D -m 0644 secretserver.1 %{buildroot}%{_mandir}/man1/secretserver.1

%files
%license LICENSE
%doc README.md
%{_bindir}/secretserver
%{_mandir}/man1/secretserver.1*

%changelog
* Sat Aug 08 2026 Secretserver CLI <cli@local> - 1.0.0-1
- Initial package
