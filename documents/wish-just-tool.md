# What the system should be
This is descirbe about the system the project is trying to build, not what it is today.

# 3 Layers 
- ISP Layer: Twilio or self-build solution, handle the telnetwork with protocol agreed to make/receive phone call.
- Voice Layer: handle turn detection/text2speech/speech2text/emotion detection/DTMF

# Roles
In this system, there can be:
- A human (user uisng softphone)
- An Agent with intelligence powered by LLM, it has context and goal
- Callee: the other side of phone

# Key Commponents 
## Cli
The system should be able to expose all it's capabilities via command line, including the backend/ngrok webrtc, etc.
This is to enable it become a plugable component to Agent system such as OpenClaw (via Skill)

## Voice Agent
An agent with capability to do TTS/STT/DTMF, It should inherently able to interact with telcom network (e.g. via twilio). it can be configured to be an IVR system.
Can also be task with goal, context, identity, soul.

