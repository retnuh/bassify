"""NLMS adaptive FIR vs the shipped per-bin static projection.

Tests the prediction that NLMS lands in the same place as the projection:
both are linear filters of R, so both are bounded by the L/R coherence
ceiling. NLMS's advantage is that it tracks drifting delay/EQ, which a single
static per-bin gain cannot.

Runs at 8 kHz -- covers everything up to 4 kHz, far past the 800 Hz band that
matters, and keeps the per-sample adaptation loop tractable.

Adaptation is gated to bass-free frames (adapting while bass plays would let
the filter cancel the bass itself). The bass-free frames are split into a fit
half (adaptation allowed) and a score half (never adapted on), so reported
numbers are held out.

Run: uv run python experiments/nlms_test.py "40_The Thrill Is Gone" ...
"""

import sys
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf
from scipy.signal import stft

COLLECTION = "BluesBass"
SR = 8000
NPERSEG = 512
HOP = 128
LOW_CUTOFF_HZ = 250.0
BASS_FREE_DROP_DB = 30.0
LOUD_PERCENTILE = 90.0

TAPS = 512
MU = 0.05
EPS = 1e-8


def bass_free_frames(L: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray, int]:
    freqs, _, Z = stft(L, fs=sr, nperseg=NPERSEG, noverlap=NPERSEG - HOP)
    low = np.sum(np.abs(Z[freqs < LOW_CUTOFF_HZ, :]) ** 2, axis=0)
    loud = np.percentile(low, LOUD_PERCENTILE)
    free = np.flatnonzero(low <= loud * 10 ** (-BASS_FREE_DROP_DB / 10))
    present = np.flatnonzero(low >= loud * 10 ** (-6.0 / 10))
    return free, present, Z.shape[1]


def frames_to_samples(frames: np.ndarray, n: int) -> np.ndarray:
    m = np.zeros(n, dtype=bool)
    for f in frames:
        start = f * HOP
        m[start : min(start + NPERSEG, n)] = True
    return m


def nlms(
    L: np.ndarray,
    R: np.ndarray,
    adapt: np.ndarray,
    taps: int,
    mu: float,
    w_init: np.ndarray | None = None,
    return_taps: bool = False,
):
    """Return the NLMS error signal (the bass estimate), optionally with the
    final tap vector so a second pass can start from a converged filter.
    """
    n = len(L)
    w = np.zeros(taps) if w_init is None else w_init.copy()
    e_out = np.zeros(n)
    Rpad = np.concatenate([np.zeros(taps - 1), R])
    for i in range(n):
        x = Rpad[i : i + taps][::-1]
        e = L[i] - w @ x
        e_out[i] = e
        if adapt[i]:
            norm = x @ x + EPS
            w += mu * e * x / norm
    return (e_out, w) if return_taps else e_out


def static_projection(L: np.ndarray, R: np.ndarray, fit_frames: np.ndarray, sr: int):
    freqs, _, L_stft = stft(L, fs=sr, nperseg=NPERSEG, noverlap=NPERSEG - HOP)
    _, _, R_stft = stft(R, fs=sr, nperseg=NPERSEG, noverlap=NPERSEG - HOP)
    Lb, Rb = L_stft[:, fit_frames], R_stft[:, fit_frames]
    num = np.sum(Lb * np.conj(Rb), axis=1)
    den = np.sum(np.abs(Rb) ** 2, axis=1)
    h = num / (den + np.max(den) * 1e-6)
    from scipy.signal import istft

    _, y = istft(L_stft - h[:, None] * R_stft, fs=sr, nperseg=NPERSEG, noverlap=NPERSEG - HOP)
    if len(y) > len(L):
        y = y[: len(L)]
    elif len(y) < len(L):
        y = np.pad(y, (0, len(L) - len(y)))
    return y


def db(a: float, b: float) -> float:
    if a <= 0 or b <= 0:
        return float("nan")
    return 20 * np.log10(a / b)


def rms(y: np.ndarray, m: np.ndarray) -> float:
    v = y[m]
    return float(np.sqrt(np.mean(v**2))) if v.size else 0.0


def analyse(track: str, write_audio: bool) -> None:
    orig = next((Path("tracks") / COLLECTION).glob(f"{track}.*"))
    y, _ = librosa.load(str(orig), sr=SR, mono=False)
    L, R = y[0].astype(np.float64), y[1].astype(np.float64)
    n = len(L)

    free, present, _ = bass_free_frames(L, SR)
    if len(free) < 200:
        print(f"{track}: only {len(free)} bass-free frames, skipping")
        return

    # Split by RUN, not by alternating frames: frames overlap at this hop, so
    # alternating them leaves the score samples almost entirely inside the
    # adapted ones. Runs are temporally separated, so their samples are too.
    runs, start = [], free[0]
    for prev, cur in zip(free[:-1], free[1:], strict=False):
        if cur != prev + 1:
            runs.append((start, prev))
            start = cur
    runs.append((start, free[-1]))
    fit_f = np.concatenate([np.arange(a, b + 1) for a, b in runs[0::2]])
    score_f = np.concatenate([np.arange(a, b + 1) for a, b in runs[1::2]])
    print(f"    {len(runs)} bass-free runs -> {len(fit_f)} fit / {len(score_f)} score frames")

    adapt = frames_to_samples(fit_f, n)
    score_m = frames_to_samples(score_f, n) & ~adapt
    signal_m = frames_to_samples(present, n)

    naive = L - R
    proj = static_projection(L, R, fit_f, SR)

    print(f"\n=== {track}  ({len(free)} bass-free frames, {TAPS} taps)")
    print(
        f"    mask samples: adapt {adapt.sum()}  score {score_m.sum()}  "
        f"signal {signal_m.sum()}  of {n}"
    )
    if score_m.sum() < 1000:
        print("    score mask too small after excluding adapted samples")

    # NLMS does not need bass-free frames: the bass is uncorrelated with R, so
    # it does not bias the Wiener optimum -- it only adds variance. Adapting
    # continuously is the whole advantage over a gated per-bin fit. Gated runs
    # are kept only as the comparison.
    always = np.ones(n, dtype=bool)
    results = [("naive L-R", naive), ("static proj", proj)]
    for mu in (0.002, 0.01, 0.05):
        results.append((f"NLMS-cont mu={mu}", nlms(L, R, always, TAPS, mu)))
    results.append((f"NLMS-gated mu={MU}", nlms(L, R, adapt, TAPS, MU)))

    # Two-pass: converge the taps on a first pass, then re-run the track from
    # the converged filter so the opening is not the ramp-in transient.
    _, w_final = nlms(L, R, always, TAPS, 0.05, return_taps=True)
    results.append(("NLMS-2pass frozen", nlms(L, R, np.zeros(n, bool), TAPS, 0.05, w_final)))
    results.append(("NLMS-2pass cont", nlms(L, R, always, TAPS, 0.05, w_final)))

    # Converge fast, then run the second pass slowly: low misadjustment (so
    # less bass is eaten) without paying the slow filter's ramp-in.
    for mu2 in (0.01, 0.002):
        results.append((f"NLMS-2pass mu2={mu2}", nlms(L, R, always, TAPS, mu2, w_final)))

    for name, sig in results:
        leak = db(rms(sig, score_m), rms(sig, signal_m))
        bass_kept = db(rms(sig, signal_m), rms(naive, signal_m))
        print(f"    {name:12s} leak {leak:6.1f} dB   bass level vs naive {bass_kept:+5.1f} dB")
    adaptive = results[-1][1]

    if write_audio:
        import subprocess

        from scipy.signal import butter, sosfilt

        num = track.split("_", 1)[0]
        sos = butter(2, 800.0 / (SR / 2), btype="low", output="sos")

        # (signal, extra 2-pole backstop stages to apply). The shipped
        # bass_clean.wav already carries a 4-pole backstop, so it gets none;
        # "option C" is that same file with 4 further stages (12-pole total).
        to_render = [(name, sig, 2) for name, sig in results]

        clean_p = Path("out") / COLLECTION / track / f"{track}_bass_clean.wav"
        if clean_p.exists():
            shipped, _ = librosa.load(str(clean_p), sr=SR, mono=True)
            shipped = shipped.astype(np.float64)
            if len(shipped) < n:
                shipped = np.pad(shipped, (0, n - len(shipped)))
            shipped = shipped[:n]
            to_render.append(("shipped clean A", shipped, 0))
            to_render.append(("shipped clean C 12pole", shipped, 4))

        filtered = []
        for name, sig, stages in to_render:
            z = sig
            for _ in range(stages):
                z = sosfilt(sos, z)
            filtered.append((name, z))

        # ONE shared scale for every variant -- per-file normalisation would
        # hide the bass-level differences these variants are being judged on.
        scale = 0.9 / max(np.max(np.abs(z)) for _, z in filtered)
        for name, z in filtered:
            label = name.replace(" ", "_").replace("=", "").replace(".", "p")
            wav = Path("experiments") / f"_tmp_{num}_{label}.wav"
            out = Path("experiments") / f"{num}_nlms_{label}.m4a"
            sf.write(str(wav), z * scale, SR, subtype="PCM_16")
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(wav),
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    str(out),
                ],
                check=True,
            )
            wav.unlink()
            print(f"    wrote {out}")


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--audio"]
    write_audio = "--audio" in sys.argv
    if not args:
        sys.exit("usage: nlms_test.py [--audio] <track dir name> ...")
    for t in args:
        analyse(t, write_audio)


if __name__ == "__main__":
    main()
