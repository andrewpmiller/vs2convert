"""Optional Shadertoy pass/channel metadata, supplied alongside the code.

WHY THIS EXISTS
---------------
A Shadertoy ".txt" paste is the shader BODY only. Everything that makes a
Shadertoy shader what it is - how many passes, which pass writes the screen,
and crucially what each iChannel is bound to - lives in the web UI, not in
the file. Shadertoy's own guidance is to inspect the pass configuration
before assuming what a channel holds, because a channel may be a texture, a
feedback buffer, a cubemap, video, keyboard or audio.

The site being unavailable makes that inspection harder, not the problem
easier. So rather than guess, the converter accepts a small JSON sidecar
that states what the body needs, and downgrades its confidence when it is
absent.

HOW TO SUPPLY IT
----------------
Any of these, all equivalent:

  1. A sidecar file next to the body: `my-shader.stmeta.json` beside
     `my-shader.shadertoy`, or any single `*.stmeta.json` in the directory.
  2. A JSON block pasted at the TOP of the .txt, in a comment:

         /*{
             "passes": 1,
             "final_pass": 0,
             "channels": {"iChannel0": {"sampler": "texture",
                                       "source": "a user-supplied image"}}
         }*/
         ... body follows ...

  3. `--meta path/to/file.json` on the command line.

WHAT IT RESOLVES
----------------
  channels  : replaces the UNVERIFIED warning with a CONFIRMED one when the
              sampler is an ordinary user image, or refuses when the channel
              is a buffer/cubemap/audio that VS-2 cannot represent.
  passes    : tells the converter which pass writes the screen, instead of
              assuming the last one.
  uniforms  : supplies the iChannelResolution-style dimensions if needed.

Only `channels` changes the safety verdict. Everything else is refinement:
absent it, the conversion still happens, just marked unverified.
"""

import json
import os
import re

# Sampler kinds VS-2 can represent. VS-2 binds a single image to `imageTex`.
REPRESENTABLE_SAMPLERS = {'texture', 'image', 'video', '2d'}
# Sampler kinds it cannot: these carry data a single image cannot.
UNREPRESENTABLE_SAMPLERS = {'buffer', 'cube', 'cubemap', 'audio', 'keyboard'}

_META_BLOCK_RE = re.compile(r'/\*(.*?)\*/', re.DOTALL)


def find_meta_file(body_path):
    """Sidecar next to the body: <stem>.stmeta.json, else a lone one nearby.

    `None` means there is no file (stdin); only --meta and the inline block
    can supply metadata then.
    """
    if not body_path:
        return None
    stem, _ = os.path.splitext(body_path)
    for cand in (stem + '.stmeta.json', body_path + '.stmeta.json'):
        if os.path.exists(cand):
            return cand
    d = os.path.dirname(os.path.abspath(body_path)) or '.'
    try:
        found = [f for f in os.listdir(d) if f.endswith('.stmeta.json')]
    except OSError:
        return None
    return os.path.join(d, found[0]) if len(found) == 1 else None


def meta_from_source(source):
    """A JSON block at the very top of the .txt, inside a comment."""
    m = _META_BLOCK_RE.search(source)
    if not m:
        return None
    try:
        data = json.loads(m.group(1).strip())
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def load_meta(body_path, source, explicit=None):
    """Resolve metadata from, in order: --meta, sidecar, inline block.

    Returns (meta_dict_or_None, where_it_came_from).
    """
    if explicit:
        if not os.path.exists(explicit):
            raise ValueError('meta file not found: %s' % explicit)
        with open(explicit) as f:
            return json.load(f), explicit
    path = find_meta_file(body_path)
    if path:
        with open(path) as f:
            return json.load(f), path
    inline = meta_from_source(source)
    if inline is not None:
        return inline, 'inline block'
    return None, None


def resolve_channels(meta, channels_found):
    """Decide the safety verdict for each iChannel the body reads.

    Returns (status, notes) where status is:
      'confirmed'    - metadata says it is an ordinary image; safe
      'unverified'   - no metadata; we are guessing
      'unrepresentable' - metadata says buffer/cubemap/audio; refuse
    """
    if not meta or 'channels' not in meta:
        return 'unverified', []
    declared = meta.get('channels') or {}
    notes = []
    worst = 'confirmed'
    for ch in channels_found:
        entry = declared.get('iChannel%s' % ch) or declared.get(ch) or {}
        sampler = str(entry.get('sampler', '')).strip().lower()
        if not sampler:
            notes.append('iChannel%s: declared but no sampler given' % ch)
            worst = 'unverified' if worst == 'confirmed' else worst
        elif sampler in UNREPRESENTABLE_SAMPLERS:
            notes.append('iChannel%s is a %s, which VS-2 cannot represent '
                         '(it has a single imageTex)' % (ch, sampler))
            worst = 'unrepresentable'
        elif sampler in REPRESENTABLE_SAMPLERS:
            src = entry.get('source', '')
            notes.append('iChannel%s confirmed: %s%s'
                         % (ch, sampler,
                            ' (%s)' % src if src else ''))
    return worst, notes
