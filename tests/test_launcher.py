"""Checks that the Windows launcher avoids an occupied local port."""

import socket
import unittest

from launcher import HOST, available_port


class LauncherTests(unittest.TestCase):
    def test_selects_another_port_when_first_is_in_use(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
            held.bind((HOST, 0))
            occupied = held.getsockname()[1]
            self.assertNotEqual(available_port(occupied, occupied + 10), occupied)
            with self.assertRaises(RuntimeError):
                available_port(occupied, occupied)


if __name__ == "__main__":
    unittest.main()
