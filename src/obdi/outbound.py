"""A process that refuses to talk to anything but itself.

The development harness (`scripts/dev_corpus_ui.py`) serves invented data behind credentials that
cannot work, and says that nothing in it can contact a bank. That claim was false for one thing:
pressing Connect ran the credentials' pre-flight check, which posts the dummy id and secret to the
provider's real token endpoint. Overwriting credentials makes a request fail, not not happen, and
it still leaves the machine.

Setting `OBDI_REFUSE_OUTBOUND` makes the process refuse, at the socket and at the name lookup,
every connection that is not to this machine. It is enforced below every HTTP client in the
process, so it holds for code that builds its own client, and it fails the attempt loudly
(`OutboundRefused`, and a line on stderr naming the host) instead of timing out quietly.

It is a process-wide switch with no per-call opt-out, on purpose: an exception somebody can reach
for is the gap the harness fell through.
"""

from __future__ import annotations

import os
import socket
import sys
from collections.abc import Callable
from typing import Any

REFUSE_ENV = "OBDI_REFUSE_OUTBOUND"


class OutboundRefused(OSError):
    """A connection or lookup to somewhere other than this machine, refused by policy."""


_LOOPBACK_NAMES = frozenset({"localhost", "ip6-localhost", "::1", ""})

#: The functions replaced and what each was before, so `uninstall` can put them back.
_originals: dict[str, Callable[..., Any]] | None = None


def is_loopback(host: object) -> bool:
    """Whether a host (a name or an address) is this machine."""
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    text = str(host).strip().lower().strip("[]")
    return text in _LOOPBACK_NAMES or text.startswith("127.") or text == "::ffff:127.0.0.1"


def _refuse(host: object) -> OutboundRefused:
    print(f"outbound refused: {host}", file=sys.stderr, flush=True)
    return OutboundRefused(
        f"outbound connections are refused in this process ({REFUSE_ENV} is set): {host}"
    )


def _host_of(address: object) -> object:
    """The host of a socket address; a path (a unix socket) is local by nature."""
    if isinstance(address, tuple) and address:
        return address[0]
    return None


def install() -> bool:
    """Start refusing. True if this call installed it, False if it already was."""
    global _originals
    if _originals is not None:
        return False
    _originals = {
        "connect": socket.socket.connect,
        "connect_ex": socket.socket.connect_ex,
        "getaddrinfo": socket.getaddrinfo,
    }
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def connect(self: socket.socket, address: Any) -> None:
        host = _host_of(address)
        if not is_loopback(host):
            raise _refuse(host)
        real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        host = _host_of(address)
        if not is_loopback(host):
            raise _refuse(host)
        return real_connect_ex(self, address)

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if not is_loopback(host):
            raise _refuse(host)
        return real_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = connect  # type: ignore[method-assign,assignment]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign,assignment]
    socket.getaddrinfo = getaddrinfo
    return True


def uninstall() -> None:
    """Stop refusing. For tests, which must not leave the policy on for the next one."""
    global _originals
    if _originals is None:
        return
    socket.socket.connect = _originals["connect"]  # type: ignore[method-assign]
    socket.socket.connect_ex = _originals["connect_ex"]  # type: ignore[method-assign]
    socket.getaddrinfo = _originals["getaddrinfo"]
    _originals = None


def install_if_requested() -> bool:
    """Install the refusal when `OBDI_REFUSE_OUTBOUND` is set to something other than 0 or empty."""
    if os.environ.get(REFUSE_ENV, "").strip() in ("", "0"):
        return False
    return install()
