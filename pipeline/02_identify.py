#!/usr/bin/env python3
"""
02_identify.py — Stage 2: per-utterance reference tagging + neural centroids + assignment.

Why this replaces the clip approach: bundling utterances into clips made
contamination contagious — one misfiled line forced you to skip the whole
cluster. Here you approve or skip INDIVIDUAL lines. A misfiled line costs one
skip, not a cluster. You are the filter, at the granularity where your ears
actually work (no MFCC / neural auto-purity — we proved neither separates
Krieger/Cyril/Pam at utterance length).

Pipeline:
    sample     Cut one clip per candidate utterance; emit a per-line tagger,
               grouped by cluster (same voice adjacent) for fast approval. [CPU]
    --- YOU APPROVE INDIVIDUAL LINES IN THE BROWSER, EXPORT refs.json ---
    embed      GPU: neural-embed every approved line, build 8 centroids.   [GPU]
    assign     GPU: embed isolated lines per cluster, average -> cluster
               vector, match to centroids. Merged clusters average to a
               blend that matches nothing -> fall below threshold -> NULL,
               instead of mislabeling. Then --dry-run to pick the threshold.

Everything is in ONE embedding space (wespeaker-voxceleb-resnet34-LM), so
references and assignment targets are actually comparable.
"""

import argparse
import json
import math
import os
import sqlite3
import sys
import wave
from pathlib import Path

import numpy as np

CHARACTERS = ["Archer", "Lana", "Malory", "Cyril", "Pam", "Cheryl", "Krieger", "Ray"]

BANDS = [("S01-S03", 1, 3), ("S04-S07", 4, 7), ("S08-S11", 8, 11), ("S12-S14", 12, 14)]

EMBED_MODEL = "pyannote/wespeaker-voxceleb-resnet34-LM"   # verified to load

# --- sampling for the tagger ---
EPISODES_PER_BAND = 8
CLUSTERS_PER_EPISODE = 8
UTTS_PER_CLUSTER = 6          # individual lines offered per cluster
TALK_FLOOR_S = 60.0          # skip bit-player clusters (overflow lives in the deep ranks)
UTT_MIN_S = 0.8
UTT_MAX_S = 3.0
UTT_MIN_WORDS = 3
ISOLATION_PAD_S = 0.5
MIN_SNR_DB = 8.0             # per-line cleanliness floor

# --- assignment ---
CLUSTER_EMBED_UTTS = 12      # isolated lines averaged to form each cluster's target vector
DEFAULT_THRESHOLD = 0.50

WORKDIR_DEFAULT = str(Path(__file__).resolve().parent.parent / "work")


def connect(workdir):
    db = sqlite3.connect(Path(workdir) / "corpus.db", timeout=60)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS utt_pool (
            id INTEGER PRIMARY KEY, episode_id INTEGER, speaker TEXT,
            band TEXT, start_s REAL, end_s REAL, snr REAL, text TEXT, preview TEXT);
        CREATE TABLE IF NOT EXISTS refs_utt (
            episode_id INTEGER, speaker TEXT, start_s REAL,
            character TEXT, PRIMARY KEY (episode_id, speaker, start_s));
        CREATE TABLE IF NOT EXISTS centroids (
            character TEXT PRIMARY KEY, vector TEXT, n INTEGER, bands TEXT, tight REAL);
        CREATE TABLE IF NOT EXISTS clusters (
            episode_id INTEGER, speaker TEXT, character TEXT, similarity REAL,
            PRIMARY KEY (episode_id, speaker));
    """)
    return db


def band_of(season):
    for name, lo, hi in BANDS:
        if lo <= season <= hi:
            return name
    return None


# ---------------------------------------------------------------------------
# audio
# ---------------------------------------------------------------------------

def load_wav_np(path):
    with wave.open(str(path), "rb") as w:
        p = w.getparams()
        f = w.readframes(w.getnframes())
    return np.frombuffer(f, dtype=np.int16).astype(np.float32) / 32768.0, p


def _rms(x):
    return float(np.sqrt(np.mean(x * x))) if x.size else 0.0


def snr_db(audio, fr, s, e, pad=0.30):
    n = audio.size
    a, b = int(s * fr), int(e * fr)
    sig = audio[max(0, a):min(n, b)]
    if sig.size == 0:
        return -99.0
    pre = audio[max(0, a - int(pad * fr)):a]
    post = audio[b:min(n, b + int(pad * fr))]
    bgs = [_rms(x) for x in (pre, post) if x.size]
    if not bgs:
        return -99.0
    r = _rms(sig)
    return 20.0 * math.log10(r / (min(bgs) + 1e-6)) if r > 0 else -99.0


def isolated(db, eid, spk, s, e, pad=ISOLATION_PAD_S):
    return db.execute(
        "SELECT COUNT(*) c FROM utterances WHERE episode_id=? AND IFNULL(speaker,'')!=? "
        "AND start_s<? AND end_s>?", (eid, spk, e + pad, s - pad)).fetchone()["c"] == 0


def cut_clip(audio, params, s, e, out_path):
    fr = params.framerate
    seg = audio[int(s * fr):int(e * fr)]
    if seg.size == 0:
        return False
    pcm = (np.clip(seg, -1, 1) * 32767).astype(np.int16).tobytes()
    with wave.open(str(out_path), "wb") as w:
        w.setparams(params)
        w.writeframes(pcm)
    return True


# ---------------------------------------------------------------------------
# sample  (CPU) — one clip per utterance
# ---------------------------------------------------------------------------

def cmd_sample(args, db):
    prev = Path(args.workdir) / "utt_previews"
    prev.mkdir(parents=True, exist_ok=True)
    db.execute("DELETE FROM utt_pool")

    uid = 0
    for band_name, lo, hi in BANDS:
        eps = db.execute(
            "SELECT id, season, episode, wav_path FROM episodes "
            "WHERE season BETWEEN ? AND ? AND wav_path IS NOT NULL ORDER BY season, episode",
            (lo, hi)).fetchall()
        if not eps:
            continue
        step = max(1, len(eps) // EPISODES_PER_BAND)
        chosen = eps[::step][:EPISODES_PER_BAND]
        print(f"[sample] {band_name}: {len(chosen)} episodes")

        for ep in chosen:
            audio, params = load_wav_np(ep["wav_path"])
            fr = params.framerate
            clusters = db.execute(
                "SELECT speaker, SUM(duration_s) talk FROM utterances "
                "WHERE episode_id=? AND speaker IS NOT NULL "
                "GROUP BY speaker HAVING talk >= ? ORDER BY talk DESC LIMIT ?",
                (ep["id"], TALK_FLOOR_S, CLUSTERS_PER_EPISODE)).fetchall()

            for c in clusters:
                cands = db.execute(
                    "SELECT start_s, end_s, text FROM utterances "
                    "WHERE episode_id=? AND speaker=? AND duration_s BETWEEN ? AND ? "
                    "AND word_count >= ?",
                    (ep["id"], c["speaker"], UTT_MIN_S, UTT_MAX_S, UTT_MIN_WORDS)).fetchall()

                scored = []
                for u in cands:
                    if not isolated(db, ep["id"], c["speaker"], u["start_s"], u["end_s"]):
                        continue
                    q = snr_db(audio, fr, u["start_s"], u["end_s"])
                    if q >= MIN_SNR_DB:
                        scored.append((q, u))
                scored.sort(key=lambda x: -x[0])

                for q, u in scored[:UTTS_PER_CLUSTER]:
                    out = prev / f"u{uid:05d}.wav"
                    if not cut_clip(audio, params, u["start_s"], u["end_s"], out):
                        continue
                    db.execute(
                        "INSERT INTO utt_pool VALUES (?,?,?,?,?,?,?,?,?)",
                        (uid, ep["id"], c["speaker"], band_name,
                         u["start_s"], u["end_s"], q, u["text"], f"utt_previews/{out.name}"))
                    uid += 1
    db.commit()

    rows = db.execute(
        "SELECT up.*, e.season, e.episode FROM utt_pool up JOIN episodes e ON e.id=up.episode_id "
        "ORDER BY e.season, e.episode, up.speaker, up.snr DESC").fetchall()
    (Path(args.workdir) / "tagger_utt.html").write_text(build_tagger([dict(r) for r in rows]))

    print(f"\n[sample] {uid} individual utterances across {len(set((r['episode_id'],r['speaker']) for r in rows))} clusters")
    print("  cd work && python -m http.server 8000")
    print("  open http://localhost:8000/tagger_utt.html")


def build_tagger(rows):
    return (TAGGER_HTML
            .replace("__DATA__", json.dumps(rows))
            .replace("__CHARS__", json.dumps(CHARACTERS))
            .replace("__BANDS__", json.dumps([b[0] for b in BANDS])))


TAGGER_HTML = r"""<!doctype html>
<meta charset="utf-8"><title>Utterance Tagger</title>
<style>
  body{background:#14161a;color:#e6e6e6;font:14px/1.4 ui-monospace,Menlo,Consolas,monospace;margin:0;padding:18px 22px 130px}
  h1{font-size:16px;margin:0 0 4px;letter-spacing:.5px}
  .sub{color:#8a8f98;margin-bottom:12px;max-width:920px}
  #ctl{display:flex;gap:14px;align-items:center;margin-bottom:12px;padding:9px 12px;background:#181b20;border:1px solid #262a31;border-radius:4px;flex-wrap:wrap}
  #ctl label{color:#a8adb6;cursor:pointer}
  #ctl select{background:#20242b;color:#e6e6e6;border:1px solid #333842;border-radius:3px;padding:4px 8px;font:inherit}
  #count{color:#5a5f68;margin-left:auto}
  table{border-collapse:collapse;width:100%}
  td,th{border-bottom:1px solid #23272e;padding:6px 6px;text-align:left;vertical-align:middle}
  th{color:#8a8f98;font-weight:400;font-size:11px;text-transform:uppercase;letter-spacing:.5px}
  tr.clusterhead td{background:#1a1d23;color:#7fd0ff;font-size:12px;border-top:2px solid #2a2f38;padding-top:9px}
  audio{height:28px;width:200px}
  .txt{color:#b4b9c2;font-size:12px;max-width:440px}
  .snr{font-size:11px;color:#5fd07a}.snr.mid{color:#d0c05f}.snr.low{color:#d07a5f}
  tr.tagged{background:#172017}
  button.c{background:#20242b;color:#e6e6e6;border:1px solid #333842;border-radius:3px;padding:3px 6px;margin:1px;cursor:pointer;font:inherit;font-size:11px}
  button.c:hover{background:#2c313a}
  button.c.on{background:#2f6f3f;border-color:#3f8f52}
  button.skip{color:#8a8f98}
  #hud{position:fixed;bottom:0;left:0;right:0;background:#0f1114;border-top:1px solid #262a31;padding:9px 22px;display:flex;gap:18px;align-items:center;flex-wrap:wrap}
  .cc{font-size:12px}.cc b{color:#e6e6e6}.cc.done b{color:#5fd07a}
  .cc .b{color:#5a5f68}.cc .b.hit{color:#5fd07a}
  #exp{margin-left:auto;background:#2f6f3f;border:1px solid #3f8f52;color:#fff;padding:7px 15px;border-radius:3px;cursor:pointer;font:inherit}
</style>
<h1>UTTERANCE TAGGER</h1>
<div class="sub">One line per row, grouped by cluster (blue headers). Play the first line of a cluster to ID the voice,
then approve the clean lines and <b>skip only the misfiled ones</b>. A bad line costs one skip, not the cluster.
Aim for 12+ approved lines per character across 3+ bands.</div>
<div id="ctl">
  <label><input type="checkbox" id="fUn"> untagged only</label>
  <label>band <select id="fBand"><option value="">all</option></select></label>
  <span id="count"></span>
</div>
<table id="t"><tbody></tbody></table>
<div id="hud"></div>
<script>
const ROWS=__DATA__, CHARS=__CHARS__, BANDS=__BANDS__;
const KEY="archer_utt_v1";
let tags=JSON.parse(localStorage.getItem(KEY)||"{}");

function render(){
  const tb=document.querySelector("#t tbody"); tb.innerHTML="";
  const onlyNew=document.getElementById("fUn").checked;
  const band=document.getElementById("fBand").value;
  let shown=0, lastCluster=null;
  for(const r of ROWS){
    if(band && r.band!==band) continue;
    const cur=tags[r.id];
    if(onlyNew && cur) continue;
    const ck=r.episode_id+":"+r.speaker;
    if(ck!==lastCluster){
      lastCluster=ck;
      const hr=document.createElement("tr"); hr.className="clusterhead";
      const ep="S"+String(r.season).padStart(2,"0")+"E"+String(r.episode).padStart(2,"0");
      hr.innerHTML=`<td colspan="5">${ep} &middot; ${r.speaker} &middot; ${r.band}</td>`;
      tb.appendChild(hr);
    }
    shown++;
    const tr=document.createElement("tr"); if(cur) tr.className="tagged";
    const cls=r.snr>=14?"":(r.snr>=9?" mid":" low");
    const btns=CHARS.map(c=>`<button class="c ${cur===c?"on":""}" data-i="${r.id}" data-c="${c}">${c}</button>`).join("")
      +`<button class="c skip ${cur==="_skip"?"on":""}" data-i="${r.id}" data-c="_skip">skip</button>`;
    tr.innerHTML=`<td class="snr${cls}">${r.snr.toFixed(0)}dB</td>`
      +`<td><audio controls preload="none" src="${r.preview}"></audio></td>`
      +`<td class="txt">${(r.text||"").replace(/</g,"&lt;").slice(0,120)}</td>`
      +`<td>${btns}</td>`;
    tb.appendChild(tr);
  }
  const ok=Object.values(tags).filter(v=>v!=="_skip").length;
  const sk=Object.values(tags).filter(v=>v==="_skip").length;
  document.getElementById("count").textContent=`${shown} shown \u2014 ${ok} approved, ${sk} skipped`;
  hud();
}
function hud(){
  const h=document.getElementById("hud"); h.innerHTML="";
  for(const c of CHARS){
    const mine=ROWS.filter(r=>tags[r.id]===c);
    const hit=new Set(mine.map(r=>r.band));
    const ok=mine.length>=12 && hit.size>=3;
    const d=document.createElement("div"); d.className="cc"+(ok?" done":"");
    d.innerHTML=`<b>${c}</b> ${mine.length} `+BANDS.map(b=>`<span class="b ${hit.has(b)?"hit":""}">${b.slice(1,3)}</span>`).join("");
    h.appendChild(d);
  }
  const b=document.createElement("button"); b.id="exp"; b.textContent="Export refs.json";
  b.onclick=()=>{
    const byId={}; for(const r of ROWS) byId[r.id]=r;
    const out=Object.entries(tags).filter(([,c])=>c!=="_skip").map(([id,c])=>{
      const r=byId[id]; return {episode_id:r.episode_id, speaker:r.speaker, start_s:r.start_s, character:c};});
    const a=document.createElement("a");
    a.href=URL.createObjectURL(new Blob([JSON.stringify(out,null,2)],{type:"application/json"}));
    a.download="refs.json"; a.click();
  };
  h.appendChild(b);
}
document.addEventListener("click",e=>{
  const b=e.target.closest("button.c"); if(!b) return;
  const id=b.dataset.i, c=b.dataset.c;
  if(tags[id]===c) delete tags[id]; else tags[id]=c;
  localStorage.setItem(KEY,JSON.stringify(tags)); render();
});
const sel=document.getElementById("fBand");
for(const b of BANDS){const o=document.createElement("option");o.value=o.textContent=b;sel.appendChild(o);}
document.getElementById("fUn").onchange=render; sel.onchange=render;
render();
</script>
"""


# ---------------------------------------------------------------------------
# embedding backend (shared by embed + assign)
# ---------------------------------------------------------------------------

def make_embedder():
    import torch
    from pyannote.audio import Model, Inference
    tok = os.environ.get("HF_TOKEN")
    if not tok:
        sys.exit("HF_TOKEN not set.")
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = Model.from_pretrained(EMBED_MODEL, use_auth_token=tok)
    model.to(torch.device(dev)).eval()
    inf = Inference(model, window="whole", device=torch.device(dev))
    print(f"[embed] model loaded on {dev}")

    def embed(sig, sr):
        wf = torch.from_numpy(sig).float().unsqueeze(0)
        v = np.asarray(inf({"waveform": wf, "sample_rate": sr}), dtype=np.float32).reshape(-1)
        n = np.linalg.norm(v)
        return v / n if n > 0 else v
    return embed


def load_span(wav, s, e):
    with wave.open(str(wav), "rb") as w:
        fr = w.getframerate()
        w.setpos(int(s * fr))
        raw = w.readframes(int((e - s) * fr))
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0, fr


# ---------------------------------------------------------------------------
# embed  (GPU) — build centroids from approved individual lines
# ---------------------------------------------------------------------------

def cmd_embed(args, db):
    refs = json.loads(Path(args.reffile).read_text())
    db.execute("DELETE FROM refs_utt")
    db.executemany("INSERT OR REPLACE INTO refs_utt VALUES (?,?,?,?)",
                   [(r["episode_id"], r["speaker"], r["start_s"], r["character"]) for r in refs])
    db.commit()
    print(f"[embed] {len(refs)} approved reference lines")

    embed = make_embedder()
    ep_wav = {r["id"]: r["wav_path"] for r in db.execute("SELECT id, wav_path FROM episodes")}

    by_char = {}
    for r in refs:
        wav = ep_wav.get(r["episode_id"])
        if not wav:
            continue
        # need end_s; re-derive from the utterance table by (episode,speaker,start)
        u = db.execute(
            "SELECT end_s FROM utterances WHERE episode_id=? AND speaker=? AND start_s=? LIMIT 1",
            (r["episode_id"], r["speaker"], r["start_s"])).fetchone()
        if not u:
            continue
        sig, sr = load_span(wav, r["start_s"], u["end_s"])
        v = embed(sig, sr)
        se = db.execute("SELECT season FROM episodes WHERE id=?", (r["episode_id"],)).fetchone()["season"]
        by_char.setdefault(r["character"], []).append((v, band_of(se)))

    db.execute("DELETE FROM centroids")
    print(f"\n{'character':<10}{'n':>4}  {'tight':<7} bands")
    for ch in CHARACTERS:
        items = by_char.get(ch, [])
        if not items:
            print(f"{ch:<10}{0:>4}  *** NO REFERENCES ***")
            continue
        M = np.stack([v for v, _ in items])
        c = M.mean(axis=0); c /= np.linalg.norm(c)
        tight = float((M @ c).mean())
        bands = sorted({b for _, b in items if b})
        db.execute("INSERT INTO centroids VALUES (?,?,?,?,?)",
                   (ch, json.dumps(c.tolist()), len(items), ",".join(bands), tight))
        warn = "  <-- thin" if (len(items) < 8 or len(bands) < 3) else ("  <-- LOOSE (mis-tag?)" if tight < 0.45 else "")
        print(f"{ch:<10}{len(items):>4}  {tight:<7.3f} {','.join(bands)}{warn}")
    db.commit()
    print("\n[embed] done. Next: python stage5b.py assign --dry-run")


# ---------------------------------------------------------------------------
# assign  (GPU) — cluster vectors = mean of isolated-line embeddings
# ---------------------------------------------------------------------------

def cmd_assign(args, db):
    crows = db.execute("SELECT character, vector FROM centroids").fetchall()
    if not crows:
        sys.exit("No centroids. Run `embed` first.")
    names = [r["character"] for r in crows]
    C = np.stack([np.asarray(json.loads(r["vector"]), dtype=np.float32) for r in crows])

    embed = make_embedder()
    ep_wav = {r["id"]: r["wav_path"] for r in db.execute("SELECT id, wav_path FROM episodes")}

    all_clusters = db.execute(
        "SELECT DISTINCT episode_id, speaker FROM utterances WHERE speaker IS NOT NULL").fetchall()
    print(f"[assign] embedding {len(all_clusters)} clusters "
          f"(up to {CLUSTER_EMBED_UTTS} isolated lines each)...")

    keys, vecs = [], []
    for i, cl in enumerate(all_clusters, 1):
        eid, spk = cl["episode_id"], cl["speaker"]
        wav = ep_wav.get(eid)
        if not wav:
            continue
        us = db.execute(
            "SELECT start_s, end_s FROM utterances WHERE episode_id=? AND speaker=? "
            "AND duration_s BETWEEN ? AND ? AND word_count >= ? ORDER BY duration_s DESC LIMIT ?",
            (eid, spk, UTT_MIN_S, UTT_MAX_S, UTT_MIN_WORDS, CLUSTER_EMBED_UTTS * 2)).fetchall()
        evs = []
        for u in us:
            if len(evs) >= CLUSTER_EMBED_UTTS:
                break
            if not isolated(db, eid, spk, u["start_s"], u["end_s"]):
                continue
            sig, sr = load_span(wav, u["start_s"], u["end_s"])
            evs.append(embed(sig, sr))
        if not evs:
            continue
        m = np.mean(evs, axis=0); n = np.linalg.norm(m)
        if n == 0:
            continue
        keys.append((eid, spk)); vecs.append(m / n)
        if i % 100 == 0:
            print(f"[assign] {i}/{len(all_clusters)}")

    V = np.stack(vecs)
    S = V @ C.T
    best = S.argmax(axis=1); sim = S.max(axis=1)
    part = np.partition(S, -2, axis=1); margin = part[:, -1] - part[:, -2]

    print(f"\n{len(keys)} clusters embedded.\nsimilarity distribution:")
    for lo in np.arange(0.0, 1.0, 0.05):
        n = int(((sim >= lo) & (sim < lo + 0.05)).sum())
        if n:
            print(f"  {lo:.2f}-{lo+0.05:.2f}  {n:>5}  {'#'*min(60, n//3)}")
    print(f"\n  median sim {np.median(sim):.3f}  median margin {np.median(margin):.3f}")

    keep = sim >= args.threshold
    print(f"\nat threshold {args.threshold}:")
    for i, nm in enumerate(names):
        print(f"  {nm:<10}{int(((best==i)&keep).sum()):>5}")
    print(f"  {'(NULL)':<10}{int((~keep).sum()):>5}")

    if args.dry_run:
        print("\n[dry-run] nothing written.")
        return

    db.execute("DELETE FROM clusters")
    db.execute("UPDATE utterances SET character=NULL")
    db.executemany("INSERT INTO clusters VALUES (?,?,?,?)",
                   [(k[0], k[1], (names[best[i]] if keep[i] else None), float(sim[i]))
                    for i, k in enumerate(keys)])
    db.execute("""UPDATE utterances SET character=(
        SELECT c.character FROM clusters c
        WHERE c.episode_id=utterances.episode_id AND c.speaker=utterances.speaker)""")
    db.commit()
    print("\nattributed:")
    for r in db.execute("SELECT COALESCE(character,'(none)') ch, COUNT(*) n, "
                        "SUM(duration_s BETWEEN 0.4 AND 2.0) callout FROM utterances "
                        "GROUP BY ch ORDER BY n DESC"):
        print(f"  {r['ch']:<10}{r['n']:>7}  ({r['callout']} callout)")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--workdir", default=WORKDIR_DEFAULT)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sample")
    e = sub.add_parser("embed"); e.add_argument("reffile")
    a = sub.add_parser("assign")
    a.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    a.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    db = connect(args.workdir)
    {"sample": cmd_sample, "embed": cmd_embed, "assign": cmd_assign}[args.cmd](args, db)
    db.close()


if __name__ == "__main__":
    main()
