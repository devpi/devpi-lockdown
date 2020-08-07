def test_index_when_unauthorized(devpi):
    devpi("logout")
    devpi("index", code=403)
