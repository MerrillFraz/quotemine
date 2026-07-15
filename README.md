# voiceover-pipeline

Turn a video corpus into a searchable, speaker-attributed, word-timestamped
line database — then match lines to game/app events for voice-pack creation.

## Stages
1. **index** — demux, transcribe (WhisperX), diarize (pyannote), index to SQLite+FTS5
2. **identify** — per-utterance tagging → neural centroids → cluster attribution
3. **match** — keyword (FTS5) + semantic (MiniLM) matching of lines to events

See `docs/` for the full run order. Requires an NVIDIA GPU and a HuggingFace token.
