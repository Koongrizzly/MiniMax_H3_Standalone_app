"""Manual corner-pin geometry. Coordinates are normalized as video percentages."""
DEFAULT_QUAD = [[26., 62.], [74., 62.], [83., 84.], [17., 84.]]

def quad_at(event, timestamp):
    keys = sorted((k for k in event.get('planar_keys', []) if isinstance(k, dict) and 'time' in k and 'quad' in k), key=lambda k: float(k['time']))
    if not keys:
        return [list(v) for v in event.get('quad', DEFAULT_QUAD)]
    t = float(timestamp)
    if t <= float(keys[0]['time']): return [list(v) for v in keys[0]['quad']]
    if t >= float(keys[-1]['time']): return [list(v) for v in keys[-1]['quad']]
    for a,b in zip(keys, keys[1:]):
        ta,tb=float(a['time']),float(b['time'])
        if ta <= t <= tb:
            f=(t-ta)/(tb-ta) if tb>ta else 0
            return [[float(u)+(float(v)-float(u))*f for u,v in zip(p,q)] for p,q in zip(a['quad'],b['quad'])]
    return [list(v) for v in event.get('quad', DEFAULT_QUAD)]

def set_quad_key(event, timestamp, quad):
    key={'time':round(float(timestamp),3),'quad':[[round(float(a),4),round(float(b),4)] for a,b in quad]}
    keys=[k for k in event.get('planar_keys',[]) if abs(float(k['time'])-key['time'])>.001]
    keys.append(key)
    event['planar_keys']=sorted(keys,key=lambda k:float(k['time']))
    return len(event['planar_keys'])
