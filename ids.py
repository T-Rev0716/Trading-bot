"""Token identity. A ticker is a name, not an identity: always chain plus full address."""

CHAIN_NAME = {1399811149: "solana", 4663: "robinhood", 56: "bsc", 8453: "base"}
EVM = {"robinhood", "bsc", "base"}


def token_key(chain: str, address: str) -> str:
    """'solana:<mint>' or 'bsc:0x...'. EVM addresses are case-insensitive, so lowered;
       Solana addresses are case-sensitive, so kept exactly."""
    if chain not in CHAIN_NAME.values():
        raise ValueError(f"unknown chain {chain!r}")
    if not address:
        raise ValueError("empty address")
    return f"{chain}:{address.lower() if chain in EVM else address}"


def split_key(key: str) -> tuple[str, str]:
    chain, address = key.split(":", 1)
    return chain, address


def label(d: dict) -> str:
    """Pick option name: ticker for the model to read, full identity for the code."""
    return f"{d['ticker']} ({d['token_key']})"
