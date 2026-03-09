"""
LLM service with streaming (Groq, OpenAI-compatible).
"""

import os
import asyncio
from typing import Optional, Callable, Awaitable, List, Dict

from openai import AsyncOpenAI

from ..log import ServiceLogger

log = ServiceLogger("LLM")

SYSTEM_PROMPT = """You are an AI agent making an outbound phone call on behalf of the caller. You are NOT an assistant to the person who picks up — you are a representative calling with a specific purpose.

Keep responses concise and conversational; they will be spoken aloud. No markdown, bullet points, or formatting. Be polite, direct, and professional.

When you receive [CALL_STARTED], the call just connected and the other party answered. Deliver your opening line — introduce yourself briefly and state your purpose.

When you believe your goal is accomplished, confirm the key details with the other party and wait for their acknowledgement before ending. Only after they confirm or say goodbye, say a single short closing sentence (e.g. "Great, thank you. Goodbye!") and immediately include [HANGUP] — keep it to one sentence, no extra pleasantries. Do NOT say goodbye and hang up in the same turn where you propose or summarise — wait for their response first.

When navigating an automated phone menu (IVR), include [DTMF:N] anywhere in your response to dial that digit (e.g., "[DTMF:1]" to press 1, "[DTMF:*]" for star). The tone is played automatically; the text around it is spoken as normal.

When you receive a [HOLD_CHECK] message, you are currently on hold. If the transcription is hold music or automated waiting messages, reply with exactly [HOLD_CONTINUE]. If a real person has started speaking, reply [HOLD_END] followed by your response to them."""


class LLMService:
    """
    OpenAI streaming LLM service.
    
    Manages conversation history and streams tokens via callback.
    """
    
    def __init__(
        self,
        on_token: Callable[[str], Awaitable[None]],
        on_done: Callable[[], Awaitable[None]],
        goal: str = "",
    ):
        self._on_token = on_token
        self._on_done = on_done

        goal_suffix = (
            f"\n\nYour goal for this call: {goal}\n"
            "Pursue this goal naturally. Do NOT announce your goal — just work towards it. "
            "Once accomplished and the other party has confirmed or said goodbye, say a brief goodbye and include [HANGUP] to end the call."
        ) if goal else ""
        self._system_prompt = SYSTEM_PROMPT + goal_suffix

        self._client = AsyncOpenAI(
            api_key=os.getenv("GROQ_API_KEY", ""),
            base_url="https://api.groq.com/openai/v1",
        )
        self._task: Optional[asyncio.Task] = None
        self._running = False

        self._history: List[Dict[str, str]] = []
    
    @property
    def is_active(self) -> bool:
        return self._running and self._task is not None
    
    @property
    def history(self) -> List[Dict[str, str]]:
        return self._history.copy()
    
    def clear_history(self) -> None:
        self._history = []
    
    async def start(self, user_message: str) -> None:
        """Start generating a response."""
        if self._running:
            await self.cancel()
        
        self._history.append({"role": "user", "content": user_message})
        
        self._running = True
        self._task = asyncio.create_task(self._generate())
        log.connected()
    
    async def cancel(self) -> None:
        """Cancel ongoing generation."""
        self._running = False
        
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        
        log.cancelled()
    
    async def _generate(self) -> None:
        """Generate response and stream tokens."""
        assistant_response = ""
        
        try:
            messages = [
                {"role": "system", "content": self._system_prompt}
            ] + self._history
            
            stream = await self._client.chat.completions.create(
                model=os.getenv("LLM_MODEL", "llama-3.3-70b-versatile"),
                messages=messages,
                stream=True,
                max_tokens=500,
                temperature=0.7,
            )
            
            async for chunk in stream:
                if not self._running:
                    break
                
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    token = delta.content
                    assistant_response += token
                    await self._on_token(token)
            
            if self._running and assistant_response:
                self._history.append({"role": "assistant", "content": assistant_response})
                await self._on_done()
        
        except asyncio.CancelledError:
            if assistant_response:
                self._history.append({"role": "assistant", "content": assistant_response + "..."})
            raise
        
        except Exception as e:
            log.error("Generation failed", e)
            await self._on_done()
        
        finally:
            self._running = False
            self._task = None
