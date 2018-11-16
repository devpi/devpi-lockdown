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
