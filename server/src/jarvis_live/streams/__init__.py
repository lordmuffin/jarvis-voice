"""Streams pushed in over SRT or RTMP (OBS, phones) through MediaMTX, as live sessions.

Importing this package pulls in nothing: the ``jarvis-live`` CLI parses ``stream-key`` on a
slim client install, which has no server dependencies.
"""

MODES = ("meeting", "solo")
SOURCES = ("obs", "mobile")
