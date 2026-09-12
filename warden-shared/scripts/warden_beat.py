"""Where the beats are, so a cut can land on one.

An edit that changes shot half a beat early reads as wrong to everyone and as
nothing in particular to almost no one, which is why edits that work are cut to
the bar. This finds the tempo, the phase of the grid, and where the track gains
body, so a clip can be a whole number of bars long and open on the drop.

The method is spectral flux onsets, autocorrelation for the period, then a phase
sweep, ported from the owner's own bpm.py with its two hard-won corrections kept
intact: autocorrelation reports half or two thirds of the real tempo often
enough that the candidates have to be scored rather than trusted, and the body
threshold is measured from the track's floor rather than its peak, because a
track with a long intro never reaches a share of its peak and the detector then
returns zero silently and the edit opens in the fade.

numpy and ffmpeg only. No audio library, because the agent's image already
carries both and a wheel that needs a compiler is a build that fails on a host.
"""
import os
import subprocess

SR, HOP, WIN = 22050, 512, 2048
BPM_FLOOR, BPM_CEIL = 70, 200


def _samples(path):
    import numpy as np
    done = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR),
         "-f", "f32le", "-"], capture_output=True, timeout=300)
    if done.returncode != 0 or not done.stdout:
        raise RuntimeError(f"ffmpeg could not read audio from {os.path.basename(path)}")
    return np.frombuffer(done.stdout, dtype=np.float32)


def _onsets(x):
    import numpy as np
    count = 1 + (len(x) - WIN) // HOP
    if count < 4:
        raise RuntimeError("this track is too short to find a tempo in")
    window = np.hanning(WIN).astype(np.float32)
    mags = np.empty((count, WIN // 2 + 1), dtype=np.float32)
    for i in range(count):
        mags[i] = np.abs(np.fft.rfft(x[i * HOP:i * HOP + WIN] * window))
    flux = np.diff(mags, axis=0)
    flux[flux < 0] = 0
    env = flux.sum(axis=1)
    return (env - env.mean()) / (env.std() + 1e-9)


def _rough_bpm(env):
    import numpy as np
    fps = SR / HOP
    auto = np.correlate(env, env, mode="full")[len(env) - 1:]
    lo, hi = int(fps * 60 / BPM_CEIL), int(fps * 60 / BPM_FLOOR)
    lag = lo + int(np.argmax(auto[lo:hi]))
    value = 60 * fps / lag
    while value < 100:
        value *= 2
    while value > BPM_CEIL:
        value /= 2
    return round(value, 1), fps


def _score(env, bpm, fps):
    """How much onset energy falls on this tempo's grid, at its best phase."""
    import numpy as np
    step = 60.0 / bpm * fps
    best = -1e9
    for offset in np.arange(0, step, 0.5):
        idx = np.round(np.arange(offset, len(env) - 1, step)).astype(int)
        best = max(best, env[idx].mean())
    return best


def _pick_bpm(env, rough, fps):
    import numpy as np
    candidates = sorted({round(rough * f, 1) for f in (0.5, 2 / 3, 1.0, 1.5, 2.0)}
                        & {round(v, 1) for v in np.arange(80, 190.1, 0.1)})
    if not candidates:
        return rough
    return max(((_score(env, c, fps), c) for c in candidates))[1]


def _first_beat(env, bpm, fps):
    import numpy as np
    step = 60.0 / bpm * fps
    best, phase = -1e9, 0.0
    for offset in np.arange(0, step, 0.5):
        idx = np.round(np.arange(offset, len(env) - 1, step)).astype(int)
        total = env[idx].sum()
        if total > best:
            best, phase = total, offset / fps
    return round(phase, 3)


def _drop(x, beat_s, first_beat):
    """Where the track gains body, snapped to the nearest bar."""
    import numpy as np
    half = SR // 2
    levels = np.array([np.sqrt((x[i:i + half] ** 2).mean())
                       for i in range(0, len(x) - half, half)])
    if not len(levels):
        return 0.0
    floor = float(np.percentile(levels, 20))
    ceiling = float(np.percentile(levels, 90))
    threshold = floor + (ceiling - floor) * 0.45
    for i in range(len(levels) - 8):
        if (levels[i:i + 8] > threshold).mean() > 0.75:
            beats = round((i * 0.5 - first_beat) / beat_s)
            return round(max(first_beat + round(beats / 4) * 4 * beat_s, 0.0), 3)
    return 0.0


def analyse(path):
    """Tempo, grid phase, bar length and the drop, in seconds."""
    x = _samples(path)
    env = _onsets(x)
    rough, fps = _rough_bpm(env)
    bpm = _pick_bpm(env, rough, fps)
    first = _first_beat(env, bpm, fps)
    beat_s = 60.0 / bpm
    return {
        "track": os.path.basename(path),
        "bpm": round(bpm, 1),
        "first_beat_s": first,
        "beat_s": round(beat_s, 4),
        "bar_s": round(beat_s * 4, 4),
        "drop_s": _drop(x, beat_s, first),
        "duration_s": round(len(x) / SR, 2),
    }


def snap(length, bar_s, lo=None, hi=None, floor_bars=2):
    """The nearest whole number of bars that still obeys the campaign.

    Returns the length and how many bars it is. The campaign's window is the
    outer bound in both directions: an edit one bar too long is still a rejected
    submission, and a beautiful cut that does not get paid is not a good cut.
    """
    if not bar_s or bar_s <= 0:
        return length, None
    bars = max(floor_bars, round(length / bar_s))
    best = bars * bar_s
    if hi is not None and best > hi:
        bars = max(1, int(hi // bar_s))
        best = bars * bar_s
    if lo is not None and best < lo:
        bars = int(-(-lo // bar_s))
        best = bars * bar_s
        if hi is not None and best > hi:
            return None, None                  # no whole bar count fits this window
    return round(best, 3), bars
