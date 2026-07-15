# Contributing to Quotemine

Thanks for pitching in. The short version: bugs go to
[Issues](https://github.com/MerrillFraz/quotemine/issues), questions and ideas
go to [Discussions](https://github.com/MerrillFraz/quotemine/discussions), and
code arrives by pull request against `main`.

## Dev setup

```bash
python -m venv .venv && source .venv/bin/activate
# torch FIRST, from the PyTorch index — see docs/gotchas.md for why this order matters
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

**Read `docs/gotchas.md` before touching pipeline code.** Several design
decisions that look odd (per-utterance tagging, no auto-purity filter, no
silence-trimming of finals) are deliberate and documented there; PRs that
reverse them will be declined.

## Running tests

```bash
pytest
```

The suite covers the pure-logic paths (no GPU or ffmpeg needed). If your change
touches a pipeline stage's runtime behavior, say in the PR how you exercised it
against real audio.

## Pull requests

- `main` is protected: all changes land via PR (no direct pushes). No review
  approval is required, but keep PRs small and self-explanatory.
- Conventional commit subjects: `feat:`, `fix:`, `docs:`, `refactor:` — under
  72 characters.
- Keep the engine/project split: `pipeline/` stays corpus-agnostic; anything
  specific to a show, film, or game belongs in `projects/<name>/` (config or
  the `package.py` hook).
- Never commit tokens, `env.sh`, `work/`, audio, or `.db` files — `.gitignore`
  already covers them; keep it that way.

## Reporting bugs

Use the bug-report form — it asks for the stage/command, expected behavior, and
your environment (OS, GPU, torch build). Environment traps (CPU torch wheel,
pyannote license, `MAX_SPEAKERS`) account for a lot of apparent bugs, so a
skim of `docs/gotchas.md` first can save us both a round-trip.
