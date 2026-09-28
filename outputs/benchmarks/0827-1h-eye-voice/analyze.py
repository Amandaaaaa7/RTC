import csv
from pathlib import Path

p = Path('outputs/benchmarks/0827-1h-eye-voice/0827-1h-eye-voice-continuous.csv')
rows = list(csv.DictReader(p.open(encoding='utf-8')))
print('samples:', len(rows))
print('duration_min:', round(len(rows)/60, 1))

eye_fps = [float(r['eye_effective_fps']) for r in rows if r['eye_effective_fps']]
print('eye_fps_min:', min(eye_fps))
print('eye_fps_max:', max(eye_fps))
print('eye_fps_avg:', round(sum(eye_fps)/len(eye_fps), 3))

cpu = [float(r['cpu_percent']) for r in rows if r['cpu_percent']]
print('cpu_max:', max(cpu))
print('cpu_avg:', round(sum(cpu)/len(cpu), 3))

temps = [float(r['temperature'].replace("temp=", "").replace("'C", "")) for r in rows if r['temperature']]
print('temp_max:', max(temps))
print('temp_avg:', round(sum(temps)/len(temps), 3))

swap = [float(r['swap_mib']) for r in rows if r['swap_mib']]
print('swap_max:', max(swap))
print('swap_avg:', round(sum(swap)/len(swap), 3))

print('audio_start_total:', rows[-1]['audio_started_count'])
print('audio_completed_total:', rows[-1]['audio_completed_count'])
print('audio_failed_total:', rows[-1]['audio_failed_count'])
print('audio_rejected_total:', rows[-1]['audio_rejected_count'])

# 25-28 min segment
seg = rows[1500:1680]
print('--- 25-28 min segment ---')
print('samples:', len(seg))
seg_eye = [float(r['eye_effective_fps']) for r in seg]
print('avg eye fps:', round(sum(seg_eye)/len(seg_eye), 3))
print('min eye fps:', min(seg_eye))
print('max eye fps:', max(seg_eye))
seg_tracker = [float(r['tracker_effective_fps']) for r in seg]
print('avg tracker fps:', round(sum(seg_tracker)/len(seg_tracker), 3))
print('face_to_eye_p95_max:', max(float(r['face_to_eye_first_frame_p95_ms']) for r in seg))
print('snapshot_age_p95_max:', max(float(r['snapshot_age_p95_ms']) for r in seg))
print('eye_frame_p95_max:', max(float(r['eye_frame_p95_ms']) for r in seg))

# Count audio states in segment
states = {}
for r in seg:
    s = r['audio_state']
    states[s] = states.get(s, 0) + 1
print('audio_state_counts:', states)
