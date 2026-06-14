# shuo - Voice Agent Framework
#
# Public API — import these directly from `shuo` rather than from sub-modules.
# Sub-module imports still work but are considered implementation details.

from .context import CallContext, build_system_prompt, load_identity_file  # noqa: F401
from .log import C, ServiceLogger, get_logger, setup_logging, colorize, quote  # noqa: F401
from .translation import Translator, get_translator, extract_speech_text  # noqa: F401
from .prompts import (  # noqa: F401
    PROMPT_WITH_TOOLS,
    PROMPT_TEXT_TAGS,
    supports_tools,
    goal_suffix,
    SUPPRESS_RE,
    FAREWELL_PHRASES,
    is_suppressed_token,
    is_farewell,
)
