Name:           corvus-cli
Version:        %(echo %{version} 2>/dev/null || echo 1.0.0)
Release:        1%{?dist}
Summary:        CLI for Corvus machine API
License:        AGPL-3.0-or-later
URL:            https://git.sigaint.au/Sigaint/corvus-cli
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch
# RHEL 9+ / compatible
Requires:       python3 >= 3.9

%description
Command-line client for Corvus. Uses machine tokens (ss_…), personal access
tokens (pat_…), or short-lived CLI session tokens (sso_…) against the /eso/v1
and /api/v1/manage APIs to list, get, edit, and delete secrets.
Credentials from ~/.config/corvus/config or SS_URL / SS_TOKEN / SS_PROJECT.

%prep
%setup -q

%build
# pure Python package — nothing to compile

%install
install -D -m 0755 corvus %{buildroot}%{_bindir}/corvus
# also install as package for `import corvus_cli` when rpm is the install path
mkdir -p %{buildroot}%{python3_sitelib}/corvus_cli
cp -r corvus_cli/*.py %{buildroot}%{python3_sitelib}/corvus_cli/
mkdir -p %{buildroot}%{python3_sitelib}/corvus_cli/commands
cp -r corvus_cli/commands/*.py %{buildroot}%{python3_sitelib}/corvus_cli/commands/
install -D -m 0644 corvus.1 %{buildroot}%{_mandir}/man1/corvus.1

%files
%license LICENSE
%doc README.md
%{_bindir}/corvus
%{python3_sitelib}/corvus_cli/
%{_mandir}/man1/corvus.1*

%changelog
* Sat Aug 08 2026 Corvus CLI <cli@local> - 1.0.0-1
- Initial package
