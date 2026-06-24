import asyncio, json, uuid
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from aiortc import RTCPeerConnection, RTCSessionDescription
from .config import voice_config
from .bridge import VoiceBridge

app = FastAPI(title="Jarvis Voice Calling")
sessions = {}

@app.on_event("startup")
async def startup():
    print(f"[+] Voice Signaling: {voice_config.SIGNALING_HOST}:{voice_config.SIGNALING_PORT}")

@app.websocket("/ws")
async def voice_ws(ws: WebSocket):
    await ws.accept()
    sid = str(uuid.uuid4())[:8]
    pc = RTCPeerConnection()
    bridge = VoiceBridge(sid)
    pc.addIceServer({
        "urls": [voice_config.TURN_SERVER],
        "username": voice_config.TURN_USERNAME,
        "credential": voice_config.TURN_PASSWORD
    })

    @pc.on("track")
    def on_track(track):
        if track.kind == "audio":
            print(f"[+] Mic connected: {sid}")
            bridge.start_stream(track)

    async def handle_ws(msg: str):
        data = json.loads(msg)
        if data["type"] == "offer":
            await pc.setRemoteDescription(RTCSessionDescription(sdp=data["sdp"], type="offer"))
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
            await ws.send_text(json.dumps({"type": "answer", "sdp": pc.localDescription.sdp}))
        elif data["type"] == "ice_candidate":
            await pc.addIceCandidate(data["candidate"])

    sessions[sid] = {"pc": pc, "ws": ws, "bridge": bridge}
    try:
        async for msg in ws:
            await handle_ws(msg)
    except WebSocketDisconnect:
        print(f"[-] Disconnected: {sid}")
    finally:
        bridge.stop_stream()
        await pc.close()
        sessions.pop(sid, None)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=voice_config.SIGNALING_HOST, port=voice_config.SIGNALING_PORT)
