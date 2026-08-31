%global pypi_name webify

Name:           webify
Version:        0.1.0
Release:        1%{?dist}
Summary:        Self-hosted Netlify alternative for Linux

License:        MIT
URL:            https://github.com/webify/webify
Source0:        webify-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  python3-setuptools
BuildRequires:  python3-wheel

Requires:       python3
Requires:       git

# Optional mode dependencies
Recommends:     nginx

%description
Webify is a self-hosted Netlify alternative for Linux. It deploys static
sites directly from Git repositories and serves them as systemd user services
behind python's built-in HTTP server, with modes for local, cloudflared, and
nginx-managed hosting.

%prep
%setup -q -n %{pypi_name}-%{version}

%build
%pyproject_wheel

%install
%pyproject_install

%check
%pyproject_check_import webify

%files
%license LICENSE
%{python3_sitelib}/%{pypi_name}/
%{python3_sitelib}/%{pypi_name}-%{version}.dist-info/
%{_bindir}/webify

%changelog
* %{date} Webify Contributors <webify@localhost> - 0.1.0-1
- Initial release
