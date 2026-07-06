"""Realtime (live) meeting transcription mode.

Modules:
- state:     process-wide live-active flag (batch worker pause coordination)
- engine:    whisper.cpp (pywhispercpp) wrapper, model resident in-process
- vad:       Silero VAD wrapper + utterance segmentation state machine
- streaming: transcription worker thread (partial scheduler, final priority)
- session:   LiveSessionManager (WAV capture, finalize -> input/ handoff)
- keywords:  lightweight keyword extraction from finalized text
- terms:     pluggable term extraction (janome nouns/compounds, keywords fallback)
- graph:     co-occurrence graph with cumulative-salience node selection
"""
