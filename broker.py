"""The broker interface: the only two things the engine may ask of an execution venue.

    submit(order, ref_price, liquidity_usd, flags) -> Fill
        raises BrokerReject       received and refused, nothing executed
        raises DeliveryTimeout    no acknowledgement: it may or may not have executed
    lookup(order_id) -> ("FILLED", Fill) | ("REJECTED", reason) | ("NOT_FOUND", None)
        raises LookupUnavailable  the venue could not be asked

There is exactly one implementation, venue.PaperVenue. Live execution is disabled: no
class in this repository sends an order anywhere real, and build() refuses any broker
that is not a PaperVenue. journal.JournaledBroker wraps the PaperVenue to record its
answers and to play them back; it executes nothing itself.
"""
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class BrokerReject(RuntimeError):
    """The venue received the order and refused it. Nothing executed."""


class DeliveryTimeout(RuntimeError):
    """No acknowledgement. The order may or may not have executed."""


class LookupUnavailable(RuntimeError):
    """The venue could not be asked. Nothing is known, nothing is assumed."""


@dataclass(frozen=True)
class Fill:
    price: float
    qty: float
    notional_usd: float       # tokens' value at the fill price
    fee_usd: float
    slippage_bps: float
    flags: tuple = ()


@runtime_checkable
class Broker(Protocol):
    def submit(self, order: dict, ref_price, liquidity_usd, flags=()) -> Fill: ...

    def lookup(self, order_id: str) -> tuple[str, object]: ...
