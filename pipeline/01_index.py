#!/usr/bin/env python3
"""
archer_index.py — Stages 1-4 of the voicepack pipeline.

Builds a searchable, speaker-attributed, word-timed index of an episode corpus.

Subcommands (run in order; each is idempotent and resumable):

    scan        Discover episodes, parse SxxExx, populate the job queue.
    demux       Stage 1: extract 16kHz mono WAV via ffmpeg.           [CPU]
    transcribe  Stage 2: WhisperX ASR + wav2vec2 forced alignment.    [GPU]
    diarize     Stage 3: pyannote diarization + speaker assignment.   [GPU]
    index       Stage 4: build utterances table + FTS5 index.         [CPU]
    status      Show progress across all stages.

Design notes:
  - transcribe and diarize are SEPARATE PROCESSES on purpose. Each loads its
    models exactly once and the OS reclaims VRAM on exit. On a 12GB card this
    is the difference between "works" and "OOM at episode 4".
  - Raw WhisperX output is persisted to JSON per episode. The `index` stage
    reads only those JSONs, so you can re-tune utterance segmentation as many
    times as you like without ever touching the GPU again.
  - HF_TOKEN is read from the environment. It is never written to disk, never
    logged, and never appears in the database.
"""

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".webm"}

# WhisperX / ASR
WHISPER_MODEL = "large-v3"
COMPUTE_TYPE = "float16"   # Ampere. Drop to "int8" only if desperate.
BATCH_SIZE = 16            # Drop to 8 on OOM.
LANGUAGE = "en"            # Pinned: skips detection, avoids misfires on cold opens.

# Diarization bounds. pyannote UNDER-counts speakers when voices overlap,
# which this show does constantly. Bounding it helps materially.
MIN_SPEAKERS = 2
MAX_SPEAKERS = 10

# Utterance segmentation (index stage; cheap to re-tune, no GPU needed)
MAX_WORD_GAP_S = 0.45      # Gap larger than this splits an utterance.
MIN_UTTERANCE_S = 0.30     # Shorter than this is noise.
MAX_UTTERANCE_S = 8.00     # Longer than this is a monologue, not a callout.

# Filename parsing: S03E12, s3.e12, 3x12, etc.
SXXEXX = re.compile(r"[Ss](\d{1,2})[\s._-]*[Ee](\d{1,3})")
NXXN = re.compile(r"\b(\d{1,2})x(\d{2,3})\b")

STAGES = ("demux", "asr", "diarize")


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id          INTEGER PRIMARY KEY,
    path        TEXT UNIQUE NOT NULL,
    filename    TEXT NOT NULL,
    season      INTEGER,
    episode     INTEGER,
    wav_path    TEXT,
    json_path   TEXT,
    duration_s  REAL
);

CREATE TABLE IF NOT EXISTS jobs (
    episode_id  INTEGER NOT NULL,
    stage       TEXT NOT NULL,
    status      TEXT NOT NULL,          -- pending | done | failed
    error       TEXT,
    updated_at  TEXT,
    PRIMARY KEY (episode_id, stage),
    FOREIGN KEY (episode_id) REFERENCES episodes(id)
);

CREATE TABLE IF NOT EXISTS utterances (
    id          INTEGER PRIMARY KEY,
    episode_id  INTEGER NOT NULL,
    season      INTEGER,
    episode     INTEGER,
    speaker     TEXT,                   -- SPEAKER_00 (episode-local, NOT global)
    character   TEXT,                   -- filled by Stage 5 (global identity pass)
    start_s     REAL,
    end_s       REAL,
    duration_s  REAL,
    text        TEXT,
    word_count  INTEGER,
    FOREIGN KEY (episode_id) REFERENCES episodes(id)
);

CREATE INDEX IF NOT EXISTS idx_utt_dur     ON utterances(duration_s);
CREATE INDEX IF NOT EXISTS idx_utt_speaker ON utterances(speaker);
CREATE INDEX IF NOT EXISTS idx_utt_char    ON utterances(character);
CREATE INDEX IF NOT EXISTS idx_utt_ep      ON utterances(episode_id);
"""

FTS_SCHEMA = """
DROP TABLE IF EXISTS utterances_fts;
CREATE VIRTUAL TABLE utterances_fts USING fts5(
    text,
    content='utterances',
    content_rowid='id',
    tokenize='porter unicode61'
);
INSERT INTO utterances_fts(rowid, text) SELECT id, text FROM utterances;
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(workdir: Path) -> sqlite3.Connection:
    db = sqlite3.connect(workdir / "corpus.db", timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(SCHEMA)
    return db


def mark(db, episode_id, stage, status, error=None):
    db.execute(
        "INSERT INTO jobs(episode_id, stage, status, error, updated_at) "
        "VALUES (?,?,?,?,?) "
        "ON CONFLICT(episode_id, stage) DO UPDATE SET "
        "status=excluded.status, error=excluded.error, updated_at=excluded.updated_at",
        (episode_id, stage, status, error, now()),
    )
    db.commit()


def pending(db, stage):
    """Episodes not yet done for this stage, in season/episode order."""
    return db.execute(
        """
        SELECT e.* FROM episodes e
        LEFT JOIN jobs j ON j.episode_id = e.id AND j.stage = ?
        WHERE j.status IS NULL OR j.status != 'done'
        ORDER BY e.season, e.episode, e.filename
        """,
        (stage,),
    ).fetchall()


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------

def parse_se(name: str):
    m = SXXEXX.search(name)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = NXXN.search(name)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def cmd_scan(args, db):
    src = Path(args.source).expanduser()
    if not src.is_dir():
        sys.exit(f"Source directory not found: {src}")

    files = sorted(p for p in src.rglob("*") if p.suffix.lower() in VIDEO_EXTS)
    if not files:
        sys.exit(f"No video files found under {src}")

    added = unparsed = 0
    for f in files:
        season, ep = parse_se(f.name)
        if season is None:
            print(f"  [!] cannot parse SxxExx: {f.name}")
            unparsed += 1
        cur = db.execute(
            "INSERT OR IGNORE INTO episodes(path, filename, season, episode) VALUES (?,?,?,?)",
            (str(f), f.name, season, ep),
        )
        if cur.rowcount:
            added += 1
    db.commit()

    total = db.execute("SELECT COUNT(*) c FROM episodes").fetchone()["c"]
    print(f"\nFound {len(files)} files. Added {added} new. Corpus now {total} episodes.")
    if unparsed:
        print(f"\n*** {unparsed} file(s) have unparseable season/episode numbers. ***")
        print("*** Rename them NOW. Provenance in the audition board depends on it. ***")


# ---------------------------------------------------------------------------
# Stage 1: demux
# ---------------------------------------------------------------------------

def ffprobe_duration(path: str):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, check=True,
        )
        return float(out.stdout.strip())
    except Exception:
        return None


def cmd_demux(args, db):
    wavdir = Path(args.workdir) / "wav"
    wavdir.mkdir(parents=True, exist_ok=True)

    todo = pending(db, "demux")
    print(f"[demux] {len(todo)} episode(s) to process.\n")

    for i, row in enumerate(todo, 1):
        wav = wavdir / (Path(row["filename"]).stem + ".wav")
        print(f"[demux] ({i}/{len(todo)}) {row['filename']}")
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", row["path"],
                 "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)],
                check=True,
            )
            dur = ffprobe_duration(row["path"])
            db.execute("UPDATE episodes SET wav_path=?, duration_s=? WHERE id=?",
                       (str(wav), dur, row["id"]))
            mark(db, row["id"], "demux", "done")
        except subprocess.CalledProcessError as e:
            print(f"  [FAIL] {e}")
            mark(db, row["id"], "demux", "failed", str(e))

    print("\n[demux] complete.")


# ---------------------------------------------------------------------------
# Stage 2: transcribe + align  (GPU)
# ---------------------------------------------------------------------------

def cmd_transcribe(args, db):
    import torch
    import whisperx

    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cpu":
        sys.exit("CUDA not available. Fix that before starting a 50-hour job on a CPU.")
    print(f"[asr] device={device} ({torch.cuda.get_device_name(0)})")

    jsondir = Path(args.workdir) / "json"
    jsondir.mkdir(parents=True, exist_ok=True)

    todo = [r for r in pending(db, "asr") if r["wav_path"]]
    if not todo:
        print("[asr] nothing to do.")
        return
    print(f"[asr] {len(todo)} episode(s) to process. Loading models (once)...\n")

    model = whisperx.load_model(
        WHISPER_MODEL, device, compute_type=COMPUTE_TYPE, language=LANGUAGE
    )
    align_model, align_meta = whisperx.load_align_model(
        language_code=LANGUAGE, device=device
    )

    for i, row in enumerate(todo, 1):
        t0 = time.time()
        print(f"[asr] ({i}/{len(todo)}) {row['filename']}")
        try:
            audio = whisperx.load_audio(row["wav_path"])
            result = model.transcribe(audio, batch_size=BATCH_SIZE, language=LANGUAGE)
            result = whisperx.align(
                result["segments"], align_model, align_meta, audio, device,
                return_char_alignments=False,
            )
            jp = jsondir / (Path(row["filename"]).stem + ".asr.json")
            jp.write_text(json.dumps(result))
            db.execute("UPDATE episodes SET json_path=? WHERE id=?", (str(jp), row["id"]))
            mark(db, row["id"], "asr", "done")
            print(f"      ok — {len(result.get('segments', []))} segments, {time.time()-t0:.0f}s")
        except Exception as e:
            print(f"      [FAIL] {type(e).__name__}: {e}")
            mark(db, row["id"], "asr", "failed", f"{type(e).__name__}: {e}")

    print("\n[asr] complete.")


# ---------------------------------------------------------------------------
# Stage 3: diarize  (GPU, separate process => clean VRAM)
# ---------------------------------------------------------------------------

def load_diarization_pipeline(token, device):
    try:
        from whisperx.diarize import DiarizationPipeline
    except ImportError:
        import whisperx
        DiarizationPipeline = whisperx.DiarizationPipeline
    return DiarizationPipeline(token=token, device=device)

def assign_speakers(diarize_segments, result):
    try:
        from whisperx.diarize import assign_word_speakers
        return assign_word_speakers(diarize_segments, result)
    except ImportError:
        import whisperx
        return whisperx.assign_word_speakers(diarize_segments, result)


def cmd_diarize(args, db):
    import torch
    import whisperx

    token = os.environ.get("HF_TOKEN")
    if not token:
        sys.exit("HF_TOKEN not set in environment. Export it; do not hardcode it.")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cpu":
        sys.exit("CUDA not available.")
    print(f"[diar] device={device} ({torch.cuda.get_device_name(0)})")

    todo = [r for r in pending(db, "diarize") if r["json_path"] and r["wav_path"]]
    if not todo:
        print("[diar] nothing to do. (Run `transcribe` first?)")
        return
    print(f"[diar] {len(todo)} episode(s). Loading pipeline (once)...\n")

    pipe = load_diarization_pipeline(token, device)

    empty_labels = 0
    for i, row in enumerate(todo, 1):
        t0 = time.time()
        print(f"[diar] ({i}/{len(todo)}) {row['filename']}")
        try:
            audio = whisperx.load_audio(row["wav_path"])
            diar, embeddings = pipe(
                audio,
                min_speakers=MIN_SPEAKERS,
                max_speakers=MAX_SPEAKERS,
                return_embeddings=True,
            )

            result = json.loads(Path(row["json_path"]).read_text())
            result = assign_speakers(diar, result)

            # Canary: the community-1 license trap produces empty labels SILENTLY.
            labeled = sum(
                1 for s in result.get("segments", [])
                for w in s.get("words", []) if w.get("speaker")
            )
            if labeled == 0:
                empty_labels += 1
                print("      [!!] ZERO speaker labels assigned.")
            # pyannote already computed these to cluster. Keep them —
            # they ARE the Stage 5 centroids, for free.
            if embeddings:
                result["_speaker_embeddings"] = {
                    str(k): [float(x) for x in v] for k, v in embeddings.items()
                }

            Path(row["json_path"]).write_text(json.dumps(result))
            mark(db, row["id"], "diarize", "done")
            print(f"      ok — {labeled} words labeled, {time.time()-t0:.0f}s")
        except Exception as e:
            print(f"      [FAIL] {type(e).__name__}: {e}")
            mark(db, row["id"], "diarize", "failed", f"{type(e).__name__}: {e}")

    if empty_labels:
        print(f"\n*** {empty_labels} episode(s) got ZERO speaker labels. ***")
        print("*** Almost certainly the pyannote model license was not accepted for the")
        print("*** model THIS whisperx build actually uses. Check which one it pulls")
        print("*** (speaker-diarization-community-1 vs 3.1), accept it on HuggingFace,")
        print("*** then re-run. This failure is silent by design and will not raise. ***")

    print("\n[diar] complete.")


# ---------------------------------------------------------------------------
# Stage 4: index  (CPU only — re-run freely to re-tune segmentation)
# ---------------------------------------------------------------------------

def words_to_utterances(result):
    """
    Flatten to a word stream, then split into utterances on:
      - speaker change
      - inter-word gap > MAX_WORD_GAP_S
      - running length > MAX_UTTERANCE_S
    This yields natural callout-sized units instead of WhisperX's long segments.
    """
    words = []
    for seg in result.get("segments", []):
        for w in seg.get("words", []):
            if w.get("start") is None or w.get("end") is None:
                continue  # alignment can drop digits/symbols
            words.append({
                "w": w.get("word", "").strip(),
                "s": float(w["start"]),
                "e": float(w["end"]),
                "spk": w.get("speaker"),
            })
    words.sort(key=lambda x: x["s"])

    utts, cur = [], []

    def flush():
        if not cur:
            return
        start, end = cur[0]["s"], cur[-1]["e"]
        dur = end - start
        if MIN_UTTERANCE_S <= dur <= MAX_UTTERANCE_S:
            utts.append({
                "speaker": cur[0]["spk"],
                "start": start,
                "end": end,
                "duration": dur,
                "text": " ".join(x["w"] for x in cur).strip(),
                "words": len(cur),
            })

    for w in words:
        if cur:
            same_spk = w["spk"] == cur[0]["spk"]
            gap = w["s"] - cur[-1]["e"]
            too_long = (w["e"] - cur[0]["s"]) > MAX_UTTERANCE_S
            if not same_spk or gap > MAX_WORD_GAP_S or too_long:
                flush()
                cur = []
        cur.append(w)
    flush()
    return utts


def cmd_index(args, db):
    rows = db.execute(
        """
        SELECT e.* FROM episodes e
        JOIN jobs j ON j.episode_id = e.id AND j.stage='asr' AND j.status='done'
        WHERE e.json_path IS NOT NULL
        ORDER BY e.season, e.episode
        """
    ).fetchall()

    if not rows:
        sys.exit("No transcribed episodes found. Run `transcribe` first.")

    print(f"[index] rebuilding from {len(rows)} episode(s)...")
    db.execute("DELETE FROM utterances")

    total = 0
    for row in rows:
        try:
            result = json.loads(Path(row["json_path"]).read_text())
        except Exception as e:
            print(f"  [skip] {row['filename']}: {e}")
            continue

        utts = words_to_utterances(result)
        db.executemany(
            "INSERT INTO utterances"
            "(episode_id, season, episode, speaker, character, start_s, end_s, duration_s, text, word_count) "
            "VALUES (?,?,?,?,NULL,?,?,?,?,?)",
            [(row["id"], row["season"], row["episode"], u["speaker"],
              u["start"], u["end"], u["duration"], u["text"], u["words"]) for u in utts],
        )
        total += len(utts)

    db.commit()
    db.executescript(FTS_SCHEMA)
    db.commit()

    print(f"[index] {total} utterances indexed.\n")

    # Voicepack-viable slice: the window a WoT crew callout actually lives in.
    viable = db.execute(
        "SELECT COUNT(*) c FROM utterances WHERE duration_s BETWEEN 0.4 AND 2.0"
    ).fetchone()["c"]
    unattributed = db.execute(
        "SELECT COUNT(*) c FROM utterances WHERE speaker IS NULL"
    ).fetchone()["c"]

    print(f"  in callout window (0.4-2.0s): {viable}")
    print(f"  unattributed (no speaker):    {unattributed}")
    if unattributed == total:
        print("\n  *** EVERY utterance is unattributed. Diarization did not take. ***")

    print("\n  Try it:")
    print("    sqlite3 corpus.db \"SELECT speaker, printf('%.2f',duration_s), text \\")
    print("      FROM utterances WHERE id IN (SELECT rowid FROM utterances_fts \\")
    print("      WHERE utterances_fts MATCH 'fire') AND duration_s BETWEEN 0.4 AND 2.0 LIMIT 20;\"")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def cmd_status(args, db):
    total = db.execute("SELECT COUNT(*) c FROM episodes").fetchone()["c"]
    print(f"\nEpisodes: {total}\n")
    print(f"  {'stage':<12}{'done':>7}{'failed':>8}{'pending':>9}")
    print("  " + "-" * 36)
    for stage in STAGES:
        done = db.execute("SELECT COUNT(*) c FROM jobs WHERE stage=? AND status='done'",
                          (stage,)).fetchone()["c"]
        failed = db.execute("SELECT COUNT(*) c FROM jobs WHERE stage=? AND status='failed'",
                            (stage,)).fetchone()["c"]
        print(f"  {stage:<12}{done:>7}{failed:>8}{total - done - failed:>9}")

    fails = db.execute(
        "SELECT e.filename, j.stage, j.error FROM jobs j "
        "JOIN episodes e ON e.id=j.episode_id WHERE j.status='failed' LIMIT 15"
    ).fetchall()
    if fails:
        print("\nFailures:")
        for f in fails:
            print(f"  [{f['stage']}] {f['filename']}: {(f['error'] or '')[:90]}")

    utts = db.execute("SELECT COUNT(*) c FROM utterances").fetchone()["c"]
    if utts:
        print(f"\nUtterances indexed: {utts}")
    print()


# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="Episode corpus indexer (Stages 1-4).")
    p.add_argument("--workdir", default=str(Path.home() / "archer-vp" / "work"),
                   help="Working dir. Keep this on ext4, NOT under /mnt/.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan");       s.add_argument("source", help="Episode directory")
    sub.add_parser("demux")
    sub.add_parser("transcribe")
    sub.add_parser("diarize")
    sub.add_parser("index")
    sub.add_parser("status")

    args = p.parse_args()
    Path(args.workdir).mkdir(parents=True, exist_ok=True)
    db = connect(Path(args.workdir))

    {
        "scan": cmd_scan, "demux": cmd_demux, "transcribe": cmd_transcribe,
        "diarize": cmd_diarize, "index": cmd_index, "status": cmd_status,
    }[args.cmd](args, db)

    db.close()


if __name__ == "__main__":
    main()
