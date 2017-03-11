devpi-lockdown: tools to enable authentication for read access
==============================================================

This plugin adds some views to allow locking down read access to devpi.

Only tested with nginx so far.


Installation
------------

``devpi-lockdown`` needs to be installed alongside ``devpi-server``.

You can install it with::

    pip install devpi-lockdown


Usage
-----

To lock down read access to devpi, you need a proxy in front of devpi which can use the provided views to limit access.


The views are:

/+authcheck

  This returns ``200`` when the user is authenticated or ``401`` if not.
  It uses the regular devpi credential checks and an additional credential check using a cookie provided by ``devpi-lockdown`` to allow login with a browser.

/+login

  A plain login form to allow access via browsers for use with ``devpi-web``.

/+logout

  Drops the authentication cookie.


For nginx the `auth_request`_ module is required and the configuration would something look like this:

.. code-block:: nginx

    server {
        ...

        # this redirects to the login view when not logged in
        error_page 401 = @error401;
        location @error401 {
            return 302 /+login;
        }

        # the location to check whether the provided infos authenticate the user
        location = /+authcheck {
            internal;

            proxy_pass_request_body off;
            proxy_set_header Content-Length "";
            proxy_set_header X-Original-URI $request_uri;
            proxy_set_header X-outside-url https://$host;
            proxy_pass http://localhost:3141;
        }

        # pass on /+login without authentication check to allow login
        location = /+login {
            proxy_set_header X-outside-url https://$host;
            proxy_pass http://localhost:3141;
        }

        # pass on /+api without authentication check for URL endpoint discovery
        location ~ /\+api$ {
            proxy_set_header X-outside-url https://$host;
            proxy_pass http://localhost:3141;
        }

        # pass on /+static without authentication check for browser access to css etc
        location /+static/ {
            proxy_set_header X-outside-url https://$host;
            proxy_pass http://localhost:3141;
        }

        # use auth_request to lock down all the rest
        location / {
            auth_request /+authcheck;
            proxy_set_header X-outside-url https://$host;
            proxy_pass http://localhost:3141;
        }
    }

If you use the example configuration from ``devpi-server`` then you have to add the ``auth_request`` check to the file and documentation parts as well.

.. _auth_request: http://nginx.org/en/docs/http/ngx_http_auth_request_module.html
