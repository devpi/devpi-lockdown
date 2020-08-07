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
import py
import pytest
import requests
import socket
import subprocess
import sys


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


@pytest.yield_fixture(scope="session")
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
    access_log off;
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
@pytest.yield_fixture(scope="session")
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
