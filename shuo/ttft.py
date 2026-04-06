"""
TTFT (Time-To-First-Token) benchmark router.

Measures first-token latency across OpenAI-compatible models.
Mounted at /bench/ttft by server.py.

Usage:
    curl https://your-server/bench/ttft
    curl https://your-server/bench/ttft?models=gpt-4o-mini,gpt-4o&runs=5
    curl https://your-server/bench/ttft?models=groq/llama-3.3-70b&runs=10
"""

import os
import random
import time
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from openai import AsyncOpenAI

from .log import get_logger

logger = get_logger("shuo.ttft")

router = APIRouter()


# Each entry: (display_name, provider_key, model_id)
DEFAULT_MODELS = [
    # OpenAI 4-series
    ("gpt-4o-mini",   "openai", "gpt-4o-mini"),
    ("gpt-4o",        "openai", "gpt-4o"),
    ("gpt-4.1-nano",  "openai", "gpt-4.1-nano"),
    ("gpt-4.1-mini",  "openai", "gpt-4.1-mini"),
    ("gpt-4.1",       "openai", "gpt-4.1"),
    # OpenAI 5-series
    ("gpt-5-nano",    "openai", "gpt-5-nano"),
    ("gpt-5-mini",    "openai", "gpt-5-mini"),
    ("gpt-5",         "openai", "gpt-5"),
    ("gpt-5.1",       "openai", "gpt-5.1"),
    ("gpt-5.2",       "openai", "gpt-5.2"),
    # Groq
    ("groq/llama-3.3-70b",  "groq", "llama-3.3-70b-versatile"),
    ("groq/llama-3.1-8b",   "groq", "llama-3.1-8b-instant"),
]

_BENCH_PROMPT = "Explain how a combustion engine works."

_BENCH_MESSAGES = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": _BENCH_PROMPT},
]


def _make_clients() -> dict:
    """Build provider → AsyncOpenAI client map from available env keys."""
    clients = {}
    oai_key = os.getenv("OPENAI_API_KEY", "")
    if oai_key:
        clients["openai"] = AsyncOpenAI(api_key=oai_key)
    groq_key = os.getenv("GROQ_API_KEY", "")
    if groq_key:
        clients["groq"] = AsyncOpenAI(
            api_key=groq_key,
            base_url="https://api.groq.com/openai/v1",
        )
    return clients


async def _measure_ttft(client: AsyncOpenAI, model: str) -> float:
    """Single TTFT measurement in milliseconds."""
    is_new = model.startswith(("gpt-5", "o1", "o3", "o4"))
    token_param = "max_completion_tokens" if is_new else "max_tokens"

    params: dict = {
        "model": model,
        "messages": _BENCH_MESSAGES,
        "stream": True,
        token_param: 20,
    }
    if is_new:
        params["extra_body"] = {"reasoning_effort": "none"}
    else:
        params["temperature"] = 0

    t0 = time.perf_counter()
    try:
        stream = await client.chat.completions.create(**params)
    except Exception as e:
        if is_new and "none" in str(e).lower():
            params["extra_body"] = {"reasoning_effort": "minimal"}
            t0 = time.perf_counter()
            stream = await client.chat.completions.create(**params)
        else:
            raise

    async for chunk in stream:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta and delta.content:
            ttft_ms = (time.perf_counter() - t0) * 1000
            await stream.close()
            return ttft_ms

    return (time.perf_counter() - t0) * 1000


@router.get("/bench/ttft")
async def bench_ttft(
    models: Optional[str] = Query(
        None,
        description="Comma-separated model names. Defaults to a built-in list.",
    ),
    runs: int = Query(30, ge=1, le=100, description="Runs per model"),
):
    """
    Benchmark TTFT across OpenAI-compatible models.

    Runs are randomised across all models to avoid order bias.
    """
    clients = _make_clients()

    if models:
        entries = []
        for m in models.split(","):
            m = m.strip()
            if not m:
                continue
            if m.startswith("groq/"):
                entries.append((m, "groq", m.removeprefix("groq/")))
            else:
                entries.append((m, "openai", m))
        model_entries = entries
    else:
        model_entries = DEFAULT_MODELS

    model_entries = [(name, prov, mid) for name, prov, mid in model_entries if prov in clients]

    schedule = [(name, prov, mid, i) for name, prov, mid in model_entries for i in range(runs)]
    random.shuffle(schedule)

    total = len(schedule)
    names = [name for name, _, _ in model_entries]
    logger.info(f"TTFT benchmark: {len(model_entries)} models × {runs} runs = {total} calls (randomised)")

    times_by_model: dict[str, list[float]] = defaultdict(list)
    errors_by_model: dict[str, list[str]] = defaultdict(list)

    for idx, (name, prov, mid, run_i) in enumerate(schedule, 1):
        try:
            ms = await _measure_ttft(clients[prov], mid)
            times_by_model[name].append(round(ms, 1))
            logger.info(f"  [{idx}/{total}] {name} #{run_i+1} → {ms:.0f} ms")
        except Exception as e:
            errors_by_model[name].append(f"run {run_i+1}: {e}")
            logger.info(f"  [{idx}/{total}] {name} #{run_i+1} → ERROR")

    results = []
    for name in names:
        t = times_by_model.get(name, [])
        errs = errors_by_model.get(name, [])
        if not t:
            results.append({"model": name, "error": errs[0] if errs else "no data"})
            logger.info(f"  {name} → ERROR: {errs[0] if errs else 'no data'}")
            continue
        avg = round(sum(t) / len(t), 1)
        entry: dict = {
            "model": name,
            "runs": len(t),
            "avg_ms": avg,
            "min_ms": min(t),
            "max_ms": max(t),
            "all_ms": t,
        }
        if errs:
            entry["errors"] = errs
        results.append(entry)
        logger.info(f"  {name} → avg {avg} ms  (min {min(t)}, max {max(t)})")

    return JSONResponse({
        "prompt": _BENCH_PROMPT,
        "runs_per_model": runs,
        "results": results,
    })
