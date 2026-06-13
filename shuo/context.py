"""
context.py — Re-exports from core.context (dialact-eval).

Source of truth lives in dialact-eval/core/context.py.
Install dialact-eval in editable mode: pip install -e ../dialact-eval
"""
from core.context import (  # noqa: F401
    CallContext,
    load_identity_file,
    build_system_prompt,
    confirm_context,
    _ACTION_CANCEL,
    _ACTION_PROCEED,
    _EDITABLE_FIELDS,
)
