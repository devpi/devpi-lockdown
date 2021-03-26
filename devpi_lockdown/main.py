from devpi_common.url import URL
from devpi_server import __version__ as devpiserver_version
from pluggy import HookimplMarker
from pkg_resources import parse_version
from pyramid.interfaces import IRequestExtensions
from pyramid.interfaces import IRootFactory
from pyramid.interfaces import IRoutesMapper
from pyramid.httpexceptions import HTTPForbidden
from pyramid.httpexceptions import HTTPFound, HTTPOk, HTTPUnauthorized
try:
    from pyramid.interfaces import IAuthenticationPolicy
except ImportError:
    IAuthenticationPolicy = object()
try:
    from pyramid.interfaces import ISecurityPolicy
except ImportError:
    ISecurityPolicy = object()
from pyramid.request import Request
from pyramid.request import apply_request_extensions
from pyramid.threadlocal import RequestContext
from pyramid.traversal import DefaultRootFactory
try:
    from pyramid.util import SimpleSerializer
except ImportError:
    from pyramid.authentication import _SimpleSerializer as SimpleSerializer
from pyramid.view import view_config
from urllib.parse import quote as url_quote
from urllib.parse import unquote as url_unquote
from webob.cookies import CookieProfile
import re


devpiserver_hookimpl = HookimplMarker("devpiserver")
devpiserver_version = parse_version(devpiserver_version)

is_atleast_server6 = (devpiserver_version >= parse_version("6dev"))


def includeme(config):
    config.add_route(
        "/+authcheck",
        "/+authcheck")
    config.add_route(
        "login",
        "/+login",
        accept="text/html")
    config.add_route(
        "logout",
        "/+logout",
        accept="text/html")
    config.scan()


def find_injection_index(nginx_lines):
    # find first location block
    for index, line in enumerate(nginx_lines):
        if line.strip().startswith("location"):
            break
    # go back until we have a non empty non comment line
    for index in range(index - 1, 0, -1):
        if nginx_lines[index].strip().startswith("#"):
            continue
        if not nginx_lines[index].strip():
            continue
        return index + 1


nginx_template = """
    # this redirects to the login view when not logged in
    recursive_error_pages on;
    error_page 401 = @error401;
    location @error401 {{
        return 302 /+login?goto_url=$request_uri;
    }}

    # lock down everything by default
    auth_request /+authcheck;

    # the location to check whether the provided infos authenticate the user
    location = /+authcheck {{
        internal;

        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
        proxy_set_header X-Original-URI $request_uri;
        {x_outside_url}
        {x_real_ip}
        {proxy_pass}
    }}
    """.rstrip()


def _inject_lockdown_config(nginx_lines):
    # inject our parts before the first location block
    index = find_injection_index(nginx_lines)

    def find_line(content):
        regexp = re.compile(content, re.I)
        for line in nginx_lines:
            if regexp.search(line):
                return "%s # same as in @proxy_to_app below" % line.strip()
        return "couldn't find %r" % content

    nginx_lines[index:index] = nginx_template.format(
        x_outside_url=find_line("proxy_set_header.+x-outside-url"),
        x_real_ip=find_line("proxy_set_header.+x-real-ip"),
        proxy_pass=find_line("proxy_pass")).splitlines()


@devpiserver_hookimpl(optionalhook=True)
def devpiserver_genconfig(tw, config, argv, writer):
    from devpi_server.genconfig import gen_nginx

    # first get the regular nginx config
    nginx_lines = []

    def my_writer(basename, content):
        nginx_lines.extend(content.splitlines())

    gen_nginx(tw, config, argv, my_writer)
    _inject_lockdown_config(nginx_lines)

    # and write it out
    nginxconf = "\n".join(nginx_lines)
    writer("nginx-devpi-lockdown.conf", nginxconf)


@devpiserver_hookimpl
def devpiserver_pyramid_configure(config, pyramid_config):
    # by using include, the package name doesn't need to be set explicitly
    # for registrations of static views etc
    pyramid_config.include('devpi_lockdown.main')


@devpiserver_hookimpl
def devpiserver_get_credentials(request):
    """Extracts username and password from cookie.

    Returns a tuple with (username, password) if credentials could be
    extracted, or None if no credentials were found.
    """
    cookie = request.cookies.get('auth_tkt')
    if cookie is None:
        return
    token = url_unquote(cookie)
    try:
        username, password = token.split(':', 1)
    except ValueError:  # not enough values to unpack
        return None
    return username, password


@devpiserver_hookimpl(optionalhook=True)
def devpiserver_authcheck_always_ok(request):
    route = request.matched_route
    if route and route.name.endswith('/+api'):
        return True
    if route and route.name == '/+login':
        return True
    if route and route.name == 'login':
        return True
    if route and route.name == 'logout':
        return True
    if route and '+static' in route.name and '/+static' in request.url:
        return True
    if route and '+theme-static' in route.name and '/+theme-static' in request.url:
        return True


@devpiserver_hookimpl(optionalhook=True)
def devpiserver_authcheck_unauthorized(request):
    if not request.authenticated_userid:
        return True


def _auth_check_request(request):
    if devpiserver_authcheck_always_ok(request=request):
        request.log.debug(
            "Authcheck always OK for %s (%s)",
            request.url, request.matched_route.name)
        return HTTPOk()
    if not devpiserver_authcheck_unauthorized(request=request):
        request.log.debug(
            "Authcheck OK for %s (%s)",
            request.url, request.matched_route.name)
        return HTTPOk()
    request.log.debug(
        "Authcheck Unauthorized for %s (%s)",
        request.url, request.matched_route.name)
    user_agent = request.user_agent or ""
    if 'devpi-client' in user_agent:
        # devpi-client needs to know for proper error messages
        return HTTPForbidden()
    return HTTPUnauthorized()


@view_config(route_name="/+authcheck")
def authcheck_view(context, request):
    routes_mapper = request.registry.getUtility(IRoutesMapper)
    root_factory = request.registry.queryUtility(
        IRootFactory, default=DefaultRootFactory)
    request_extensions = request.registry.getUtility(IRequestExtensions)
    url = request.headers.get('x-original-uri', request.url)
    orig_request = Request.blank(url, headers=request.headers)
    orig_request.log = request.log
    orig_request.registry = request.registry
    if request_extensions:
        apply_request_extensions(
            orig_request, extensions=request_extensions)
    info = routes_mapper(orig_request)
    (orig_request.matchdict, orig_request.matched_route) = (
        info['match'], info['route'])
    root_factory = orig_request.matched_route.factory or root_factory
    orig_request.context = root_factory(orig_request)
    with RequestContext(orig_request):
        return _auth_check_request(orig_request)


def get_cookie_profile(request, max_age=0):
    return CookieProfile(
        'auth_tkt',
        httponly=True,
        max_age=max_age,
        secure=request.scheme == 'https',
        serializer=SimpleSerializer()).bind(request)


@view_config(
    route_name="login",
    renderer="templates/login.pt")
def login_view(context, request):
    policy = request.registry.queryUtility(IAuthenticationPolicy)
    if policy is None:
        policy = request.registry.getUtility(ISecurityPolicy)
    error = None
    if 'submit' in request.POST:
        user = request.POST['username']
        password = request.POST['password']
        if is_atleast_server6:
            token = policy.auth.new_proxy_auth(user, password, request=request)
        else:
            token = policy.auth.new_proxy_auth(user, password)
        if token:
            profile = get_cookie_profile(
                request,
                token['expiration'])
            cookie_value = url_quote("%s:%s" % (user, token['password']))
            # set the credentials on the current request
            request.cookies[profile.cookie_name] = cookie_value
            # coherence check of the generated credentials
            if user != request.authenticated_userid:
                request.response.status_code = 401
                error = "user %r could not be authenticated" % user
                return dict(error=error)
            # it is possible that a plugin removes the permission to login
            # the permission was added in 6.0.0
            if is_atleast_server6 and not request.has_permission('user_login'):
                request.response.status_code = 401
                error = (
                    "user %r has no permission to login with the "
                    "provided credentials" % user)
                return dict(error=error)
            headers = profile.get_headers(cookie_value)
            app_url = URL(request.application_url)
            url = app_url.joinpath(request.GET.get('goto_url'))
            # plus signs are urldecoded to a space, this reverses that
            url = url.replace(path=url.path.replace('/ ', '/+'))
            if app_url.netloc != url.netloc or app_url.scheme != url.scheme:
                # prevent abuse
                url = request.route_url('/')
            else:
                url = url.url
            return HTTPFound(location=url, headers=headers)
        else:
            request.response.status_code = 401
            error = "Invalid credentials"
    return dict(error=error)


@view_config(
    route_name="logout",
    request_method="GET",
    renderer="templates/logout.pt")
def logout_get_view(context, request):
    return dict()


@view_config(
    route_name="logout",
    request_method="POST",
    is_mutating=False)
def logout_post_view(context, request):
    profile = get_cookie_profile(request)
    headers = profile.get_headers(None)
    return HTTPFound(location=request.route_url('/'), headers=headers)
