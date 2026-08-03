#!/usr/bin/env python3
"""
04_audition.py — Stage 4: audition ranked candidates and record your picks.

    sample            Cut a padded preview per top candidate, emit audition.html
                      (a keep/reject board grouped by pool).            [CPU]
    serve [--port]    Serve the board over HTTP (Range-capable — see below).
    --- YOU KEEP THE LINES YOU WANT IN THE BROWSER, EXPORT picks.json ---
    import <picks>    Load kept (pool, utterance) pairs into the picks table.

Previews are cut from the 16 kHz working WAV (fast, fine for review). The final
clips are cut later from the ORIGINAL source in Stage 5. Reads pool_candidates
from Stage 3, so run `03_match.py match` first.

Use the `serve` subcommand, NOT `python -m http.server`: the stdlib server
ignores HTTP Range requests, which leaves audio elements non-seekable in the
browser (currentTime clamps to 0), so the board's lead-in ▶ seek silently does
nothing. `serve` answers a byte range with 206 so seeking works.
"""

import argparse
import http.server
import json
import os
import re
import sqlite3
import sys
from functools import partial
from pathlib import Path

from project import load_project
import downstream


def connect(workdir):
    db = sqlite3.connect(Path(workdir) / "corpus.db", timeout=60)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    # head_s/tail_s: per-clip lead-in/lead-out deltas (signed seconds) dialed on
    # the board, added on top of CLEAN_PAD_S when Stage 5 cuts the final.
    db.execute("""CREATE TABLE IF NOT EXISTS picks (
        pool_id TEXT, utterance_id INTEGER,
        head_s REAL DEFAULT 0, tail_s REAL DEFAULT 0,
        PRIMARY KEY (pool_id, utterance_id))""")
    # migrate DBs that predate the timing columns (e.g. the shipped archer_wot)
    have = {r[1] for r in db.execute("PRAGMA table_info(picks)")}
    for col in ("head_s", "tail_s"):
        if col not in have:
            db.execute(f"ALTER TABLE picks ADD COLUMN {col} REAL DEFAULT 0")
    return db


def cmd_sample(args, db):
    proj = args.proj
    t = proj.TUNING
    meta = {p[0]: {"display": p[1], "suggested_char": p[2]} for p in proj.POOLS}

    outdir = Path(proj.workdir) / "audition"
    outdir.mkdir(parents=True, exist_ok=True)

    rows = []
    total = 0
    for p in proj.POOLS:
        pid = p[0]
        cands = db.execute(
            """SELECT pc.utterance_id, pc.kw, pc.ph, pc.sem, u.start_s, u.end_s,
                      u.duration_s, u.character, u.text, e.wav_path
               FROM pool_candidates pc
               JOIN utterances u ON u.id = pc.utterance_id
               JOIN episodes e   ON e.id = u.episode_id
               WHERE pc.pool_id = ? AND e.wav_path IS NOT NULL
               ORDER BY pc.score DESC LIMIT ?""",
            (pid, t["AUDITION_TOP_N"])).fetchall()
        # Cut previews with a generous pad so the board can widen the play-window
        # for lead-in/lead-out tuning without re-cutting. clip_head = seconds of
        # preview before the line starts (floored at 0 near the file head).
        edit_pad = t["PREVIEW_EDIT_PAD_S"]
        for c in cands:
            name = f"{pid}_{c['utterance_id']}.wav"
            if not downstream.preview_clip(c["wav_path"], c["start_s"], c["end_s"],
                                           outdir / name, pad=edit_pad):
                continue
            rows.append({
                "pool_id": pid, "display": meta[pid]["display"],
                "utterance_id": c["utterance_id"], "character": c["character"],
                "duration_s": c["duration_s"], "text": c["text"],
                "kw": c["kw"], "ph": c["ph"] or 0, "sem": c["sem"],
                "preview": f"audition/{name}",
                "clip_head": min(edit_pad, c["start_s"]),
            })
            total += 1
        print(f"[audition] {pid}: {len(cands)} candidates")

    # seed the board with the current picks (kept-state + saved deltas) so a
    # re-sample opens ready to retune, independent of browser localStorage.
    seed = {f"{r['pool_id']}|{r['utterance_id']}": {"h": r["head_s"] or 0.0,
                                                    "t": r["tail_s"] or 0.0}
            for r in db.execute("SELECT pool_id, utterance_id, head_s, tail_s FROM picks")}
    (Path(proj.workdir) / "audition.html").write_text(
        downstream.build_audition_board(rows, base_pad=t["CLEAN_PAD_S"],
                                        step_s=t["NUDGE_STEP_S"], seed=seed))
    print(f"\n[audition] {total} previews across {len(proj.POOLS)} pools")
    print(f"  python pipeline/04_audition.py --project {args.project} serve")
    print("  open http://localhost:8000/audition.html")
    print("  ... keep + tune the lines you want, export picks.json, then:")
    print(f"  python pipeline/04_audition.py --project {args.project} import picks.json")


class _RangeHandler(http.server.SimpleHTTPRequestHandler):
    """Static handler that honors a single HTTP byte range with 206. The stdlib
    handler ignores Range and returns 200+full body, which marks media elements
    non-seekable in the browser (currentTime clamps to 0) — breaking the board's
    lead-in ▶ seek. Everything else falls through to the stdlib behavior."""

    protocol_version = "HTTP/1.1"

    def send_head(self):
        self._range_len = None
        rng = self.headers.get("Range")
        path = self.translate_path(self.path)
        if not rng or not os.path.isfile(path):
            return super().send_head()
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
        if not m:
            return super().send_head()
        size = os.path.getsize(path)
        g1, g2 = m.group(1), m.group(2)
        if g1 == "":                       # suffix range: last N bytes
            if g2 == "":
                return super().send_head()
            start, end = max(0, size - int(g2)), size - 1
        else:
            start = int(g1)
            end = min(int(g2), size - 1) if g2 else size - 1
        if start > end or start >= size:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        f = open(path, "rb")
        f.seek(start)
        self._range_len = end - start + 1
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(self._range_len))
        self.end_headers()
        return f

    def copyfile(self, source, outputfile):
        if self._range_len is None:
            return super().copyfile(source, outputfile)
        remaining = self._range_len          # send exactly the requested slice
        while remaining > 0:
            chunk = source.read(min(64 * 1024, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)

    def log_message(self, *a):
        pass                                 # quiet; this is an interactive tool


def cmd_serve(args):
    root = str(args.proj.workdir)
    handler = partial(_RangeHandler, directory=root)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"[audition] serving {root} (Range-capable)")
    print(f"  open http://localhost:{args.port}/audition.html   (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[audition] stopped.")
    finally:
        httpd.server_close()


def cmd_import(args, db):
    picks = json.loads(Path(args.picksfile).read_text())
    if not isinstance(picks, list):
        sys.exit("picks.json must be a list of {pool_id, utterance_id}.")
    db.execute("DELETE FROM picks")
    # head_s/tail_s optional: pre-tuning picks.json files (two fields) still load.
    db.executemany(
        "INSERT OR IGNORE INTO picks(pool_id, utterance_id, head_s, tail_s) VALUES (?,?,?,?)",
        [(p["pool_id"], p["utterance_id"],
          float(p.get("head_s", 0.0)), float(p.get("tail_s", 0.0))) for p in picks])
    db.commit()
    print(f"[audition] imported {len(picks)} picks.")
    for r in db.execute("SELECT pool_id, COUNT(*) n FROM picks GROUP BY pool_id ORDER BY pool_id"):
        print(f"  {r['pool_id']:<18}{r['n']:>4}")


def main():
    p = argparse.ArgumentParser(description="Audition candidates, record picks (Stage 4).")
    p.add_argument("--project", default="archer_wot")
    p.add_argument("--workdir", default=None, help="Override (default: work/<project>/).")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sample")
    sv = sub.add_parser("serve"); sv.add_argument("--port", type=int, default=8000)
    im = sub.add_parser("import"); im.add_argument("picksfile")
    args = p.parse_args()

    args.proj = load_project(args.project, args.workdir)
    args.proj.workdir.mkdir(parents=True, exist_ok=True)
    if args.cmd == "serve":            # long-running, no DB needed
        cmd_serve(args)
        return
    db = connect(args.proj.workdir)
    {"sample": cmd_sample, "import": cmd_import}[args.cmd](args, db)
    db.close()


if __name__ == "__main__":
    main()
