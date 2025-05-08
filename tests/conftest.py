from devpi_common.url import URL
from pathlib import Path
import os
import pytest
import re
import subprocess
import sys
import textwrap


pytest_plugins = ["pytest_devpi_server", "test_devpi_server.plugin"]


phase_report_key = pytest.StashKey()


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    rep = yield
    item.stash.setdefault(phase_report_key, {})[rep.when] = rep
    return rep


@pytest.fixture(scope="class")
def adjust_nginx_conf_content(nginx_path):
    def adjust_nginx_conf_content(content):
        listen = re.search(r'listen \d+;', content).group(0)
        new_content = nginx_path.joinpath('nginx-devpi-lockdown.conf').read_text()
        new_content = new_content.replace('listen 80;', listen)
        return new_content
    return adjust_nginx_conf_content


@pytest.fixture
def xom(request, makexom):
    import devpi_lockdown.main
    import devpi_web.main
    xom = makexom(plugins=[
        (devpi_web.main, None),
        (devpi_lockdown.main, None)])
    from devpi_server.main import set_default_indexes
    with xom.keyfs.write_transaction():
        set_default_indexes(xom.model)
    return xom


@pytest.fixture(scope="class")
def nginx_path(request):
    try:
        server_path = request.getfixturevalue("server_path")
    except pytest.FixtureLookupError:
        server_path = Path(request.getfixturevalue("server_directory"))
    return server_path / "gen-config"


@pytest.fixture
def url_of_liveserver(request, nginx_path, nginx_host_port):
    (host, port) = nginx_host_port
    yield URL(f'http://{host}:{port}')
    key = request.node.stash[phase_report_key]
    if (result := key.get('call')) is not None and result.outcome == 'failed':
        if (access_log := nginx_path / 'nginx_access.log').exists():
            print(access_log.read_text())
        if (error_log := nginx_path / 'nginx_error.log').exists():
            print(error_log.read_text())


@pytest.fixture
def cmd_devpi(tmpdir, monkeypatch):
    """ execute devpi subcommand in-process (with fresh init) """
    from devpi.main import initmain

    def ask_confirm(msg):
        print("%s: yes" % msg)
        return True

    clientdir = tmpdir.join("client")

    def run_devpi(*args, **kwargs):
        callargs = []
        for arg in ["devpi", "--clientdir", clientdir] + list(args):
            if isinstance(arg, URL):
                arg = arg.url
            callargs.append(str(arg))
        print("*** inline$ %s" % " ".join(callargs))
        hub, method = initmain(callargs)
        monkeypatch.setattr(hub, "ask_confirm", ask_confirm)
        expected = kwargs.get("code", None)
        try:
            method(hub, hub.args)
        except SystemExit as sysex:
            hub.sysex = sysex
            if expected is None or expected < 0 or expected >= 400:
                # we expected an error or nothing, don't raise
                pass
            else:
                raise
        finally:
            hub.close()
        if expected is not None:
            if expected == -2:  # failed-to-start
                assert hasattr(hub, "sysex")
            elif isinstance(expected, list):
                assert hub._last_http_stati == expected
            else:
                if not isinstance(expected, tuple):
                    expected = (expected, )
                if hub._last_http_status not in expected:
                    pytest.fail(
                        "got http code %r, expected %r" % (
                            hub._last_http_status, expected))
        return hub

    run_devpi.clientdir = clientdir
    return run_devpi


@pytest.fixture
def devpi_username():
    attrname = '_count'
    count = getattr(devpi_username, attrname, 0)
    setattr(devpi_username, attrname, count + 1)
    return "user%d" % count


@pytest.fixture
def devpi(capfd, cmd_devpi, devpi_username, url_of_liveserver):
    cmd_devpi("use", url_of_liveserver.url, code=200)
    (out, err) = capfd.readouterr()
    cmd_devpi("login", "root", "--password", "", code=200)
    (out, err) = capfd.readouterr()
    cmd_devpi("index", "root/pypi", "mirror_no_project_list=true", "mirror_use_external_urls=true", code=200)
    (out, err) = capfd.readouterr()
    cmd_devpi("user", "-c", devpi_username, "password=123", "email=123", code=201)
    (out, err) = capfd.readouterr()
    cmd_devpi("login", devpi_username, "--password", "123", code=200)
    (out, err) = capfd.readouterr()
    cmd_devpi("index", "-c", "dev", "bases=root/pypi", code=200)
    (out, err) = capfd.readouterr()
    cmd_devpi("use", "dev", code=200)
    (out, err) = capfd.readouterr()
    cmd_devpi.user = devpi_username
    return cmd_devpi


def _path_parts(path):
    parts = []
    while path:
        folder, name = os.path.split(path)
        if folder == path:  # root folder
            folder, name = name, folder
        if name:
            parts.append(name)
        path = folder
    parts.reverse()
    return parts


def _filedefs_contains(base, filedefs, path):
    """
    whether `filedefs` defines a file/folder with the given `path`

    `path`, if relative, will be interpreted relative to the `base` folder, and
    whether relative or not, must refer to either the `base` folder or one of
    its direct or indirect children. The base folder itself is considered
    created if the filedefs structure is not empty.

    """
    unknown = object()
    base = Path(base)
    path = base / path

    path_rel_parts = _path_parts(path.relative_to(base))
    for part in path_rel_parts:
        if not isinstance(filedefs, dict):
            return False
        filedefs = filedefs.get(part, unknown)
        if filedefs is unknown:
            return False
    return path_rel_parts or path == base and filedefs


def create_files(base, filedefs):
    for key, value in filedefs.items():
        if isinstance(value, dict):
            create_files(base.ensure(key, dir=1), value)
        elif isinstance(value, str):
            s = textwrap.dedent(value)
            base.join(key).write(s)


@pytest.fixture
def initproj(tmpdir):
    """Create a factory function for creating example projects.

    Constructed folder/file hierarchy examples:

    with `src_root` other than `.`:

      tmpdir/
          name/                  # base
            src_root/            # src_root
                name/            # package_dir
                    __init__.py
                name.egg-info/   # created later on package build
            setup.py

    with `src_root` given as `.`:

      tmpdir/
          name/                  # base, src_root
            name/                # package_dir
                __init__.py
            name.egg-info/       # created later on package build
            setup.py
    """

    def initproj_(nameversion, filedefs=None, src_root=".", kind="setup.py"):
        if filedefs is None:
            filedefs = {}
        if not src_root:
            src_root = "."
        if isinstance(nameversion, str):
            parts = nameversion.split("-")
            if len(parts) == 1:
                parts.append("0.1")
            name, version = parts
        else:
            name, version = nameversion
        base = tmpdir.join(name)
        src_root_path = base / src_root
        assert base == src_root_path or src_root_path.relto(
            base
        ), "`src_root` must be the constructed project folder or its direct or indirect subfolder"

        base.ensure(dir=1)
        create_files(base, filedefs)
        if not _filedefs_contains(base, filedefs, "setup.py") and kind == "setup.py":
            create_files(
                base,
                {
                    "setup.py": """
                from setuptools import setup, find_packages
                setup(
                    name='{name}',
                    description='{name} project',
                    version='{version}',
                    license='MIT',
                    platforms=['unix', 'win32'],
                    packages=find_packages('{src_root}'),
                    package_dir={{'':'{src_root}'}},
                )
            """.format(
                        **locals()
                    )
                },
            )
        if not _filedefs_contains(base, filedefs, "pyproject.toml") and kind == "setup.cfg":
            create_files(base, {"pyproject.toml": """
                    [build-system]
                    requires = ["setuptools", "wheel"]
                """})
        if not _filedefs_contains(base, filedefs, "setup.cfg") and kind == "setup.cfg":
            create_files(base, {"setup.cfg": """
                    [metadata]
                    name = {name}
                    description= {name} project
                    version = {version}
                    license = MIT
                    packages = find:
                """.format(**locals())})
        if not _filedefs_contains(base, filedefs, "pyproject.toml") and kind == "pyproject.toml":
            create_files(base, {"pyproject.toml": """
                    [build-system]
                    requires = ["flit_core >=3.2"]
                    build-backend = "flit_core.buildapi"

                    [project]
                    name = "{name}"
                    description= "{name} project"
                    version = "{version}"
                    license = {{text="MIT"}}
                    packages = "find:"
                """.format(**locals())})
        if not _filedefs_contains(base, filedefs, src_root_path.join(name)):
            create_files(
                src_root_path, {name: {"__init__.py": "__version__ = {!r}".format(version)}}
            )
        manifestlines = [
            "include {}".format(p.relto(base)) for p in base.visit(lambda x: x.check(file=1))
        ]
        create_files(base, {"MANIFEST.in": "\n".join(manifestlines)})
        print("created project in {}".format(base))
        base.chdir()
        return base

    return initproj_


def _check_output(request, args, env=None):
    result = subprocess.run(
        args,  # noqa: S603 only used for tests
        check=False,
        env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if not result.returncode:
        print(result.stdout.decode())  # noqa: T201 only used for tests
    else:
        capman = request.config.pluginmanager.getplugin("capturemanager")
        capman.suspend()
        print(result.stdout.decode())  # noqa: T201 only used for tests
        capman.resume()
    result.check_returncode()
    return result


def check_call(request, args, env=None):
    _check_output(request, args, env=env)


@pytest.fixture
def create_venv(request, tmpdir_factory, monkeypatch):
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    venvdir = tmpdir_factory.mktemp("venv")
    venvinstalldir = tmpdir_factory.mktemp("inst")

    def do_create_venv():
        # we need to change directory, otherwise the path will become
        # too long on windows
        venvinstalldir.ensure_dir()
        os.chdir(venvinstalldir)
        check_call(request, [
            "virtualenv", "--never-download", str(venvdir)])
        # activate
        if sys.platform == "win32":
            bindir = "Scripts"
        else:
            bindir = "bin"
        monkeypatch.setenv("PATH", bindir + os.pathsep + os.environ["PATH"])
        return venvdir

    return do_create_venv
