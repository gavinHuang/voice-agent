# Benchmark Dataset Research

Search conducted: 2026-04-04  
Query focus: benchmarks for task completion via multi-party conversational AI (voice agents, IVR, customer service)

---

## Datasets Found

### 1. τ-bench (tau-bench)
- **Source:** Sierra Research — https://arxiv.org/abs/2406.12045 · https://github.com/sierra-research/tau-bench
- **What it is:** Simulates dynamic multi-turn conversations between a user LLM and a customer-service agent with real API tools + policy documents.
- **Domains:** Retail, Airline (τ³-bench adds Banking + voice modality)
- **Size:** ~69 retail tasks, ~16 airline tasks (few-shot), full eval set larger
- **Key metric:** pass^k — even GPT-4o <50% task success, pass^8 <25% in retail
- **Relevance:** Closest to this project. Real DB state, policy adherence, identity verification.
- **Status:** ✅ Downloaded + adapter written

---

### 2. T1 Dataset
- **Source:** https://arxiv.org/abs/2505.16986
- **What it is:** Tool-oriented conversational dataset for multi-turn agentic planning. 9 domains. Captures inter-tool dependencies (one tool's output feeds into the next).
- **Size:** Not specified in search results
- **Key metric:** Tool chain success across turns
- **Relevance:** Strong fit — tool use across dialogue turns, similar to IVR DTMF + API hybrid flows
- **Status:** ❌ Not downloaded — paper published May 2025, no public GitHub found at search time

---

### 3. ABCD (Action-Based Conversations Dataset)
- **Source:** ASAPPResearch — https://arxiv.org/abs/2104.00783 · https://github.com/asappresearch/abcd
- **What it is:** 10k+ human-human dialogues with 55 user intents. Actions constrained by company policy. Includes both raw and delexicalized versions.
- **Domains:** E-commerce customer service (10 flows: account access, order issues, product defects, shipping, etc.)
- **Size:** 8,034 train / 1,004 dev / 1,097 test dialogues
- **Key metric:** Action prediction accuracy, task success
- **Relevance:** High — policy-constrained service, identity verification, multi-step resolution
- **Status:** ✅ Downloaded + adapter written

---

### 4. MultiWOZ 2.4
- **Source:** https://arxiv.org/abs/2104.00773 · https://github.com/smartyfh/MultiWOZ2.4
- **What it is:** 10k+ human-human dialogues across 7 domains (hotel, restaurant, train, taxi, attraction, hospital, police). Standard slot-filling / dialogue-state-tracking benchmark.
- **Size:** 10,438 dialogues total
- **Key metric:** Joint goal accuracy, inform rate, success rate
- **Relevance:** Medium — covers booking/info tasks but no real tool calls or verification steps
- **Status:** ✅ Downloaded (MultiWOZ 2.2 test split) + adapter written

---

### 5. VoiceAgentBench
- **Source:** https://arxiv.org/abs/2510.07978
- **What it is:** Benchmark for SpeechLMs in realistic spoken agentic settings. 6,000+ synthetic spoken queries: single-tool, multi-tool, multi-turn, safety. English + 6 Indic languages.
- **Size:** 6,000+ queries
- **Key metric:** Task success rate on spoken agentic tasks
- **Relevance:** Voice-native benchmark — most directly evaluates spoken agent capability rather than text
- **Status:** ❌ Not downloaded — no public data release found (OpenReview submission, Oct 2025)

---

### 6. VoiceAssistant-Eval
- **Source:** https://arxiv.org/abs/2509.22651 · https://github.com/mathllm/VoiceAssistant-Eval
- **What it is:** Comprehensive benchmark across listening, speaking, and viewing. 10,497 examples, 13 task categories including multi-turn spoken dialogue and role-play.
- **Size:** 10,497 examples
- **Key metric:** Per-category accuracy across modalities
- **Relevance:** Medium — covers spoken dialogue but not customer-service task completion
- **Status:** ❌ Not downloaded — GitHub exists but focused on multimodal eval, less aligned with call/IVR task completion

---

### 7. JourneyBench
- **Source:** https://arxiv.org/html/2601.00596
- **What it is:** Graph-based scenario generation for policy-aware customer support agents. Introduces User Journey Coverage Score — measures adherence to multi-step policy graphs.
- **Size:** Validated against 1,000+ real production calls; synthetic conversations achieve 84.37% overall
- **Key metric:** User Journey Coverage Score (CP: 82.33%, GA: 87.78%)
- **Relevance:** Very high — specifically targets IVR → LLM agent transition, policy adherence, and customer support flows. Most similar to this project's benchmark design.
- **Status:** ❌ Not downloaded — no public data release found (Jan 2026 preprint)

---

### 8. Evaluating LLM-based Agents for Multi-Turn Conversations (Survey)
- **Source:** https://arxiv.org/pdf/2503.22458
- **What it is:** Survey of ~200 papers on evaluating multi-turn conversational agents (2023–2025). Not a dataset but a meta-reference.
- **Status:** ℹ️ Reference only — not a downloadable dataset

---

## Coverage Summary

| Dataset | Downloaded | Adapter | Bench Mode | Scenarios |
|---------|-----------|---------|------------|-----------|
| τ-bench retail | ✅ | ✅ | two-agent | 20 |
| τ-bench airline | ✅ | ✅ | two-agent | 19 |
| ABCD | ✅ | ✅ | two-agent | 20 |
| MultiWOZ 2.4 | ✅ (2.2 test) | ✅ | two-agent | 20 |
| T1 Dataset | ❌ | ❌ | — | — |
| VoiceAgentBench | ❌ | ❌ | — | — |
| VoiceAssistant-Eval | ❌ | ❌ | — | — |
| JourneyBench | ❌ | ❌ | — | — |

---

## What's Covered

- **Tool-use + identity verification** (τ-bench): Retail order management, airline reservation changes with real DB lookups
- **Policy-constrained customer service** (ABCD): 10 flow types, company guidelines, multi-step resolution
- **Multi-domain booking/information** (MultiWOZ): Hotels, restaurants, trains, taxis — slot filling across domains
- **Difficulty distribution** (ABCD): easy/medium/hard difficulty labeling for tiered evaluation
- **Structured caller context**: All adapters embed real user/account data into caller and answerer goals

## What's Not Covered

- **Voice/speech-native evaluation** (VoiceAgentBench, VoiceAssistant-Eval): These benchmark the spoken modality directly — audio quality, ASR robustness, prosody. The current adapters only test text-level task success.
- **Policy graph adherence** (JourneyBench): Journey coverage score measuring whether every required policy step was visited — not just whether the task succeeded.
- **Inter-tool dependency chains** (T1): Scenarios where tool A's output is a required input for tool B across turns (e.g., look up reservation ID → then modify → then confirm payment).
- **Banking domain** (τ³-bench): τ-bench's newest domain with wire transfers and fraud detection was not available in the public few-shot data at download time.
- **IVR mode coverage**: All four new datasets map to `--mode two-agent`. No new `--mode ivr` scenarios were added from these datasets (IVR mode remains covered by `scenarios/example_ivr.yaml`).

## To Add Later

- **JourneyBench** when data is released: write adapter targeting `TwoAgentSuccessCriteria.verification_phrases` to mirror Journey Coverage Score
- **T1** when GitHub is public: adapter using tool-chain structure to set `success_criteria.goal_phrases` at each tool boundary
- **VoiceAgentBench** when data is released: may require a new bench mode or an eval hook since it benchmarks the audio pipeline, not just task text
