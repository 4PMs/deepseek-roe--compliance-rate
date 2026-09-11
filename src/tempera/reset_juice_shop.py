"""Compatibility wrapper for the environment-owned Juice Shop reset CLI.

The implementation lives in ``environments.juice_shop.reset``; keep this
module for callers that used the older package-level entry point.
"""

from environments.juice_shop.reset import main, reset_juice_shop

__all__ = ["reset_juice_shop"]


if __name__ == "__main__":
    main()
