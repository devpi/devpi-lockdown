from devpi_server import __version__ as devpi_server_version
from pkg_resources import parse_version
from webob.headers import ResponseHeaders
import pytest


devpi_server_version = parse_version(devpi_server_version)


def test_importable():
    import devpi_lockdown
    assert devpi_lockdown.__version__


def test_login(mapp, testapp):
    mapp.create_user("user1", "1")
    testapp.xget(401, 'http://localhost/+authcheck')
    testapp.xget(200, 'http://localhost/+login')
    r = testapp.post(
        'http://localhost/+login',
        dict(username="user1", password="1", submit=""))
    assert r.status_code == 302
    assert r.location == 'http://localhost/'
    testapp.xget(200, 'http://localhost/+authcheck')


@pytest.mark.skipif(
    devpi_server_version < parse_version("6dev"),
    reason="Needs authcheck support")
def test_always_ok(testapp):
    testapp.xget(
        200, 'http://localhost/+authcheck',
        headers=ResponseHeaders({
            'X-Original-URI': 'http://localhost/+login'}))
    r = testapp.xget(200, 'http://localhost/+login')
    for elem in r.html.select('link, script'):
        uri = None
        if elem.name.lower() == 'link':
            uri = elem.attrs.get('href')
        elif elem.name.lower() == 'script':
            uri = elem.attrs.get('src')
        if not uri:
            continue
        testapp.xget(
            200, 'http://localhost/+authcheck',
            headers=ResponseHeaders({
                'X-Original-URI': uri}))
