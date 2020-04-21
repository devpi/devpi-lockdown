from pluggy import HookimplMarker
from pyramid.compat import url_unquote, url_quote
from pyramid.interfaces import IRequestExtensions
from pyramid.interfaces import IRootFactory
from pyramid.interfaces import IRoutesMapper
from pyramid.httpexceptions import HTTPForbidden
from pyramid.httpexceptions import HTTPFound, HTTPOk, HTTPUnauthorized
from pyramid.interfaces import IAuthenticationPolicy
from pyramid.request import Request
from pyramid.request import apply_request_extensions
from pyramid.traversal import DefaultRootFactory
try:
    from pyramid.util import SimpleSerializer
except ImportError:
    from pyramid.authentication import _SimpleSerializer as SimpleSerializer
from pyramid.view import view_config
from webob.cookies import CookieProfile


devpiserver_hookimpl = HookimplMarker("devpiserver")


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
    if route and '+static' in route.name and '/+static' in request.url:
        return True
    if route and '+theme-static' in route.name and '/+theme-static' in request.url:
        return True


@devpiserver_hookimpl(optionalhook=True)
def devpiserver_authcheck_unauthorized(request):
    if not request.authenticated_userid:
        return True


@view_config(route_name="/+authcheck")
def authcheck_view(context, request):
    routes_mapper = request.registry.queryUtility(IRoutesMapper)
    root_factory = request.registry.queryUtility(
        IRootFactory, default=DefaultRootFactory)
    request_extensions = request.registry.queryUtility(IRequestExtensions)
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
    if devpiserver_authcheck_always_ok(request=orig_request):
        request.log.debug(
            "Authcheck always OK for %s (%s)",
            url, orig_request.matched_route.name)
        return HTTPOk()
    if not devpiserver_authcheck_unauthorized(request=orig_request):
        request.log.debug(
            "Authcheck OK for %s (%s)",
            url, orig_request.matched_route.name)
        return HTTPOk()
    request.log.debug(
        "Authcheck Unauthorized for %s (%s)",
        url, orig_request.matched_route.name)
    user_agent = request.user_agent or ""
    if 'devpi-client' in user_agent:
        # devpi-client needs to know for proper error messages
        return HTTPForbidden()
    return HTTPUnauthorized()


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
    auth_policy = request.registry.queryUtility(IAuthenticationPolicy)
    if 'submit' in request.POST:
        user = request.POST['username']
        password = request.POST['password']
        token = auth_policy.auth.new_proxy_auth(user, password)
        if token:
            profile = get_cookie_profile(
                request,
                token['expiration'])
            headers = profile.get_headers(url_quote(
                "%s:%s" % (user, token['password'])))
            return HTTPFound(location=request.route_url('/'), headers=headers)
    return dict()


@view_config(
    route_name="logout",
    request_method="GET")
def logout_view(context, request):
    profile = get_cookie_profile(request)
    headers = profile.get_headers(None)
    return HTTPFound(location=request.route_url('/'), headers=headers)
