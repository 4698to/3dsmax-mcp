"""Tests for auto-discovery of 3ds Max instances from the JSONL registry file.

Run standalone (no 3ds Max required):

    .venv\\Scripts\\python tests\\test_registry_discovery.py

Verifies: fresh entries are discovered, stale entries are ignored, a missing
registry falls back to the single-instance mode, an explicit MAXMCP_INSTANCES
config always wins, refresh picks up newly started instances, and instances
that disappear release their locks.
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from maxmcp.instance_manager import (  # noqa: E402
    InstanceManager,
    InstanceNotAcquiredError,
    _default_registry_path,
)


def _write_registry(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


def _fresh(port: int, name: str, age: float = 5.0) -> dict:
    return {
        "host": "127.0.0.1",
        "port": port,
        "pid": 10000 + port,
        "name": name,
        "maxVersion": 2024,
        "lastSeen": time.time() - age,
    }


def _stale(port: int, name: str) -> dict:
    return _fresh(port, name, age=500.0)


def _new_manager(registry: Path) -> InstanceManager:
    os.environ["MAXMCP_REGISTRY"] = str(registry)
    os.environ.pop("MAXMCP_INSTANCES", None)
    return InstanceManager()


def main() -> None:
    checks = 0
    passed = 0

    def check(label: str, cond: bool, detail: str = "") -> None:
        nonlocal checks, passed
        checks += 1
        status = "PASS" if cond else "FAIL"
        if cond:
            passed += 1
        print(f"  [{status}] {label}{' -- ' + detail if detail else ''}")

    with tempfile.TemporaryDirectory() as tmp:
        registry = Path(tmp) / "instances.jsonl"

        # 1. Missing registry file -> single-instance fallback
        mgr = _new_manager(registry)
        names = [i.name for i in mgr._instances]
        check("missing registry falls back to single instance", names == ["max1"], str(names))
        check("fallback enables auto-bind", mgr._auto_bind is True)
        check(
            "registry path resolves under MAXMCP_REGISTRY",
            _default_registry_path() == str(registry),
            _default_registry_path(),
        )

        # 2. Two fresh entries -> auto-discovered
        _write_registry(registry, [_fresh(19001, "maxA"), _fresh(19002, "maxB")])
        mgr = _new_manager(registry)
        check(
            "fresh entries are discovered",
            sorted(i.name for i in mgr._instances) == ["maxA", "maxB"],
            str(sorted(i.name for i in mgr._instances)),
        )
        check("discovery disables auto-bind", mgr._auto_bind is False)
        by_name = {i.name: i for i in mgr._instances}
        check("ports match registry", (by_name["maxA"].port, by_name["maxB"].port) == (19001, 19002))

        # 3. Only stale entries -> single-instance fallback
        _write_registry(registry, [_stale(19001, "maxA")])
        mgr = _new_manager(registry)
        check(
            "stale entries are ignored (fallback)",
            [i.name for i in mgr._instances] == ["max1"],
            str([i.name for i in mgr._instances]),
        )

        # 4. Mixed fresh + stale -> only fresh is discovered
        _write_registry(registry, [_fresh(19001, "maxA"), _stale(19002, "maxB")])
        mgr = _new_manager(registry)
        check(
            "stale entry skipped in mixed file",
            sorted(i.name for i in mgr._instances) == ["maxA"],
            str(sorted(i.name for i in mgr._instances)),
        )

        # 5. Explicit MAXMCP_INSTANCES wins over registry
        os.environ["MAXMCP_INSTANCES"] = "127.0.0.1:19999:manual"
        os.environ["MAXMCP_REGISTRY"] = str(registry)  # registry still holds maxA
        mgr = InstanceManager()
        check(
            "explicit config wins over registry",
            [i.name for i in mgr._instances] == ["manual"],
            str([i.name for i in mgr._instances]),
        )
        mgr.refresh_registry()
        check(
            "refresh is a no-op with explicit config",
            [i.name for i in mgr._instances] == ["manual"],
            str([i.name for i in mgr._instances]),
        )
        os.environ.pop("MAXMCP_INSTANCES", None)

        # 6. Refresh picks up newly started instances (fallback -> discovered)
        _write_registry(registry, [_fresh(19001, "maxA")])
        mgr = _new_manager(registry)
        mgr.refresh_registry()
        check(
            "refresh upgrades fallback to discovered",
            sorted(i.name for i in mgr._instances) == ["maxA"],
            str(sorted(i.name for i in mgr._instances)),
        )
        check("refresh disables auto-bind", mgr._auto_bind is False)

        # 7. Disappearing instance releases its lock
        session_a = object()
        client = mgr.acquire(session_a, name="maxA")
        check("acquired maxA", client.port == 19001)
        _write_registry(registry, [])  # maxA vanished (3ds Max closed)
        mgr.refresh_registry()
        check(
            "disappeared instance is removed",
            mgr._instances == [],
            str([i.name for i in mgr._instances]),
        )
        check(
            "lock for gone instance was released",
            mgr.get_my_instance(session_a)["acquired"] is False,
        )
        try:
            mgr.release(session_a)
            check("release after cleanup raises InstanceNotAcquiredError", False)
        except InstanceNotAcquiredError:
            check("release after cleanup raises InstanceNotAcquiredError", True)

    print(f"\nALL {passed}/{checks} CHECKS PASSED" if passed == checks else f"\n{checks - passed} CHECKS FAILED")
    sys.exit(0 if passed == checks else 1)


if __name__ == "__main__":
    main()
