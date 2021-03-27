from contextlib import closing
from devpi_common.url import URL
from test_devpi_server.conftest import gentmp  # noqa
from test_devpi_server.conftest import httpget  # noqa
from test_devpi_server.conftest import makemapp  # noqa
from test_devpi_server.conftest import maketestapp  # noqa
from test_devpi_server.conftest import makexom  # noqa
from test_devpi_server.conftest import mapp  # noqa
from test_devpi_server.conftest import pypiurls  # noqa
from test_devpi_server.conftest import storage_info  # noqa
from test_devpi_server.conftest import testapp  # noqa
from time import sleep
import os
import py
import pytest
import requests
import socket
import subprocess
import sys
import textwrap


(makexom,)  # shut up pyflakes


@pytest.fixture
def xom(request, makexom):
    import devpi_lockdown.main
    import devpi_web.main
    xom = makexom(plugins=[
        (devpi_web.main, None),
        (devpi_lockdown.main, None)])
    from devpi_server.main import set_default_indexes
    with xom.keyfs.transaction(write=True):
        set_default_indexes(xom.model)
    return xom


def get_open_port(host):
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind((host, 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


def wait_for_port(host, port, timeout=60):
    while timeout > 0:
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            s.settimeout(1)
            if s.connect_ex((host, port)) == 0:
                return timeout
        sleep(1)
        timeout -= 1
    raise RuntimeError(
        "The port %s on host %s didn't become accessible" % (port, host))


def wait_for_server_api(host, port, timeout=60):
    timeout = wait_for_port(host, port, timeout=timeout)
    while timeout > 0:
        try:
            r = requests.get("http://%s:%s/+api" % (host, port), timeout=1)
        except requests.exceptions.ConnectionError:
            pass
        else:
            if r.status_code == 200:
                return
        sleep(1)
        timeout -= 1
    raise RuntimeError(
        "The api on port %s, host %s didn't become accessible" % (port, host))


@pytest.fixture(scope="session")
def server_directory():
    import tempfile
    srvdir = py.path.local(
        tempfile.mkdtemp(prefix='test-', suffix='-server-directory'))
    yield srvdir
    srvdir.remove(ignore_errors=True)


def _liveserver(host, port, serverdir):
    path = py.path.local.sysfind("devpi-server")
    assert path
    init_path = py.path.local.sysfind("devpi-init")
    assert init_path
    args = [
        "--serverdir", str(serverdir)]
    try:
        subprocess.check_call(
            [str(init_path)] + args + ['--no-root-pypi'])
    except subprocess.CalledProcessError as e:
        # this won't output anything on Windows
        print(
            getattr(e, 'output', "Can't get process output on Windows"),
            file=sys.stderr)
        raise
    p = subprocess.Popen(
        [str(path)] + args + ["--debug", "--host", host, "--port", str(port)])
    wait_for_server_api(host, port)
    return (p, URL("http://%s:%s" % (host, port)))


@pytest.fixture(scope="session")
def server_host_port(server_directory):
    host = 'localhost'
    port = get_open_port(host)
    (p, url) = _liveserver(host, port, server_directory)
    try:
        yield (host, port)
    finally:
        p.terminate()
        p.wait()


nginx_conf_content = """
worker_processes  1;
daemon off;
pid nginx.pid;
error_log nginx_error.log;

events {
    worker_connections  32;
}

http {
    access_log nginx_access.log combined;
    default_type  application/octet-stream;
    sendfile        on;
    keepalive_timeout 0;
    include nginx-devpi-lockdown.conf;
}
"""


def _livenginx(host, port, serverdir, server_host_port):
    from devpi_lockdown.main import _inject_lockdown_config
    nginx = py.path.local.sysfind("nginx")
    if nginx is None:
        pytest.skip("No nginx executable found.")
    gen_config_path = py.path.local.sysfind("devpi-gen-config")
    assert gen_config_path
    (server_host, server_port) = server_host_port
    with serverdir.as_cwd():
        try:
            subprocess.check_call(
                [str(gen_config_path), "--host", server_host, "--port", str(server_port)])
        except subprocess.CalledProcessError as e:
            # this won't output anything on Windows
            print(
                getattr(e, 'output', "Can't get process output on Windows"),
                file=sys.stderr)
            raise
    nginx_directory = serverdir.join("gen-config")
    nginx_devpi_conf = nginx_directory.join("nginx-devpi-lockdown.conf")
    if nginx_devpi_conf.check():
        nginx_devpi_conf_content = nginx_devpi_conf.read()
    else:
        nginx_lines = nginx_directory.join("nginx-devpi.conf").read().splitlines()
        _inject_lockdown_config(nginx_lines)
        nginx_devpi_conf_content = "\n".join(nginx_lines)
    nginx_devpi_conf_content = nginx_devpi_conf_content.replace(
        "listen 80;",
        "listen %s;" % port)
    nginx_devpi_conf.write(nginx_devpi_conf_content)
    nginx_conf = nginx_directory.join("nginx.conf")
    nginx_conf.write(nginx_conf_content)
    try:
        subprocess.check_output([
            str(nginx), "-t",
            "-c", nginx_conf.strpath,
            "-p", nginx_directory.strpath], stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        # this won't output anything on Windows
        print(
            getattr(e, 'output', "Can't get process output on Windows"),
            file=sys.stderr)
        raise
    p = subprocess.Popen([
        str(nginx), "-c", nginx_conf.strpath, "-p", nginx_directory.strpath])
    wait_for_port(host, port)
    return (p, URL("http://%s:%s" % (host, port)))


@pytest.mark.skipif(
    "sys.platform.startswith('win')", reason="no nginx on windows")
@pytest.fixture(scope="session")
def url_of_liveserver(request, server_directory, server_host_port):
    host = 'localhost'
    port = get_open_port(host)
    (p, url) = _livenginx(host, port, server_directory, server_host_port)
    try:
        yield url
    finally:
        p.terminate()
        p.wait()


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
    cmd_devpi("user", "-c", devpi_username, "password=123", "email=123", code=201)
    (out, err) = capfd.readouterr()
    cmd_devpi("login", devpi_username, "--password", "123", code=200)
    (out, err) = capfd.readouterr()
    cmd_devpi("index", "-c", "dev", code=200)
    (out, err) = capfd.readouterr()
    cmd_devpi("use", "dev", code=200)
    (out, err) = capfd.readouterr()
    cmd_devpi.user = devpi_username
    return cmd_devpi


def _path_parts(path):
    path = path and str(path)  # py.path.local support
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


def _path_join(base, *args):
    # workaround for a py.path.local bug on Windows (`path.join('/x', abs=1)`
    # should be py.path.local('X:\\x') where `X` is the current drive, when in
    # fact it comes out as py.path.local('\\x'))
    return py.path.local(base.join(*args, abs=1))


def _filedefs_contains(base, filedefs, path):
    """
    whether `filedefs` defines a file/folder with the given `path`

    `path`, if relative, will be interpreted relative to the `base` folder, and
    whether relative or not, must refer to either the `base` folder or one of
    its direct or indirect children. The base folder itself is considered
    created if the filedefs structure is not empty.

    """
    unknown = object()
    base = py.path.local(base)
    path = _path_join(base, path)

    path_rel_parts = _path_parts(path.relto(base))
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
        elif isinstance(value, py.builtin._basestring):
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

    def initproj_(nameversion, filedefs=None, src_root="."):
        if filedefs is None:
            filedefs = {}
        if not src_root:
            src_root = "."
        if isinstance(nameversion, py.builtin._basestring):
            parts = nameversion.split(str("-"))
            if len(parts) == 1:
                parts.append("0.1")
            name, version = parts
        else:
            name, version = nameversion
        base = tmpdir.join(name)
        src_root_path = _path_join(base, src_root)
        assert base == src_root_path or src_root_path.relto(
            base
        ), "`src_root` must be the constructed project folder or its direct or indirect subfolder"

        base.ensure(dir=1)
        create_files(base, filedefs)
        if not _filedefs_contains(base, filedefs, "setup.py"):
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


@pytest.fixture
def create_venv(request, tmpdir_factory, monkeypatch):
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    venvdir = tmpdir_factory.mktemp("venv")
    venvinstalldir = tmpdir_factory.mktemp("inst")

    def do_create_venv():
        # we need to change directory, otherwise the path will become
        # too long on windows
        venvinstalldir.ensure_dir()
        with venvinstalldir.as_cwd():
            subprocess.check_call([
                "virtualenv", "--never-download", venvdir.strpath])
        # activate
        if sys.platform == "win32":
            bindir = venvdir.join("Scripts")
        else:
            bindir = venvdir.join("bin")
        monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
        return venvdir

    return do_create_venv
