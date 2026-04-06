# What the system should be
This is descirbe about the system the project is trying to build, not what it is today.

# 3 Layers 
- ISP Layer: Twilio or self-build solution, handle the telnetwork with protocol agreed to make/receive phone call.
- Voice Layer: handle turn detection/text2speech/speech2text/emotion detection/DTMF
- Voice Agent layer: hanlde intelligence, communicate between callee/caller, it can act as an agent/human (with UI to take voice from microphone)/ivr system

# Roles
In this system, there can be:
- A human (user uisng softphone)
- An Agent with intelligence powered by LLM, it has context and goal
- Mockup IVR system: a system powered by Voice Agent with a predefined ivr menu
- Callee: the other side of phone

# Applications
- IVR system: mock up ivr system which act as a service phone number interact with ISP layer, backed by voice agent
- Soft phone: mock up phone number (configurable), comes with UI that can play voice and take voice intake (microphone), can be backed a voice agent or a real human
- Monitoring dashboard: monitor the whole conversation (show transcripts realtime), allow intercept/take-over/hand-back/hangup/etc.

# Key Commponents 
## Cli
The system should be able to expose all it's capabilities via command line, including the backend/ngrok webrtc, etc.

## Voice Agent
An agent with capability to do TTS/STT/DTMF, It should inherently able to interact with telcom network (e.g. via twilio). it can be configured to be an IVR system.
Can also be task with goal, context, identity, soul.

## IVR Benchmark
Add dataset to evaluate system successful rate for Voice Agent to interact with IVR system

## Softphone/IVR without actual phone call
Abstract away twilio for local test, local call local.

## Agent Framework
candidate: pi-agent/pydantic