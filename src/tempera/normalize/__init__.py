"""Tool-independent action normalization."""

from .action import CanonicalAction, normalize_action, register_adapter

__all__ = ["CanonicalAction", "normalize_action", "register_adapter"]
