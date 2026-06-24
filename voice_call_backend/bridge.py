import asyncio, queue, numpy as np
from threading import Thread, Event
from .config import voice_config

class VoiceBridge:
    def __init__(self, session_id: str):
        self.sid = session_id
        self.is_running = False
        self.stop_event = Event()
        self.audio_queue: queue.Queue = queue.Queue(maxsize=10)
        self.stream_thread: Thread | None = None

    def start_stream(self, audio_track):
        self.is_running = True
        self.stream_thread = Thread(target=self._listen_loop, args=(audio_track,), daemon=True)
        self.stream_thread.start()
        self.pipeline_thread = Thread(target=self._pipeline_loop, daemon=True)
        self.pipeline_thread.start()

    def _listen_loop(self, track):
        while self.is_running and not self.stop_event.is_set():
            try:
                frame = asyncio.run_coroutine_threadsafe(track.recv(), asyncio.new_event_loop()).result()
                samples = np.array(frame.audio)
                self.audio_queue.put_nowait(samples)
            except Exception as e:
                if self.is_running:
                    print(f"[!] Stream error {self.sid}: {e}")

    def _pipeline_loop(self):
        while self.is_running and not self.stop_event.is_set():
            if not self.audio_queue.empty():
                audio_chunk = self.audio_queue.get()
                # 🔌 PLUG YOUR STT → LLM → TTS HERE
                pass
            else:
                asyncio.sleep(0.05)

    def stop_stream(self):
        self.is_running = False
        self.stop_event.set()
        if self.stream_thread:
            self.stream_thread.join(timeout=2)
