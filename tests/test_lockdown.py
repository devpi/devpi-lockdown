from devpi_server import __version__ as devpi_server_version
from pkg_resources import parse_version
from webob.headers import ResponseHeaders
import pytest
import subprocess


devpi_server_version = parse_version(devpi_server_version)


def test_importable():
    import devpi_lockdown
    assert devpi_lockdown.__version__


def test_login(mapp, testapp):
    mapp.create_user("user1", "1")
    testapp.xget(401, 'http://localhost/+authcheck')
    testapp.xget(200, 'http://localhost/+login')
    r = testapp.post(
        'http://localhost/+login?goto_url=/foo/bar',
        dict(username="user1", password="1", submit=""))
    assert r.status_code == 302
    assert r.location == 'http://localhost/foo/bar'
    testapp.xget(200, 'http://localhost/+authcheck')


def test_goto_url_with_plus(mapp, testapp):
    mapp.create_user("user1", "1")
    r = testapp.post(
        'http://localhost/+login?goto_url=http://localhost/+status',
        dict(username="user1", password="1", submit=""))
    assert r.status_code == 302
    assert r.location == 'http://localhost/+status'


def test_login_bad_goto_url(mapp, testapp):
    mapp.create_user("user1", "1")
    r = testapp.post(
        'http://localhost/+login?goto_url=https://github.com',
        dict(username="user1", password="1", submit=""))
    assert r.status_code == 302
    assert r.location == 'http://localhost/'


def test_login_differing_goto_url_scheme(mapp, testapp):
    mapp.create_user("user1", "1")
    r = testapp.post(
        'http://localhost/+login?goto_url=https://localhost/foo',
        dict(username="user1", password="1", submit=""))
    assert r.status_code == 302
    assert r.location == 'http://localhost/'


def test_login_invalid_credentials(mapp, testapp):
    mapp.create_user("user1", "1")
    r = testapp.post(
        'http://localhost/+login',
        dict(username="user1", password="wrong", submit=""),
        code=401)
    assert 'Invalid credentials' in r.text
    testapp.xget(401, 'http://localhost/+authcheck')


def test_always_ok(testapp):
    testapp.xget(
        200, 'http://localhost/+authcheck',
        headers=ResponseHeaders({
            'X-Original-URI': 'http://localhost/+api'}))
    testapp.xget(
        200, 'http://localhost/+authcheck',
        headers=ResponseHeaders({
            'X-Original-URI': 'http://localhost/+login',
            'Accept': 'application/json'}))
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


def test_get_current_request(maketestapp, makexom):
    from devpi_lockdown import main as lockdown_plugin
    from devpi_lockdown.main import devpiserver_hookimpl
    from pyramid.authentication import b64encode
    from pyramid.threadlocal import get_current_request
    from webob.headers import ResponseHeaders

    calls = []

    class Plugin:
        @devpiserver_hookimpl
        def devpiserver_get_credentials(self, request):
            calls.append(request)
            current_request = get_current_request()
            assert request is current_request

    plugin = Plugin()
    xom = makexom(plugins=[lockdown_plugin, plugin])
    testapp = maketestapp(xom)
    basic_auth = '%s:%s' % ('user1', '1')
    testapp.xget(
        401, 'http://localhost/+authcheck',
        headers=ResponseHeaders({
            'Authorization': 'MyBasic %s' % b64encode(basic_auth).decode('ascii'),
            'X-Original-URI': 'http://localhost/foo/bar/+simple/pkg'}))
    assert calls


@pytest.mark.skipif(
    devpi_server_version < parse_version("6dev"),
    reason="Needs devpiserver_genconfig hook")
def test_gen_config(tmpdir):
    import re

    tmpdir.chdir()
    proc = subprocess.Popen(["devpi-gen-config"])
    res = proc.wait()
    assert res == 0
    path = tmpdir.join("gen-config").join("nginx-devpi-lockdown.conf")
    assert path.check()
    lines = path.read().splitlines()

    def find_line(content):
        regexp = re.compile(content, re.I)
        for index, line in enumerate(lines):
            if regexp.search(line):
                return (index, line)

    (server_index, server_line) = find_line("server_name")
    (auth_index, auth_line) = find_line("auth_request")
    (proxy_index, proxy_line) = find_line("location\\s+@proxy_to_app")
    assert "/+authcheck" in auth_line
    assert server_index < auth_index < proxy_index


@pytest.mark.skipif(
    devpi_server_version < parse_version("6dev"),
    reason="Needs devpiserver_authcheck_* hooks")
def test_forbidden_plugin(makemapp, maketestapp, makexom):
    from devpi_lockdown.main import devpiserver_hookimpl
    from devpi_server.model import ACLList
    from webob.headers import ResponseHeaders

    class Plugin:
        @devpiserver_hookimpl
        def devpiserver_indexconfig_defaults(self, index_type):
            return {"acl_pkg_read": ACLList([':ANONYMOUS:'])}

        @devpiserver_hookimpl
        def devpiserver_stage_get_principals_for_pkg_read(self, ixconfig):
            return ixconfig.get('acl_pkg_read', None)

        @devpiserver_hookimpl
        def devpiserver_authcheck_forbidden(self, request):
            if request.authenticated_userid:
                stage = request.context._stage
                if stage and not request.has_permission('pkg_read'):
                    return True

    plugin = Plugin()
    xom = makexom(plugins=[plugin])
    testapp = maketestapp(xom)
    mapp = makemapp(testapp)
    api1 = mapp.create_and_use("someuser/dev", indexconfig=dict(
        acl_pkg_read="someuser"))
    mapp.upload_file_pypi("hello-1.0.tar.gz", b'content', "hello", "1.0")
    (path,) = mapp.get_release_paths("hello")
    # current user should be able to read package and index
    testapp.xget(200, api1.index)
    testapp.xget(
        200, '/+authcheck',
        headers=ResponseHeaders({'X-Original-URI': api1.index}))
    testapp.xget(200, path)
    testapp.xget(
        200, '/+authcheck',
        headers=ResponseHeaders({'X-Original-URI': 'http://localhost' + path}))
    # create another user
    mapp.create_and_use("otheruser/dev")
    # the user should be able to access the index
    testapp.xget(200, api1.index)
    # but the authcheck will fail, so through nginx it will be blocked
    testapp.xget(
        403, '/+authcheck',
        headers=ResponseHeaders({'X-Original-URI': api1.index}))
    # the package should be forbidden
    testapp.xget(403, path)
    testapp.xget(
        403, '/+authcheck',
        headers=ResponseHeaders({'X-Original-URI': 'http://localhost' + path}))


@pytest.mark.skipif(
    devpi_server_version < parse_version("6dev"),
    reason="Needs devpiserver_authcheck_* hooks")
def test_inherited_forbidden_plugin(makemapp, maketestapp, makexom):
    from devpi_lockdown.main import devpiserver_hookimpl
    from devpi_server.model import ACLList
    from webob.headers import ResponseHeaders

    class Plugin:
        @devpiserver_hookimpl
        def devpiserver_indexconfig_defaults(self, index_type):
            return {"acl_pkg_read": ACLList([':ANONYMOUS:'])}

        @devpiserver_hookimpl
        def devpiserver_stage_get_principals_for_pkg_read(self, ixconfig):
            return ixconfig.get('acl_pkg_read', None)

        @devpiserver_hookimpl
        def devpiserver_authcheck_forbidden(self, request):
            if not request.authenticated_userid:
                return
            stage = request.context._stage
            if not stage:
                return
            for _stage in stage.sro():
                if not request.has_permission('pkg_read', _stage):
                    return True

    plugin = Plugin()
    xom = makexom(plugins=[plugin])
    testapp = maketestapp(xom)
    mapp = makemapp(testapp)
    api1 = mapp.create_and_use("someuser/dev", indexconfig=dict(
        acl_pkg_read="someuser"))
    mapp.upload_file_pypi("hello-1.0.tar.gz", b'content', "hello", "1.0")
    (path,) = mapp.get_release_paths("hello")
    # current user should be able to read package and index
    testapp.xget(200, api1.index)
    testapp.xget(
        200, '/+authcheck',
        headers=ResponseHeaders({'X-Original-URI': api1.index}))
    testapp.xget(200, path)
    testapp.xget(
        200, '/+authcheck',
        headers=ResponseHeaders({'X-Original-URI': 'http://localhost' + path}))
    # create another user and index deriving from the previous
    api2 = mapp.create_and_use("otheruser/dev", indexconfig=dict(bases="someuser/dev"))
    # the user should be able to access the first index
    testapp.xget(200, api1.index)
    # but the authcheck will fail, so through nginx it will be blocked
    testapp.xget(
        403, '/+authcheck',
        headers=ResponseHeaders({'X-Original-URI': api1.index}))
    # the package should be forbidden
    testapp.xget(403, path)
    testapp.xget(
        403, '/+authcheck',
        headers=ResponseHeaders({'X-Original-URI': 'http://localhost' + path}))
    # the users own index should be accessible
    testapp.xget(200, api2.index)
    # but the authcheck will fail due to inheritance, so through nginx it will be blocked
    testapp.xget(
        403, '/+authcheck',
        headers=ResponseHeaders({'X-Original-URI': api2.index}))


@pytest.mark.skipif(
    devpi_server_version < parse_version("6dev"),
    reason="Needs devpiserver_auth_denials hook")
def test_deny_login(makemapp, maketestapp, makexom):
    from devpi_lockdown import main as lockdown_plugin
    from devpi_lockdown.main import devpiserver_hookimpl
    from devpi_web import main as web_plugin

    class Plugin:
        @devpiserver_hookimpl
        def devpiserver_auth_denials(self, request, acl, user, stage):
            return self.results.pop()

    plugin = Plugin()
    xom = makexom(plugins=[lockdown_plugin, plugin, web_plugin])
    testapp = maketestapp(xom)
    mapp = makemapp(testapp)
    plugin.results = [None]
    mapp.create_user("user1", "1")
    plugin.results = [[('user1', 'user_login')]]
    r = testapp.post(
        'http://localhost/+login?goto_url=/foo/bar',
        dict(username="user1", password="1", submit=""),
        code=401)
    assert "has no permission to login with the" in r.text
