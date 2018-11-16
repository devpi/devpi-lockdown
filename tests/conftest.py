from test_devpi_server.conftest import gentmp  # noqa
from test_devpi_server.conftest import httpget  # noqa
from test_devpi_server.conftest import makemapp  # noqa
from test_devpi_server.conftest import maketestapp  # noqa
from test_devpi_server.conftest import makexom  # noqa
from test_devpi_server.conftest import mapp  # noqa
from test_devpi_server.conftest import pypiurls  # noqa
from test_devpi_server.conftest import storage_info  # noqa
from test_devpi_server.conftest import testapp  # noqa
import pytest


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
