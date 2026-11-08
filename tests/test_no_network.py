import socket

import pytest


def test_socket_connect_is_refused() -> None:
    with pytest.raises(RuntimeError, match="network"):
        socket.create_connection(("127.0.0.1", 9))
