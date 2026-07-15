#!/usr/bin/env python3
"""
downstream.py — shared primitives for the downstream stages (audition, clean,
package). Kept here so a project's own package.py can compose these rather than
reinvent clip-cutting, cleaning, or manifest writing.

No GPU, no models. ffmpeg on PATH is required (same as Stage 1 demux).
"""

import json
import subprocess
from pathlib import Path


def _ffmpeg(args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args],
                   check=True, capture_output=True)


def cut_span(src, start_s, end_s, out_wav, pad=0.0, ar=None):
    """Extract [start-pad, end+pad] seconds from src into out_wav (mono PCM).

    Fast input seek (-ss before -i) so extracting a short span from a large
    source video doesn't decode from the top — important for original-source
    cuts on multi-GB files. `ar` resamples (e.g. 16000 for previews); omit to
    keep the source rate (full quality for finals).
    """
    ss = max(0.0, start_s - pad)
    dur = (end_s + pad) - ss
    if dur <= 0:
        return False
    args = ["-ss", f"{ss:.3f}", "-i", str(src), "-t", f"{dur:.3f}", "-vn", "-ac", "1"]
    if ar:
        args += ["-ar", str(ar)]
    args += ["-c:a", "pcm_s16le", str(out_wav)]
    _ffmpeg(args)
    return Path(out_wav).exists()


def preview_clip(wav_path, start_s, end_s, out_wav, pad):
    """Fast padded preview from the 16 kHz working WAV (for the audition board)."""
    return cut_span(wav_path, start_s, end_s, out_wav, pad=pad, ar=16000)


def cut_from_source(src_video, start_s, end_s, out_wav, pad):
    """Full-quality span from the ORIGINAL source (native rate) — for finals."""
    return cut_span(src_video, start_s, end_s, out_wav, pad=pad, ar=None)


def clean_audio(in_wav, out_wav, lufs=-16.0, bandpass_hz=None, fade_ms=15, ar=48000):
    """loudnorm + optional bandpass + symmetric fades, via one ffmpeg pass.

    Deliberately does NOT silence-trim: the input is already tight (word-level
    boundaries) with intentional CLEAN_PAD_S head/tail for a natural sound, and
    aggressive trimming both removes that padding and can gut quieter clips.
    """
    chain = []
    if bandpass_hz:
        lo, hi = bandpass_hz
        chain.append(f"highpass=f={lo},lowpass=f={hi}")
    chain.append(f"loudnorm=I={lufs}:TP=-1.5:LRA=11")
    if fade_ms:
        f = fade_ms / 1000.0
        # fade both ends without needing total duration (reverse trick).
        chain.append(f"afade=t=in:st=0:d={f},areverse,afade=t=in:st=0:d={f},areverse")
    _ffmpeg(["-i", str(in_wav), "-af", ",".join(chain), "-ac", "1", "-ar", str(ar),
             "-c:a", "pcm_s16le", str(out_wav)])
    return Path(out_wav).exists()


def write_manifest(entries, pools, pool_events, out_path):
    """Write the package manifest: one block per pool, with its wired game events
    and the cleaned clips selected for it.

    entries: list of dicts {pool_id, file, character, text, source}
    pools:   {pool_id: {"display":..., "suggested_char":...}}
    pool_events: {pool_id: [game_event, ...]}
    """
    by_pool = {}
    for e in entries:
        by_pool.setdefault(e["pool_id"], []).append(e)

    manifest = []
    for pid, meta in pools.items():
        clips = by_pool.get(pid, [])
        manifest.append({
            "pool_id": pid,
            "display": meta.get("display"),
            "suggested_char": meta.get("suggested_char"),
            "game_events": pool_events.get(pid, []),
            "clip_count": len(clips),
            "clips": [{"file": c["file"], "character": c["character"],
                       "text": c["text"], "source": c["source"]} for c in clips],
        })
    out = {"pools": manifest,
           "total_clips": sum(len(v) for v in by_pool.values())}
    Path(out_path).write_text(json.dumps(out, indent=2))
    return out


# ---------------------------------------------------------------------------
# audition board (mirrors the Stage 2 tagger: local-served HTML, localStorage,
# export JSON). Keep/reject per candidate, grouped by pool.
# ---------------------------------------------------------------------------

def build_audition_board(rows):
    return AUDITION_HTML.replace("__DATA__", json.dumps(rows))


AUDITION_HTML = r"""<!doctype html>
<meta charset="utf-8"><title>Audition</title>
<style>
  body{background:#14161a;color:#e6e6e6;font:14px/1.4 ui-monospace,Menlo,Consolas,monospace;margin:0;padding:18px 22px 130px}
  h1{font-size:16px;margin:0 0 4px;letter-spacing:.5px}
  .sub{color:#8a8f98;margin-bottom:12px;max-width:920px}
  #ctl{display:flex;gap:14px;align-items:center;margin-bottom:12px;padding:9px 12px;background:#181b20;border:1px solid #262a31;border-radius:4px;flex-wrap:wrap}
  #ctl label{color:#a8adb6;cursor:pointer}#count{color:#5a5f68;margin-left:auto}
  table{border-collapse:collapse;width:100%}
  td,th{border-bottom:1px solid #23272e;padding:6px 6px;text-align:left;vertical-align:middle}
  tr.poolhead td{background:#1a1d23;color:#7fd0ff;font-size:12px;border-top:2px solid #2a2f38;padding-top:9px}
  audio{height:28px;width:220px}
  .txt{color:#b4b9c2;font-size:12px;max-width:460px}
  .who{color:#8a8f98;font-size:11px}.sc{font-size:11px;color:#5fd07a}.star{color:#d0c05f}
  tr.kept{background:#172017}
  button.k{background:#20242b;color:#e6e6e6;border:1px solid #333842;border-radius:3px;padding:3px 10px;cursor:pointer;font:inherit;font-size:11px}
  button.k.on{background:#2f6f3f;border-color:#3f8f52}
  #hud{position:fixed;bottom:0;left:0;right:0;background:#0f1114;border-top:1px solid #262a31;padding:9px 22px;display:flex;gap:18px;align-items:center;flex-wrap:wrap}
  .cc{font-size:12px}.cc b{color:#e6e6e6}
  #exp{margin-left:auto;background:#2f6f3f;border:1px solid #3f8f52;color:#fff;padding:7px 15px;border-radius:3px;cursor:pointer;font:inherit}
</style>
<h1>AUDITION</h1>
<div class="sub">One row per ranked candidate, grouped by pool (blue headers). Play a line and <b>Keep</b> the ones you want in the pack;
leave the rest. A <span class="star">&#9733;</span> marks a keyword hit. Export picks.json when done, then run <code>04_audition import</code>.</div>
<div id="ctl">
  <label><input type="checkbox" id="fKept"> kept only</label>
  <span id="count"></span>
</div>
<table id="t"><tbody></tbody></table>
<div id="hud"></div>
<script>
const ROWS=__DATA__;
const KEY="audition_v1";
let picks=JSON.parse(localStorage.getItem(KEY)||"{}");   // key `${pool}|${uid}` -> 1

function render(){
  const tb=document.querySelector("#t tbody"); tb.innerHTML="";
  const keptOnly=document.getElementById("fKept").checked;
  let shown=0, last=null;
  for(const r of ROWS){
    const key=r.pool_id+"|"+r.utterance_id;
    const on=picks[key];
    if(keptOnly && !on) continue;
    if(r.pool_id!==last){
      last=r.pool_id;
      const hr=document.createElement("tr"); hr.className="poolhead";
      hr.innerHTML=`<td colspan="4">${r.pool_id} &middot; ${r.display||""}</td>`;
      tb.appendChild(hr);
    }
    shown++;
    const tr=document.createElement("tr"); if(on) tr.className="kept";
    const star=r.kw?` <span class="star">&#9733;</span>`:"";
    tr.innerHTML=`<td><button class="k ${on?"on":""}" data-k="${key}">${on?"kept":"keep"}</button></td>`
      +`<td><audio controls preload="none" src="${r.preview}"></audio></td>`
      +`<td class="txt">${(r.text||"").replace(/</g,"&lt;").slice(0,140)}<div class="who">${r.character} &middot; ${r.duration_s.toFixed(1)}s &middot; <span class="sc">sem ${r.sem.toFixed(2)}</span>${star}</div></td>`;
    tb.appendChild(tr);
  }
  document.getElementById("count").textContent=`${shown} shown — ${Object.keys(picks).length} kept`;
  hud();
}
function hud(){
  const h=document.getElementById("hud"); h.innerHTML="";
  const per={};
  for(const k of Object.keys(picks)){const p=k.split("|")[0]; per[p]=(per[p]||0)+1;}
  const pools=[...new Set(ROWS.map(r=>r.pool_id))];
  for(const p of pools){const d=document.createElement("div"); d.className="cc"; d.innerHTML=`<b>${p}</b> ${per[p]||0}`; h.appendChild(d);}
  const b=document.createElement("button"); b.id="exp"; b.textContent="Export picks.json";
  b.onclick=()=>{
    const out=Object.keys(picks).map(k=>{const [pool_id,uid]=k.split("|"); return {pool_id, utterance_id:Number(uid)};});
    const a=document.createElement("a");
    a.href=URL.createObjectURL(new Blob([JSON.stringify(out,null,2)],{type:"application/json"}));
    a.download="picks.json"; a.click();
  };
  h.appendChild(b);
}
document.addEventListener("click",e=>{
  const b=e.target.closest("button.k"); if(!b) return;
  const k=b.dataset.k;
  if(picks[k]) delete picks[k]; else picks[k]=1;
  localStorage.setItem(KEY,JSON.stringify(picks)); render();
});
document.getElementById("fKept").onchange=render;
render();
</script>
"""
