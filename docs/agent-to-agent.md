# Agent-to-Agent Calls

Two agent instances can talk to each other locally without Twilio using `AgentPhone.pair()`. This is useful for benchmarking, automated testing, and building multi-agent pipelines.

## How it works

`AgentPhone.pair()` returns two `LocalPhone` instances wired together in-process. Audio sent by one phone arrives at the other's audio handler — bypassing the network entirely. Each phone fires its own `on_start` / `on_stop` callbacks, so each side runs a full `run_call()` coroutine independently.

```
caller run_call() ──► LocalPhone A ──► LocalPhone B ──► answerer run_call()
                  ◄──────────────────────────────────◄
```

## Basic usage

```python
import asyncio
from shuo.phone import AgentPhone
from shuo.call import run_call
from shuo.context import CallContext
from shuo.voice import VoicePool

async def main():
    caller_phone, answerer_phone = AgentPhone.pair()

    caller_ctx = CallContext(goal="Book a dental appointment for next Tuesday at 2pm.")
    answerer_ctx = CallContext(
        goal="You are a dental receptionist. Offer Tuesday at 2pm and confirm the booking.",
        agent_name="Receptionist",
    )

    voice_pool = VoicePool(size=2)
    await voice_pool.start()

    await asyncio.gather(
        run_call(caller_phone, get_goal=lambda _: caller_ctx.goal, voice_pool=voice_pool),
        run_call(answerer_phone, get_goal=lambda _: answerer_ctx.goal, voice_pool=voice_pool),
    )

asyncio.run(main())
```

## Connection timeout

`LocalPhone` raises `TimeoutError` if the peer does not send audio within 5 seconds. This prevents hung coroutines when one side fails to start. The timeout is configurable via the `LOCAL_PHONE_TIMEOUT` environment variable (seconds, float).

## Running the built-in benchmark

The two-agent benchmark runs two scenarios end-to-end using the local phone pair:

```bash
voice-agent bench --dataset eval/scenarios/two_agent.yaml --mode two-agent
```

Scenarios are defined in `eval/scenarios/two_agent.yaml`. Each scenario specifies:
- `caller.goal` — the objective of the calling agent
- `answerer.goal` — the objective of the answering agent
- `answerer.opening_line` — the first thing the answerer says (optional)
- `success_criteria` — transcript keywords and max turn count checked after the call

## CLI shortcut

```bash
voice-agent local-call \
  --caller-goal "Book an appointment for Tuesday" \
  --callee-goal "You are a receptionist. Offer Tuesday at 2pm."
```

## Simulator flow

The scripted scheduler flow (`simulator/flows/two_agent.yaml`) can be used as the answerer side in IVR-mode benchmarks, giving a deterministic script for the answerer while the caller runs a real LLM agent.
