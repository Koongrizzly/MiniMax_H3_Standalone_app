"""Offline monotonic alignment of supplied lyrics to already-timed Whisper words.

This is text sequence matching, NOT phoneme-level forced alignment. Existing time
anchors are preserved and unmatched spans are interpolated; inspect the results.
"""
import re
from difflib import SequenceMatcher


def normalize(word):
    return re.sub(r'[^\w]+', '', str(word).casefold(), flags=re.UNICODE)


def align_known_lyrics(lyrics, recognized, duration=0.0):
    target = [(token, ln) for ln, row in enumerate(lyrics.splitlines()) for token in row.split()]
    if not target:
        raise ValueError('Enter the correct lyrics first.')
    source = [w for w in recognized if not w.get('hidden') and normalize(w.get('w', ''))]
    if not source:
        raise ValueError('No usable Whisper timestamps; transcribe or import timed words first.')
    left = [normalize(w) for w, _ in target]
    right = [normalize(w.get('w', '')) for w in source]
    matches = SequenceMatcher(None, left, right, autojunk=False).get_matching_blocks()
    anchors = {}
    for block in matches:
        for k in range(block.size):
            idx, src = block.a+k, source[block.b+k]
            anchors[idx] = (float(src['t0']), float(src['t1']))
    if not anchors:
        raise ValueError('No matching words found. Use a cleaner vocal stem or manual tapping.')
    n = len(target)
    starts = [None]*n
    ends = [None]*n
    for i, (a,b) in anchors.items():
        starts[i], ends[i] = a,b
    ordered = sorted(anchors)
    # Fill internal gaps without modifying the anchored words.
    for first, last in zip(ordered, ordered[1:]):
        count = last-first-1
        if count <= 0: continue
        lo = ends[first]
        hi = starts[last]
        step = max(0.035, (hi-lo)/(count+1)) if hi > lo else .12
        for j in range(1,count+1):
            i = first+j
            starts[i] = lo+(j-1)*step
            ends[i] = min(hi, starts[i]+step*.9) if hi>lo else starts[i]+.1
    if ordered[0] > 0:
        lead = ordered[0]
        step = min(.35, max(.09, starts[lead]/max(lead, 1)))
        for i in range(lead):
            starts[i] = max(0.0, starts[lead]-(lead-i)*step)
            ends[i] = min(starts[lead], starts[i]+step*.9)
    if ordered[-1] < n-1:
        tail = ordered[-1]
        step = min(.35, max(.1, (float(duration)-ends[tail])/max(1,n-tail-1))) if duration>ends[tail] else .22
        for i in range(tail+1,n):
            starts[i] = ends[tail]+(i-tail-1)*step
            ends[i] = starts[i]+step*.9
    return [{'w': word, 'line': line, 't0': round(max(0, starts[i]),3),
             't1': round(max(starts[i]+.03,ends[i]),3),
             'estimated': i not in anchors}
            for i,(word,line) in enumerate(target)]
