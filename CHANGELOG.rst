Changelog
=========

3.0.0 - Unreleased
------------------

- Require devpi-server >= 6.10.0.

- Added support for Python 3.12 and 3.13.

- Dropped Python 3.6, 3.7 and 3.8 support.


2.0.0 - 2021-05-16
------------------

.. note:: The nginx configuration has changed from 1.x.

- Dropped Python 2.7, 3.4 and 3.5 support.

- Support for devpi-server 6.0.0.

- Redirect back to original URL after login.

- With devpi-server 6.0.0 the ``devpi-gen-config`` script
  creates a ``nginx-devpi-lockdown.conf``.

- Automatically allow locations required for login page.

- Show error message for invalid credentials.

- Support Pyramid 2.0.


1.0.1 - 2018-11-16
------------------

- Fix import for Pyramid >= 1.10.0.

- Add /+static to configuration

- Lock down everything by default in the configuration and only allow the
  necessary locations


1.0.0 - 2017-03-10
------------------

- initial release
