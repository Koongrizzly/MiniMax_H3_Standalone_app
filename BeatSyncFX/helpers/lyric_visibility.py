"""Absolute lyric cue boundaries shared by all animation styles.

The interval is half-open: [t0, t1). No line-relative pre-roll, hold,
lyric offset, or animation-dependent tail is permitted.
"""

def visible_at(word, timestamp):
    try:
        start, end = float(word['t0']), float(word['t1'])
        return (not word.get('hidden', False)) and bool(str(word.get('w', '')).strip()) and start <= timestamp < end
    except (KeyError, ValueError, TypeError):
        return False


def active_words(words, timestamp):
    return [word for word in words if visible_at(word, timestamp)]
