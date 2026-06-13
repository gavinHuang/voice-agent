"""
translation.py — Re-exports from core.translation (dialact-eval).

Source of truth lives in dialact-eval/core/translation.py.
Install dialact-eval in editable mode: pip install -e ../dialact-eval
"""
from core.translation import (  # noqa: F401
    Translator,
    LLMTranslator,
    DeepLTranslator,
    extract_speech_text,
    get_translator,
)
