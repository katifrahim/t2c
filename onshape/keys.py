"""Show each configured Onshape API key's daily-cap status for the featurescript endpoint.

    mcp_server/.venv/bin/python -m onshape.keys

Reset times come from the Retry-After Onshape returned when that key was last capped
(persisted in out/key_state.json). A key that has never been capped shows 'available' —
its remaining quota is unknown until it is used (Onshape has no quota-status endpoint).
"""
import time

from onshape.client import Onshape


def main() -> None:
    api = Onshape(cache_only=True)   # never hits the network
    now = time.time()
    print(f"{len(api._keys)} key(s) configured:")
    for i, k in enumerate(api._keys, 1):
        acc = k["access"]
        unlock = api._locks.get(acc, 0)
        rem = unlock - now
        if rem > 0:
            print(f"  key #{i}  {acc[:8]}…  CAPPED — resets in ~{rem / 3600:.1f}h "
                  f"({rem / 60:.0f} min)")
        else:
            print(f"  key #{i}  {acc[:8]}…  available (not capped / reset)")


if __name__ == "__main__":
    main()
