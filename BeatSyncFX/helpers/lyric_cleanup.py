"""Non-destructive suppression of non-lyric Whisper captions, preserving timestamps."""
from __future__ import annotations
import re

SOUND_WORDS = {'upbeat', 'music', 'musical', 'instrumental', 'instrumentals',
               'applause', 'clapping', 'laughing', 'laughter', 'cheering',
               'humming', 'singing', 'silence', 'sound', 'sounds', 'intro',
               'outro', 'background music', 'no speech', 'no vocals'}
NOTE_CHARS = '♪♫♬♩🎵🎶'


def is_sound_caption(token: str) -> bool:
    """Conservative: strip explicit metadata, not legitimate sung words."""
    s = str(token).strip()
    if not s:
        return True
    if all(c in NOTE_CHARS + ' \t\n.,!?-–—' for c in s):
        return True
    core = s.strip(' \t\n.,!?-–—').strip()
    if re.fullmatch(r'\[(.*?)\]|\((.*?)\)|<(.*?)>', core):
        cleaned = re.sub(r'^[\[<(]|[\])>]$', '', core).strip().lower()
        if cleaned in SOUND_WORDS or any(x in cleaned for x in ('music', 'instrumental', 'applause', 'no speech', 'no vocals')):
            return True
    # Explicit note-wrapped phrases (Whisper's habitual hallucination).
    if core and core[0] in NOTE_CHARS and core[-1] in NOTE_CHARS:
        return True
    return False


def mute_sound_captions(words: list[dict]) -> int:
    count = 0
    # whisper.cpp can tokenize '( upbeat music )' into four separate cues.
    # Suppress such runs only when bracketed and composed entirely of known
    # sound-description terms. Don't suppress a real lyric simply for matching
    # a single music-related word such as 'singing'.
    for i, word in enumerate(words):
        if str(word.get('w','')).strip() in ('(', '[', '<'):
            for j in range(i+1,min(i+12,len(words))):
                if str(words[j].get('w','')).strip() in (')',']','>'):
                    joined=' '.join(str(x.get('w','')).strip().lower() for x in words[i+1:j])
                    if joined and (joined in SOUND_WORDS or any(v in joined for v in ('upbeat music','background music','instrumental','applause'))):
                        for x in words[i:j+1]:
                            if not x.get('hidden'):
                                x['hidden']=True;count+=1
                    break
    for word in words:
        if is_sound_caption(word.get('w', '')) and not word.get('hidden', False):
            word['hidden'] = True
            count += 1
    return count


def hide_words_at_indices(words: list[dict], indices) -> int:
    count = 0
    for i in set(indices):
        if 0 <= i < len(words) and not words[i].get('hidden', False):
            words[i]['hidden'] = True
            count += 1
    return count


def restore_words_at_indices(words: list[dict], indices) -> int:
    count = 0
    for i in set(indices):
        if 0 <= i < len(words) and words[i].get('hidden', False):
            words[i]['hidden'] = False
            count += 1
    return count
