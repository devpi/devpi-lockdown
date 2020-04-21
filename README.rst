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


For nginx the `auth_request`_ module is required.
You should use the ``devpi-genconfig`` script to generate your nginx configuration.
Then you need to add the following to your server block before the first location block:

.. code-block:: nginx

        # this redirects to the login view when not logged in
        recursive_error_pages on;
        error_page 401 = @error401;
        location @error401 {
            return 302 /+login;
        }

        # lock down everything by default
        auth_request /+authcheck;

        # the location to check whether the provided infos authenticate the user
        location = /+authcheck {
            internal;

            proxy_pass_request_body off;
            proxy_set_header Content-Length "";
            proxy_set_header X-Original-URI $request_uri;
            proxy_set_header X-outside-url $scheme://$http_host;  # copy the value from your existing configuration
            proxy_set_header X-Real-IP $remote_addr;  # copy the value from your existing configuration
            proxy_pass http://localhost:3141;  # copy the value from your existing configuration
        }

.. _auth_request: http://nginx.org/en/docs/http/ngx_http_auth_request_module.html
