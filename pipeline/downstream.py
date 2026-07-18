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


def _span_bounds(start_s, end_s, pad_head, pad_tail):
    """(-ss, -t) for a padded span, floored at 0. Head/tail pads are independent
    so a per-clip lead-in/lead-out tweak (Stage 4) can widen or tighten one end
    without touching the other. Returns (ss, dur); dur<=0 means an empty span."""
    ss = max(0.0, start_s - pad_head)
    dur = (end_s + pad_tail) - ss
    return ss, dur


def cut_span(src, start_s, end_s, out_wav, pad=0.0, ar=None,
             pad_head=None, pad_tail=None):
    """Extract [start-pad_head, end+pad_tail] seconds from src into out_wav (mono
    PCM). `pad` sets both ends symmetrically; pass pad_head/pad_tail to override
    either end independently (each defaults to `pad`).

    Fast input seek (-ss before -i) so extracting a short span from a large
    source video doesn't decode from the top — important for original-source
    cuts on multi-GB files. `ar` resamples (e.g. 16000 for previews); omit to
    keep the source rate (full quality for finals).
    """
    if pad_head is None:
        pad_head = pad
    if pad_tail is None:
        pad_tail = pad
    ss, dur = _span_bounds(start_s, end_s, pad_head, pad_tail)
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


def cut_from_source(src_video, start_s, end_s, out_wav, pad=0.0,
                    pad_head=None, pad_tail=None):
    """Full-quality span from the ORIGINAL source (native rate) — for finals.
    pad_head/pad_tail carry Stage 4's per-clip lead-in/lead-out onto the cut."""
    return cut_span(src_video, start_s, end_s, out_wav, pad=pad, ar=None,
                    pad_head=pad_head, pad_tail=pad_tail)


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

def build_audition_board(rows, base_pad=0.10, step_s=0.05, seed=None):
    """Render the board. base_pad is the play-window pad that mirrors Stage 5's
    CLEAN_PAD_S (so the previewed window ≈ the final cut); step_s is the size of
    one lead-in/lead-out nudge. `seed` ({`pool|uid`: {h,t}}) pre-populates the
    board with the current DB picks so a re-sample opens ready to retune, rather
    than depending on whatever is in browser localStorage."""
    return (AUDITION_HTML
            .replace("__DATA__", json.dumps(rows))
            .replace("__BASEPAD__", json.dumps(base_pad))
            .replace("__STEP__", json.dumps(step_s))
            .replace("__SEED__", json.dumps(seed or {})))


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
  .tune{white-space:nowrap}
  button.nb,button.pw{background:#20242b;color:#cdd2da;border:1px solid #333842;border-radius:3px;padding:2px 6px;cursor:pointer;font:inherit;font-size:11px}
  button.pw{color:#7fd0ff;margin-left:6px}
  button.nb:disabled,button.pw:disabled{opacity:.32;cursor:default}
  .ro{color:#8a8f98;font-size:11px;margin:0 6px;display:inline-block;min-width:118px;text-align:center}
  .ro.set{color:#d0c05f}
  #hud{position:fixed;bottom:0;left:0;right:0;background:#0f1114;border-top:1px solid #262a31;padding:9px 22px;display:flex;gap:18px;align-items:center;flex-wrap:wrap}
  .cc{font-size:12px}.cc b{color:#e6e6e6}
  #exp{margin-left:auto;background:#2f6f3f;border:1px solid #3f8f52;color:#fff;padding:7px 15px;border-radius:3px;cursor:pointer;font:inherit}
</style>
<h1>AUDITION</h1>
<div class="sub">One row per ranked candidate, grouped by pool (blue headers). Play a line and <b>Keep</b> the ones you want in the pack;
leave the rest. A <span class="star">&#9733;</span> marks a keyword hit. On a kept line, nudge <b>lead-in</b> / <b>lead-out</b>
(each &plusmn;__STEP__s per click; may go negative to tighten) and hit <b>&#9654;</b> to hear just that window — it exports with the pick
and Stage 5 cuts to it. Export picks.json when done, then run <code>04_audition import</code>.</div>
<div id="ctl">
  <label><input type="checkbox" id="fKept"> kept only</label>
  <span id="count"></span>
</div>
<table id="t"><tbody></tbody></table>
<div id="hud"></div>
<script>
const ROWS=__DATA__;
const SEED=__SEED__;         // current DB picks: `${pool}|${uid}` -> {h,t}
const BASEPAD=__BASEPAD__;   // play-window pad, mirrors Stage 5 CLEAN_PAD_S
const STEP=__STEP__;         // one lead-in/lead-out nudge, seconds
const KEY="audition_v1";
// key `${pool}|${uid}` -> {h,t} lead-in/lead-out deltas (seconds, signed).
// Legacy values (a bare 1 from before per-clip tuning) read as {h:0,t:0}.
// First visit (no localStorage yet) adopts the DB picks so you open ready to
// retune; after that localStorage is authoritative and refreshes keep your edits.
let picks=JSON.parse(localStorage.getItem(KEY)||"null");
if(picks===null){picks=Object.assign({},SEED); localStorage.setItem(KEY,JSON.stringify(picks));}
const off=k=>{const v=picks[k]; if(v==null) return null;
  return (typeof v==="object")?{h:v.h||0,t:v.t||0}:{h:0,t:0};};
const ROW=new Map(ROWS.map(r=>[r.pool_id+"|"+r.utterance_id,r]));
// Keys on THIS board. Keeps persist in localStorage across re-samples, but if a
// pool was retuned its old picks reference lines no longer on the board — count
// and export only current-board keeps so stale picks can't ride along.
const ROWKEYS=new Set(ROW.keys());
const liveKeys=()=>Object.keys(picks).filter(k=>ROWKEYS.has(k));
const save=()=>localStorage.setItem(KEY,JSON.stringify(picks));
const fmt=x=>(x<0?"−":"+")+Math.abs(x).toFixed(2);

function render(){
  const tb=document.querySelector("#t tbody"); tb.innerHTML="";
  const keptOnly=document.getElementById("fKept").checked;
  let shown=0, last=null;
  for(const r of ROWS){
    const key=r.pool_id+"|"+r.utterance_id;
    const o=off(key), on=o!=null;
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
    const dis=on?"":" disabled";
    const roset=(on&&(o.h||o.t))?" set":"";
    const roTxt=on?`in ${fmt(o.h)} / out ${fmt(o.t)}`:"in +0.00 / out +0.00";
    tr.innerHTML=`<td><button class="k ${on?"on":""}" data-k="${key}">${on?"kept":"keep"}</button></td>`
      +`<td><audio controls preload="none" src="${r.preview}"></audio></td>`
      +`<td class="txt">${(r.text||"").replace(/</g,"&lt;").slice(0,140)}<div class="who">${r.character} &middot; ${r.duration_s.toFixed(1)}s &middot; <span class="sc">sem ${r.sem.toFixed(2)}</span>${star}</div></td>`
      +`<td class="tune">`
        +`<button class="nb" data-k="${key}" data-e="h" data-d="-1"${dis}>in&minus;</button>`
        +`<button class="nb" data-k="${key}" data-e="h" data-d="1"${dis}>in+</button>`
        +`<span class="ro${roset}" id="ro_${key}">${roTxt}</span>`
        +`<button class="nb" data-k="${key}" data-e="t" data-d="-1"${dis}>out&minus;</button>`
        +`<button class="nb" data-k="${key}" data-e="t" data-d="1"${dis}>out+</button>`
        +`<button class="pw" data-pw="${key}"${dis}>&#9654;</button></td>`;
    tb.appendChild(tr);
  }
  document.getElementById("count").textContent=`${shown} shown — ${liveKeys().length} kept`;
  hud();
}
function hud(){
  const h=document.getElementById("hud"); h.innerHTML="";
  const per={};
  for(const k of liveKeys()){const p=k.split("|")[0]; per[p]=(per[p]||0)+1;}
  const pools=[...new Set(ROWS.map(r=>r.pool_id))];
  for(const p of pools){const d=document.createElement("div"); d.className="cc"; d.innerHTML=`<b>${p}</b> ${per[p]||0}`; h.appendChild(d);}
  const b=document.createElement("button"); b.id="exp"; b.textContent="Export picks.json";
  b.onclick=()=>{
    const out=liveKeys().map(k=>{const [pool_id,uid]=k.split("|"); const o=off(k);
      return {pool_id, utterance_id:Number(uid), head_s:o.h, tail_s:o.t};});
    const a=document.createElement("a");
    a.href=URL.createObjectURL(new Blob([JSON.stringify(out,null,2)],{type:"application/json"}));
    a.download="picks.json"; a.click();
  };
  h.appendChild(b);
}
// Play just the effective window inside the generously-padded preview: the clip
// holds `clip_head` seconds before the line, so the window base is BASEPAD in
// from that, widened/tightened by the per-clip deltas.
// Windowed ▶ playback uses its OWN hidden audio, never the row's visible <audio>,
// so stopping it can't chop up full-clip playback on the native controls.
// Seeking needs a Range-capable server (use `04_audition serve`).
const winAudio=new Audio();
let winEndAt=null, winRAF=null;
// Stop via a requestAnimationFrame poll (~16ms), NOT the audio `timeupdate` event
// (which only fires ~every 250ms) — otherwise small out-cut nudges do nothing
// until they cross a tick, then drop a whole quarter-second, and the stop point
// jitters run-to-run.
function winStop(){
  if(winEndAt==null){ winRAF=null; return; }
  if(winAudio.currentTime>=winEndAt){ winAudio.pause(); winRAF=null; return; }
  winRAF=requestAnimationFrame(winStop);
}
function playWindow(r,o){
  const winStart=Math.max(0, r.clip_head - BASEPAD - o.h);
  winEndAt=r.clip_head + r.duration_s + BASEPAD + o.t;
  if(winRAF!=null){ cancelAnimationFrame(winRAF); winRAF=null; }
  const seekPlay=()=>{ winAudio.oncanplay=null; winAudio.currentTime=winStart;
    winAudio.play(); winRAF=requestAnimationFrame(winStop); };
  if(winAudio.src.endsWith(r.preview) && winAudio.readyState>=2) seekPlay();  // same clip, ready
  else { winAudio.src=r.preview; winAudio.oncanplay=seekPlay; }               // load then seek
}
document.addEventListener("click",e=>{
  const keep=e.target.closest("button.k");
  if(keep){const k=keep.dataset.k;
    if(off(k)) delete picks[k]; else picks[k]={h:0,t:0};
    save(); render(); return;}
  const nb=e.target.closest("button.nb");
  if(nb){const k=nb.dataset.k, o=off(k); if(!o) return;
    const e2=nb.dataset.e, d=Number(nb.dataset.d);
    o[e2]=Math.round((o[e2]+d*STEP)*1000)/1000;   // avoid float drift
    picks[k]=o; save();
    const ro=document.getElementById("ro_"+k);
    ro.textContent=`in ${fmt(o.h)} / out ${fmt(o.t)}`;
    ro.classList.toggle("set", !!(o.h||o.t));
    return;}
  const pw=e.target.closest("button.pw");
  if(pw){const k=pw.dataset.pw, o=off(k); if(!o) return;
    playWindow(ROW.get(k), o); return;}
});
document.getElementById("fKept").onchange=render;
render();
</script>
"""
