import threading
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk
import numpy as np
import sounddevice as sd
import soundfile as sf
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure


class WaveformEditor(ctk.CTkToplevel):
    """
    波形を表示してトリミング範囲を選択するポップアップ。
    左クリック = 開始点, 右クリック = 終了点 を波形上で設定できる。
    on_confirm(segment: np.ndarray, sr: int) がトリミング済み音声を受け取る。
    """

    def __init__(self, parent, filepath: str, on_confirm):
        super().__init__(parent)
        self.title("波形エディタ")
        self.resizable(True, False)
        self.grab_set()

        self._filepath = filepath
        self._on_confirm = on_confirm

        self._audio, self._sr = sf.read(filepath, dtype="float32")
        if self._audio.ndim > 1:
            self._audio = self._audio.mean(axis=1)
        self._duration = len(self._audio) / self._sr

        self._start_var = tk.DoubleVar(value=0.0)
        self._end_var   = tk.DoubleVar(value=self._duration)
        self._playing   = False

        self._build_ui()
        self._redraw()

    # ------------------------------------------------------------------ #

    def _build_ui(self):
        # ── 波形 ───────────────────────────────────────────────────────── #
        wave_frame = ctk.CTkFrame(self, fg_color="#12121a", corner_radius=8)
        wave_frame.pack(fill="x", padx=12, pady=(12, 4))

        self._fig = Figure(figsize=(7, 2.2), dpi=100, facecolor="#12121a")
        self._ax  = self._fig.add_subplot(111)
        self._fig.subplots_adjust(left=0.04, right=0.99, top=0.95, bottom=0.18)

        self._canvas = FigureCanvasTkAgg(self._fig, wave_frame)
        self._canvas.get_tk_widget().pack(fill="x")
        self._canvas.mpl_connect("button_press_event", self._on_canvas_click)

        hint = ctk.CTkLabel(wave_frame,
                             text="左クリック = 開始点  /  右クリック = 終了点",
                             font=ctk.CTkFont(size=10), text_color="gray55")
        hint.pack(pady=(0, 4))

        # ── スライダー ─────────────────────────────────────────────────── #
        sf_ = ctk.CTkFrame(self, fg_color="transparent")
        sf_.pack(fill="x", padx=12, pady=2)

        ctk.CTkLabel(sf_, text="開始", width=36).grid(row=0, column=0, sticky="w")
        ctk.CTkSlider(sf_, from_=0, to=self._duration, variable=self._start_var,
                       command=self._on_slider, width=360).grid(row=0, column=1, padx=6)
        self._start_lbl = ctk.CTkLabel(sf_, text="0.00 s", width=58, anchor="w")
        self._start_lbl.grid(row=0, column=2)

        ctk.CTkLabel(sf_, text="終了", width=36).grid(row=1, column=0, sticky="w")
        ctk.CTkSlider(sf_, from_=0, to=self._duration, variable=self._end_var,
                       command=self._on_slider, width=360).grid(row=1, column=1, padx=6)
        self._end_lbl = ctk.CTkLabel(sf_, text=f"{self._duration:.2f} s", width=58, anchor="w")
        self._end_lbl.grid(row=1, column=2)

        self._dur_lbl = ctk.CTkLabel(sf_, text="", text_color="#5dade2",
                                      font=ctk.CTkFont(size=11))
        self._dur_lbl.grid(row=2, column=0, columnspan=3, pady=(4, 0))

        # ── ボタン ─────────────────────────────────────────────────────── #
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(pady=10)

        self._preview_btn = ctk.CTkButton(
            btn_row, text="試聴", width=80,
            fg_color="gray35", hover_color="gray45",
            command=self._preview)
        self._preview_btn.pack(side="left", padx=5)

        ctk.CTkButton(
            btn_row, text="この範囲を使う", width=140,
            fg_color="#2e7d32", hover_color="#388e3c",
            command=self._confirm).pack(side="left", padx=5)

        ctk.CTkButton(
            btn_row, text="キャンセル", width=90,
            fg_color="#7f1d1d", hover_color="#b91c1c",
            command=self.destroy).pack(side="left", padx=5)

        self._update_labels()

    # ------------------------------------------------------------------ #

    def _on_slider(self, _=None):
        s, e = self._start_var.get(), self._end_var.get()
        if s >= e - 0.01:
            if _ == "start":
                self._start_var.set(e - 0.01)
            else:
                self._end_var.set(s + 0.01)
        self._update_labels()
        self._redraw()

    def _on_canvas_click(self, event):
        if event.xdata is None:
            return
        t = float(np.clip(event.xdata, 0, self._duration))
        if event.button == 1:
            self._start_var.set(min(t, self._end_var.get() - 0.01))
        elif event.button == 3:
            self._end_var.set(max(t, self._start_var.get() + 0.01))
        self._update_labels()
        self._redraw()

    def _update_labels(self):
        s, e = self._start_var.get(), self._end_var.get()
        self._start_lbl.configure(text=f"{s:.2f} s")
        self._end_lbl.configure(text=f"{e:.2f} s")
        self._dur_lbl.configure(text=f"選択長: {e - s:.2f} 秒")

    def _redraw(self):
        ax = self._ax
        ax.cla()

        t = np.linspace(0, self._duration, len(self._audio))
        ax.plot(t, self._audio, color="#3a7bd5", linewidth=0.5, zorder=2)

        s, e = self._start_var.get(), self._end_var.get()
        ax.axvspan(s, e, color="#50c87828", zorder=1)
        ax.axvline(s, color="#50c878", linewidth=1.8, zorder=3, label="開始")
        ax.axvline(e, color="#e74c3c", linewidth=1.8, zorder=3, label="終了")

        ax.set_facecolor("#0d0d17")
        ax.tick_params(colors="#666", labelsize=7)
        ax.set_xlim(0, self._duration)
        ax.set_ylim(-1.05, 1.05)
        ax.set_xlabel("秒", color="#666", fontsize=8)
        for spine in ax.spines.values():
            spine.set_edgecolor("#333")

        self._canvas.draw_idle()

    def _preview(self):
        if self._playing:
            sd.stop()
            self._playing = False
            self._preview_btn.configure(text="試聴")
            return
        s = int(self._start_var.get() * self._sr)
        e = int(self._end_var.get() * self._sr)
        segment = self._audio[s:e]

        self._playing = True
        self._preview_btn.configure(text="停止")

        def _play():
            sd.play(segment, self._sr)
            sd.wait()
            self._playing = False
            try:
                self.after(0, lambda: self._preview_btn.configure(text="試聴"))
            except Exception:
                pass

        threading.Thread(target=_play, daemon=True).start()

    def _confirm(self):
        s = int(self._start_var.get() * self._sr)
        e = int(self._end_var.get() * self._sr)
        segment = self._audio[s:e]
        if len(segment) < self._sr * 0.1:
            messagebox.showwarning("警告", "選択範囲が短すぎます（0.1秒以上必要）", parent=self)
            return
        self._on_confirm(segment, self._sr)
        self.destroy()
