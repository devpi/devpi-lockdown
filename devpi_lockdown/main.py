from pyramid.authentication import _SimpleSerializer
from pyramid.compat import url_unquote, url_quote
from pyramid.httpexceptions import HTTPFound, HTTPOk, HTTPUnauthorized
from pyramid.interfaces import IAuthenticationPolicy
from pyramid.view import view_config
from webob.cookies import CookieProfile


def includeme(config):
    config.add_route(
        "authcheck",
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


def devpiserver_pyramid_configure(config, pyramid_config):
    # by using include, the package name doesn't need to be set explicitly
    # for registrations of static views etc
    pyramid_config.include('devpi_lockdown.main')


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


@view_config(route_name="authcheck")
def authcheck_view(context, request):
    if not request.authenticated_userid:
        return HTTPUnauthorized()
    return HTTPOk()


def get_cookie_profile(request, max_age=0):
    return CookieProfile(
        'auth_tkt',
        httponly=True,
        max_age=max_age,
        secure=request.scheme == 'https',
        serializer=_SimpleSerializer()).bind(request)


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
