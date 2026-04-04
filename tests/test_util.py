from sydent.util.stringutils import is_valid_matrix_server_name


def test_is_valid_matrix_server_name():
    """Tests that the is_valid_matrix_server_name function accepts only
    valid hostnames (or domain names), with optional port number.
    """
    assert is_valid_matrix_server_name("9.9.9.9")
    assert is_valid_matrix_server_name("9.9.9.9:4242")
    assert is_valid_matrix_server_name("[::]")
    assert is_valid_matrix_server_name("[::]:4242")
    assert is_valid_matrix_server_name("[a:b:c::]:4242")

    assert is_valid_matrix_server_name("example.com")
    assert is_valid_matrix_server_name("EXAMPLE.COM")
    assert is_valid_matrix_server_name("ExAmPlE.CoM")
    assert is_valid_matrix_server_name("example.com:4242")
    assert is_valid_matrix_server_name("localhost")
    assert is_valid_matrix_server_name("localhost:9000")
    assert is_valid_matrix_server_name("a.b.c.d:1234")

    assert not is_valid_matrix_server_name("[:::]")
    assert not is_valid_matrix_server_name("a:b:c::")

    assert not is_valid_matrix_server_name("example.com:65536")
    assert not is_valid_matrix_server_name("example.com:0")
    assert not is_valid_matrix_server_name("example.com:-1")
    assert not is_valid_matrix_server_name("example.com:a")
    assert not is_valid_matrix_server_name("example.com: ")
    assert not is_valid_matrix_server_name("example.com:04242")
    assert not is_valid_matrix_server_name("example.com: 4242")
    assert not is_valid_matrix_server_name("example.com/example.com")
    assert not is_valid_matrix_server_name("example.com#example.com")
