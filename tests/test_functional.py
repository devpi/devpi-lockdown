from devpi import __version__ as devpi_client_version
from pkg_resources import parse_version
import pytest


devpi_client_version = parse_version(devpi_client_version)
is_atleast_client6 = devpi_client_version >= parse_version("6dev")


def test_index_when_unauthorized(capfd, devpi):
    devpi("logout")
    (out, err) = capfd.readouterr()
    if is_atleast_client6:
        devpi("index")
        (out, err) = capfd.readouterr()
        assert "you need to be logged" in out
    else:
        devpi("index", code=403)


@pytest.mark.skipif(
    not is_atleast_client6,
    reason="Needs authentication passing to pip")
def test_devpi_install(capfd, create_venv, devpi, initproj, monkeypatch):
    pkg = initproj("foo-1.0")
    with pkg.as_cwd():
        devpi("upload", code=200)
    (out, err) = capfd.readouterr()
    assert "file_upload of foo" in out
    venvdir = create_venv()
    monkeypatch.setenv("VIRTUAL_ENV", venvdir.strpath)
    devpi("install", "foo")
    (out, err) = capfd.readouterr()
    assert "Successfully installed foo" in out


@pytest.mark.skipif(
    not is_atleast_client6,
    reason="Needs authentication passing to pip")
def test_devpi_test(capfd, create_venv, devpi, initproj, monkeypatch):
    foo = initproj(
        "foo-1.0",
        filedefs={
            "tox.ini": """
              [testenv]
              commands = python -c "print('ok')"
              deps = bar==1.0
            """})
    with foo.as_cwd():
        devpi("upload", code=200)
    bar = initproj("bar-1.0")
    with bar.as_cwd():
        devpi("upload", code=200)
    (out, err) = capfd.readouterr()
    assert "file_upload of foo" in out
    venvdir = create_venv()
    monkeypatch.setenv("VIRTUAL_ENV", venvdir.strpath)
    devpi("test", "foo")
    (out, err) = capfd.readouterr()
    assert "installdeps: bar==1.0" in out
    assert "commands succeeded" in out
    assert "successfully posted tox result data" in out
