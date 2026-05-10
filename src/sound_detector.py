import threading
import time
from collections import deque

import numpy as np
import sounddevice as sd
import soundfile as sf
from python_speech_features import mfcc as psf_mfcc
from scipy.signal import resample


class SoundDetector:
    SAMPLE_RATE = 16000
    N_MFCC = 20

    def __init__(self):
        self.templates: dict = {}
        self.monitoring = False
        self.on_start_triggered = None
        self.on_stop_triggered = None
        self.sensitivity = 0.70
        self.cooldown = 2.0
        self._last_trigger: dict = {}
        self._queue: deque = deque(maxlen=2000)
        self._queue_lock = threading.Lock()
        self._monitor_thread = None
        self.on_similarity_update = None

    # ------------------------------------------------------------------ #
    # Template loading                                                      #
    # ------------------------------------------------------------------ #

    def load_template(self, trigger_type: str, filepath: str):
        y, sr = sf.read(filepath, dtype="float32")
        if y.ndim > 1:
            y = y.mean(axis=1)
        if sr != self.SAMPLE_RATE:
            y = resample(y, int(len(y) * self.SAMPLE_RATE / sr))
        y = self._normalize(y)
        self.templates[trigger_type] = {
            "feature": self._feature_vec(y),
            "duration": len(y) / self.SAMPLE_RATE,
            "rms": float(np.sqrt(np.mean(y ** 2))),
        }
        self._last_trigger[trigger_type] = 0.0

    def has_templates(self) -> bool:
        return bool(self.templates)

    # ------------------------------------------------------------------ #
    # Monitoring                                                            #
    # ------------------------------------------------------------------ #

    def start_monitoring(self, sensitivity=0.70, on_start_triggered=None, on_stop_triggered=None):
        self.sensitivity = sensitivity
        self.on_start_triggered = on_start_triggered
        self.on_stop_triggered = on_stop_triggered
        self.monitoring = True
        with self._queue_lock:
            self._queue.clear()
        self._monitor_thread = threading.Thread(target=self._loop, daemon=True)
        self._monitor_thread.start()

    def stop_monitoring(self):
        self.monitoring = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=3)

    # ------------------------------------------------------------------ #
    # Internal                                                              #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _normalize(y: np.ndarray) -> np.ndarray:
        peak = np.max(np.abs(y))
        return y / peak if peak > 1e-6 else y

    def _feature_vec(self, y: np.ndarray) -> np.ndarray:
        mfcc = psf_mfcc(y, self.SAMPLE_RATE, numcep=self.N_MFCC,
                         nfilt=40, nfft=512, winlen=0.025, winstep=0.01)
        return np.concatenate([np.mean(mfcc, axis=0), np.std(mfcc, axis=0)])

    def _cosine(self, a: np.ndarray, b: np.ndarray) -> float:
        na, nb = np.linalg.norm(a), np.linalg.norm(b)
        if na < 1e-9 or nb < 1e-9:
            return 0.0
        return float(np.dot(a, b) / (na * nb))

    def _similarity(self, trigger_type: str, segment: np.ndarray) -> float:
        tmpl = self.templates[trigger_type]
        if float(np.sqrt(np.mean(segment ** 2))) < tmpl["rms"] * 0.05:
            return 0.0
        seg = self._normalize(segment)
        return self._cosine(tmpl["feature"], self._feature_vec(seg))

    def _loop(self):
        if not self.templates:
            return

        max_dur = max(t["duration"] for t in self.templates.values())
        chunk_size = int(0.05 * self.SAMPLE_RATE)
        buf_size = int((max_dur + 1.5) * self.SAMPLE_RATE)
        accumulated = np.zeros(0, dtype=np.float32)

        def _cb(indata, frames, time_info, status):
            with self._queue_lock:
                self._queue.append(indata[:, 0].copy())

        with sd.InputStream(samplerate=self.SAMPLE_RATE, channels=1,
                            dtype="float32", blocksize=chunk_size, callback=_cb):
            while self.monitoring:
                chunks = []
                with self._queue_lock:
                    while self._queue:
                        chunks.append(self._queue.popleft())

                if chunks:
                    accumulated = np.append(accumulated, np.concatenate(chunks))
                    if len(accumulated) > buf_size:
                        accumulated = accumulated[-buf_size:]

                    now = time.time()
                    for ttype, tmpl in self.templates.items():
                        n = int(tmpl["duration"] * self.SAMPLE_RATE)
                        if len(accumulated) < n:
                            continue
                        if now - self._last_trigger.get(ttype, 0) < self.cooldown:
                            continue

                        score = self._similarity(ttype, accumulated[-n:])

                        if self.on_similarity_update:
                            self.on_similarity_update(ttype, score)

                        if score >= self.sensitivity:
                            self._last_trigger[ttype] = now
                            accumulated = np.zeros(0, dtype=np.float32)
                            cb = (self.on_start_triggered if ttype == "start"
                                  else self.on_stop_triggered)
                            if cb:
                                threading.Thread(target=cb, daemon=True).start()
                            break

                time.sleep(0.04)
