import json
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
import sounddevice as sd
import soundfile as sf

from obs_client import OBSClient
from sound_detector import SoundDetector

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# exe化時は sys.executable の隣、開発時はスクリプトの隣をベースにする
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SOUND_DIR   = os.path.join(BASE_DIR, "sound")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

os.makedirs(SOUND_DIR, exist_ok=True)

GRAY  = ("gray80", "gray25")
DARK  = ("gray90", "gray17")


class App:
    def __init__(self, root: ctk.CTk):
        self.root = root
        self.root.title("OBS Sound Trigger")
        self.root.resizable(False, False)

        self.obs = OBSClient()
        self.detector = SoundDetector()
        self.detector.on_similarity_update = self._on_similarity_update

        self.start_path = tk.StringVar(value="未設定")
        self.stop_path  = tk.StringVar(value="未設定")
        self._is_monitoring = False

        self._sim_bars: dict[str, ctk.CTkProgressBar] = {}
        self._rec_streams: dict[str, object] = {}
        self._rec_buffers: dict[str, list]   = {}

        self._build_ui()
        self._load_config()

    # ------------------------------------------------------------------ #
    # UI                                                                    #
    # ------------------------------------------------------------------ #

    def _section(self, parent, title: str) -> ctk.CTkFrame:
        wrap = ctk.CTkFrame(parent, fg_color=DARK, corner_radius=10)
        wrap.pack(fill="x", padx=12, pady=5)
        ctk.CTkLabel(wrap, text=title, font=ctk.CTkFont(size=12, weight="bold"),
                     anchor="w").pack(fill="x", padx=10, pady=(8, 2))
        inner = ctk.CTkFrame(wrap, fg_color="transparent")
        inner.pack(fill="x", padx=10, pady=(0, 8))
        return inner

    def _build_ui(self):
        self.root.configure(fg_color=("gray95", "gray13"))

        # ── OBS接続 ──────────────────────────────────────────────────── #
        cf = self._section(self.root, "  OBS接続設定")

        row0 = ctk.CTkFrame(cf, fg_color="transparent")
        row0.pack(fill="x", pady=2)
        ctk.CTkLabel(row0, text="ホスト", width=60).pack(side="left")
        self._host = tk.StringVar(value="localhost")
        ctk.CTkEntry(row0, textvariable=self._host, width=160).pack(side="left", padx=(0, 12))
        ctk.CTkLabel(row0, text="ポート", width=50).pack(side="left")
        self._port = tk.StringVar(value="4455")
        ctk.CTkEntry(row0, textvariable=self._port, width=70).pack(side="left")

        row1 = ctk.CTkFrame(cf, fg_color="transparent")
        row1.pack(fill="x", pady=2)
        ctk.CTkLabel(row1, text="パスワード", width=70).pack(side="left")
        self._pw = tk.StringVar(value="")
        ctk.CTkEntry(row1, textvariable=self._pw, show="*", width=160).pack(side="left", padx=(0, 12))
        self._conn_btn    = ctk.CTkButton(row1, text="接続",  width=70, command=self._connect)
        self._conn_btn.pack(side="left", padx=(0, 6))
        self._disconn_btn = ctk.CTkButton(row1, text="切断",  width=70, command=self._disconnect,
                                           fg_color="gray40", hover_color="gray50", state="disabled")
        self._disconn_btn.pack(side="left")

        self._conn_lbl = ctk.CTkLabel(cf, text="● 未接続", text_color="#e05050",
                                       font=ctk.CTkFont(size=12))
        self._conn_lbl.pack(pady=(4, 0))

        # ── トリガーパネル ────────────────────────────────────────────── #
        self._build_trigger_panel("  録画開始トリガー音", "start", self.start_path)
        self._build_trigger_panel("  録画停止トリガー音", "stop",  self.stop_path)

        # ── 検出設定 ──────────────────────────────────────────────────── #
        df = self._section(self.root, "  検出設定")

        sens_row = ctk.CTkFrame(df, fg_color="transparent")
        sens_row.pack(fill="x", pady=2)
        ctk.CTkLabel(sens_row, text="感度", width=40).pack(side="left")
        self._sens = tk.DoubleVar(value=0.70)
        ctk.CTkSlider(sens_row, from_=0.30, to=0.97, variable=self._sens,
                      width=200).pack(side="left", padx=6)
        self._sens_lbl = ctk.CTkLabel(sens_row, text="0.70", width=40)
        self._sens_lbl.pack(side="left")
        self._sens.trace_add("write", lambda *_: self._sens_lbl.configure(
            text=f"{self._sens.get():.2f}"))

        ctk.CTkLabel(sens_row, text="クールダウン(秒)", width=110).pack(side="left", padx=(16, 0))
        self._cd = tk.StringVar(value="2.0")
        ctk.CTkEntry(sens_row, textvariable=self._cd, width=55).pack(side="left", padx=4)

        # ── モニタリング ──────────────────────────────────────────────── #
        mid = ctk.CTkFrame(self.root, fg_color="transparent")
        mid.pack(pady=8)
        self._mon_btn = ctk.CTkButton(mid, text="モニタリング開始", width=200,
                                       height=40, font=ctk.CTkFont(size=14, weight="bold"),
                                       command=self._toggle_monitoring)
        self._mon_btn.pack()
        self._status_lbl = ctk.CTkLabel(mid, text="待機中",
                                         font=ctk.CTkFont(size=13, weight="bold"),
                                         text_color="gray60")
        self._status_lbl.pack(pady=(6, 0))

        # ── ログ ──────────────────────────────────────────────────────── #
        lf = self._section(self.root, "  ログ")
        self._log = ctk.CTkTextbox(lf, height=140, font=ctk.CTkFont(family="Consolas", size=11),
                                    state="disabled", wrap="none")
        self._log.pack(fill="both", expand=True)

    def _build_trigger_panel(self, title: str, key: str, path_var: tk.StringVar):
        pf = self._section(self.root, title)

        path_lbl = ctk.CTkLabel(pf, textvariable=path_var, anchor="w",
                                  fg_color=GRAY, corner_radius=6, height=28)
        path_lbl.pack(fill="x", pady=(0, 6))

        btn_row = ctk.CTkFrame(pf, fg_color="transparent")
        btn_row.pack(fill="x")

        start_btn = ctk.CTkButton(btn_row, text="録音開始", width=80,
                                   fg_color="#2e7d32", hover_color="#388e3c",
                                   command=lambda: self._start_rec(key))
        start_btn.pack(side="left", padx=(0, 4))
        setattr(self, f"_rec_start_{key}_btn", start_btn)

        stop_btn = ctk.CTkButton(btn_row, text="録音停止", width=80,
                                  fg_color="gray40", hover_color="gray50", state="disabled",
                                  command=lambda: self._stop_rec(key))
        stop_btn.pack(side="left", padx=(0, 12))
        setattr(self, f"_rec_stop_{key}_btn", stop_btn)

        ctk.CTkButton(btn_row, text="ファイル選択", width=100,
                       command=lambda: self._pick_file(key)).pack(side="left", padx=(0, 6))
        ctk.CTkButton(btn_row, text="試聴", width=60, fg_color="gray40", hover_color="gray50",
                       command=lambda: self._preview(key)).pack(side="left")

        sim_row = ctk.CTkFrame(pf, fg_color="transparent")
        sim_row.pack(fill="x", pady=(6, 0))
        ctk.CTkLabel(sim_row, text="類似度", width=50).pack(side="left")
        bar = ctk.CTkProgressBar(sim_row, width=220, height=14)
        bar.set(0)
        bar.pack(side="left", padx=6)
        self._sim_bars[key] = bar

    # ------------------------------------------------------------------ #
    # OBS                                                                   #
    # ------------------------------------------------------------------ #

    def _connect(self):
        try:
            port = int(self._port.get())
        except ValueError:
            messagebox.showerror("エラー", "ポート番号が正しくありません")
            return
        try:
            self.obs.connect(self._host.get(), port, self._pw.get())
            self._conn_lbl.configure(text="● 接続済み", text_color="#50c878")
            self._conn_btn.configure(state="disabled")
            self._disconn_btn.configure(state="normal")
            self._log_msg(f"OBS接続成功: {self._host.get()}:{port}")
            self._save_config()
        except Exception as e:
            self._conn_lbl.configure(text="● 接続失敗", text_color="#e05050")
            self._log_msg(f"接続エラー: {e}")
            messagebox.showerror("接続エラー", str(e))

    def _disconnect(self):
        if self._is_monitoring:
            self._toggle_monitoring()
        self.obs.disconnect()
        self._conn_lbl.configure(text="● 未接続", text_color="#e05050")
        self._conn_btn.configure(state="normal")
        self._disconn_btn.configure(state="disabled")
        self._log_msg("OBS切断")

    # ------------------------------------------------------------------ #
    # Recording sample                                                      #
    # ------------------------------------------------------------------ #

    def _start_rec(self, key: str):
        if key in self._rec_streams:
            return
        self._rec_buffers[key] = []

        def _cb(indata, frames, time_info, status):
            self._rec_buffers[key].append(indata[:, 0].copy())

        stream = sd.InputStream(samplerate=44100, channels=1, dtype="float32", callback=_cb)
        stream.start()
        self._rec_streams[key] = stream

        getattr(self, f"_rec_start_{key}_btn").configure(state="disabled")
        getattr(self, f"_rec_stop_{key}_btn").configure(
            state="normal", fg_color="#c0392b", hover_color="#e74c3c")
        self._log_msg(f"[{key}] 録音中...")

    def _stop_rec(self, key: str):
        stream = self._rec_streams.pop(key, None)
        if stream is None:
            return
        stream.stop()
        stream.close()

        buf = self._rec_buffers.pop(key, [])
        if not buf:
            self._log_msg(f"[{key}] 録音データなし")
        else:
            import numpy as np
            audio = np.concatenate(buf)
            path = os.path.join(SOUND_DIR, f"{key}_trigger.wav")
            sf.write(path, audio, 44100)
            self._set_template(key, path)
            self._log_msg(f"[{key}] 録音完了: {os.path.basename(path)}")

        getattr(self, f"_rec_start_{key}_btn").configure(state="normal")
        getattr(self, f"_rec_stop_{key}_btn").configure(
            state="disabled", fg_color="gray40", hover_color="gray50")

    # ------------------------------------------------------------------ #
    # File / preview                                                        #
    # ------------------------------------------------------------------ #

    def _pick_file(self, key: str):
        path = filedialog.askopenfilename(
            filetypes=[("音声ファイル", "*.mp3 *.wav *.ogg *.flac"), ("すべて", "*.*")]
        )
        if path:
            self._set_template(key, path)
            self._log_msg(f"[{key}] ファイル選択: {os.path.basename(path)}")

    def _preview(self, key: str):
        path = self.start_path.get() if key == "start" else self.stop_path.get()
        if path in ("未設定", ""):
            messagebox.showinfo("情報", "先に音声を設定してください")
            return

        def _play():
            try:
                data, sr = sf.read(path, dtype="float32")
                sd.play(data, sr)
                sd.wait()
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("再生エラー", str(e)))

        threading.Thread(target=_play, daemon=True).start()

    # ------------------------------------------------------------------ #
    # Template                                                              #
    # ------------------------------------------------------------------ #

    def _set_template(self, key: str, path: str):
        try:
            self.detector.load_template(key, path)
            (self.start_path if key == "start" else self.stop_path).set(path)
            self._save_config()
        except Exception as e:
            messagebox.showerror("読み込みエラー", str(e))
            self._log_msg(f"テンプレート読み込みエラー: {e}")

    # ------------------------------------------------------------------ #
    # Monitoring                                                            #
    # ------------------------------------------------------------------ #

    def _toggle_monitoring(self):
        if not self._is_monitoring:
            if not self.obs.connected:
                messagebox.showwarning("警告", "先にOBSに接続してください")
                return
            if not self.detector.has_templates():
                messagebox.showwarning("警告", "トリガー音を少なくとも1つ設定してください")
                return
            try:
                self.detector.cooldown = float(self._cd.get())
            except ValueError:
                pass
            self._is_monitoring = True
            self._mon_btn.configure(text="モニタリング停止", fg_color="#c0392b", hover_color="#e74c3c")
            self._status_lbl.configure(text="モニタリング中...", text_color="#5dade2")
            self.detector.start_monitoring(
                sensitivity=self._sens.get(),
                on_start_triggered=self._on_start,
                on_stop_triggered=self._on_stop,
            )
            self._log_msg("モニタリング開始")
        else:
            self._is_monitoring = False
            self._mon_btn.configure(text="モニタリング開始", fg_color=("#3b8ed0", "#1f6aa5"),
                                     hover_color=("#36719f", "#144870"))
            self._status_lbl.configure(text="待機中", text_color="gray60")
            self.detector.stop_monitoring()
            self._log_msg("モニタリング停止")

    def _on_start(self):
        self._log_msg("開始トリガー検出 → 録画開始")
        self.root.after(0, lambda: self._status_lbl.configure(
            text="● 録画中", text_color="#e74c3c"))
        try:
            self.obs.start_recording()
        except Exception as e:
            self._log_msg(f"録画開始エラー: {e}")

    def _on_stop(self):
        self._log_msg("停止トリガー検出 → 録画停止")
        self.root.after(0, lambda: self._status_lbl.configure(
            text="モニタリング中...", text_color="#5dade2"))
        try:
            self.obs.stop_recording()
        except Exception as e:
            self._log_msg(f"録画停止エラー: {e}")

    def _on_similarity_update(self, trigger_type: str, score: float):
        bar = self._sim_bars.get(trigger_type)
        if bar:
            self.root.after(0, lambda: bar.set(max(0.0, min(1.0, score))))

    # ------------------------------------------------------------------ #
    # Config                                                                #
    # ------------------------------------------------------------------ #

    def _save_config(self):
        cfg = {
            "host":        self._host.get(),
            "port":        self._port.get(),
            "password":    self._pw.get(),
            "sensitivity": self._sens.get(),
            "cooldown":    self._cd.get(),
            "start_sound": self.start_path.get(),
            "stop_sound":  self.stop_path.get(),
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

    def _load_config(self):
        if not os.path.exists(CONFIG_FILE):
            return
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                cfg = json.load(f)
            self._host.set(cfg.get("host", "localhost"))
            self._port.set(cfg.get("port", "4455"))
            self._pw.set(cfg.get("password", ""))
            self._sens.set(cfg.get("sensitivity", 0.70))
            self._cd.set(cfg.get("cooldown", "2.0"))
            for key in ("start", "stop"):
                path = cfg.get(f"{key}_sound", "")
                if path and path != "未設定" and os.path.exists(path):
                    self._set_template(key, path)
        except Exception as e:
            self._log_msg(f"設定読み込みエラー: {e}")

    # ------------------------------------------------------------------ #
    # Logging                                                               #
    # ------------------------------------------------------------------ #

    def _log_msg(self, msg: str):
        def _write():
            self._log.configure(state="normal")
            self._log.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n")
            self._log.see("end")
            self._log.configure(state="disabled")

        if threading.current_thread() is threading.main_thread():
            _write()
        else:
            self.root.after(0, _write)


if __name__ == "__main__":
    root = ctk.CTk()
    App(root)
    root.mainloop()
