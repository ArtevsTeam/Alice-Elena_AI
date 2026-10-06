"""Лёгкий спектроанализатор: FFT 1024, sr 16k — грузит за <1 сек."""
import numpy as np
import soundfile as sf


class SpectrumAnalyzer:
    BARS = 40
    WINDOW_SEC = 0.05
    FFT_SIZE = 1024          # было 2048 — в 2 раза быстрее
    F_MIN = 40
    F_MAX = 16000
    SR_TARGET = 16000        # было 22050

    def __init__(self, filepath):
        self.frames = None
        self.duration = 0.0
        try:
            data, sr = sf.read(filepath, dtype="float32", always_2d=True)
            if data.size == 0: return
            mono = data.mean(axis=1)

            if sr > self.SR_TARGET:
                step = sr // self.SR_TARGET
                mono = mono[::step]
                sr = sr // step

            self.duration = len(mono) / sr
            hop = int(sr * self.WINDOW_SEC)
            fft_size = self.FFT_SIZE
            win = np.hanning(fft_size).astype(np.float32)

            n_windows = max(1, (len(mono) - fft_size) // hop + 1)
            freqs = np.fft.rfftfreq(fft_size, 1.0 / sr)

            edges = np.logspace(np.log10(self.F_MIN), np.log10(self.F_MAX), self.BARS + 1)
            band_idx = [np.where((freqs >= edges[i]) & (freqs < edges[i+1]))[0]
                        for i in range(self.BARS)]

            result = np.zeros((n_windows, self.BARS), dtype=np.float32)
            for w in range(n_windows):
                chunk = mono[w * hop: w * hop + fft_size]
                if len(chunk) < fft_size:
                    chunk = np.pad(chunk, (0, fft_size - len(chunk)))
                spec = np.abs(np.fft.rfft(chunk * win))
                for b, idx in enumerate(band_idx):
                    if len(idx) > 0:
                        result[w, b] = spec[idx].mean()

            result = np.log1p(result * 100)
            mx = result.max()
            if mx > 0: result /= mx
            self.frames = result
        except Exception as e:
            print(f"[!] spectrum: {e}")

    def get_frame(self, seconds):
        if self.frames is None or self.duration <= 0:
            return None
        idx = int(seconds / self.WINDOW_SEC)
        if 0 <= idx < len(self.frames):
            return self.frames[idx].tolist()
        return None