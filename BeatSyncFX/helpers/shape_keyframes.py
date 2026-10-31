"""Deterministic interpolation of user-defined shape geometry keyframes."""
from __future__ import annotations

FIELDS = ('x', 'y', 'end_x', 'end_y', 'frame_width', 'frame_height', 'rotation')

def geometry_at(event, timestamp):
    keys = sorted((k for k in event.get('shape_keyframes', []) if isinstance(k, dict) and 'time' in k), key=lambda k: float(k['time']))
    if not keys:
        return event
    t = float(timestamp)
    if t <= float(keys[0]['time']):
        before = after = keys[0]
    elif t >= float(keys[-1]['time']):
        before = after = keys[-1]
    else:
        before, after = keys[0], keys[-1]
        for a,b in zip(keys,keys[1:]):
            if float(a['time']) <= t <= float(b['time']):
                before, after = a,b
                break
    span = float(after['time']) - float(before['time'])
    alpha = max(0.0,min(1.0,(t-float(before['time']))/span)) if span > 1e-9 else 0.0
    result = dict(event)
    for field in FIELDS:
        if field in before or field in after:
            a=float(before.get(field,event.get(field,0)))
            b=float(after.get(field,event.get(field,0)))
            result[field] = a + (b-a)*alpha
    return result

def capture(event, timestamp):
    key={'time':round(float(timestamp),3)}
    for field in FIELDS:
        if field in event: key[field]=float(event[field])
    keys=[k for k in event.get('shape_keyframes',[]) if abs(float(k['time'])-key['time']) > 0.001]
    keys.append(key)
    event['shape_keyframes']=sorted(keys,key=lambda k:k['time'])
    return len(event['shape_keyframes'])
