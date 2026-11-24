#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Окно для конвертера Эльфоран TXT -> ANDI CDF (tkinter, без доп. библиотек)."""

import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import elforan2cdf as core

APP_TITLE = "Эльфоран → CDF"


class App(object):
    def __init__(self, root, initial_paths=()):
        self.root = root
        self.q = queue.Queue()
        self.busy = False
        self.watch_stop = None

        root.title("%s  v%s" % (APP_TITLE, core.__version__))
        root.minsize(640, 520)
        try:
            root.tk.call("tk", "scaling", max(1.0, root.winfo_fpixels("1i") / 72.0))
        except tk.TclError:
            pass

        pad = dict(padx=8, pady=4)
        main = ttk.Frame(root, padding=8)
        main.pack(fill="both", expand=True)

        # --- Список файлов
        ttk.Label(main, text="Файлы Эльфорана (.mdf или выгруженные .txt):").grid(row=0, column=0, sticky="w")
        lf = ttk.Frame(main)
        lf.grid(row=1, column=0, columnspan=3, sticky="nsew")
        self.files = tk.Listbox(lf, selectmode="extended", height=9, activestyle="none")
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.files.yview)
        self.files.configure(yscrollcommand=sb.set)
        self.files.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.files.bind("<Delete>", lambda e: self.remove_selected())
        self.files.bind("<BackSpace>", lambda e: self.remove_selected())

        bf = ttk.Frame(main)
        bf.grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 8))
        ttk.Button(bf, text="Добавить файлы…", command=self.add_files).pack(side="left")
        ttk.Button(bf, text="Добавить папку…", command=self.add_folder).pack(side="left", padx=6)
        ttk.Button(bf, text="Убрать выбранные", command=self.remove_selected).pack(side="left")
        ttk.Button(bf, text="Очистить", command=lambda: self.files.delete(0, "end")).pack(side="left", padx=6)

        # --- Настройки
        opt = ttk.LabelFrame(main, text="Настройки", padding=6)
        opt.grid(row=3, column=0, columnspan=3, sticky="ew")
        self.out_mode = tk.StringVar(value="same")
        self.out_dir = tk.StringVar(value="")
        ttk.Radiobutton(opt, text="Сохранять .cdf рядом с исходным файлом", variable=self.out_mode,
                        value="same", command=self._sync).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Radiobutton(opt, text="Сохранять в папку:", variable=self.out_mode,
                        value="dir", command=self._sync).grid(row=1, column=0, sticky="w")
        self.out_entry = ttk.Entry(opt, textvariable=self.out_dir, width=46)
        self.out_entry.grid(row=1, column=1, sticky="ew", **pad)
        self.out_btn = ttk.Button(opt, text="Обзор…", command=self.pick_out)
        self.out_btn.grid(row=1, column=2, sticky="w")

        self.current = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt, text="Дополнительно сохранять ток (Current) в *_current.cdf",
                        variable=self.current).grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.peaks = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="Переносить таблицу пиков из .mdf (время, высота, площадь, название)",
                        variable=self.peaks).grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        uf = ttk.Frame(opt)
        uf.grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Label(uf, text="Единица сигнала для TXT:").pack(side="left")
        self.unit = tk.StringVar(value="mAU")
        ttk.Combobox(uf, textvariable=self.unit, width=8,
                     values=("mAU", "AU", "mV", "V", "counts")).pack(side="left", padx=6)
        ttk.Label(uf, text="   Кодировка текста:").pack(side="left")
        self.encoding = tk.StringVar(value="utf-8")
        ttk.Combobox(uf, textvariable=self.encoding, width=8, state="readonly",
                     values=("utf-8", "cp1251")).pack(side="left", padx=6)
        opt.columnconfigure(1, weight=1)

        # --- Кнопки действия
        af = ttk.Frame(main)
        af.grid(row=4, column=0, columnspan=3, sticky="ew", pady=8)
        self.go_btn = ttk.Button(af, text="Конвертировать", command=self.start)
        self.go_btn.pack(side="left")
        self.watch_btn = ttk.Button(af, text="Следить за папкой…", command=self.toggle_watch)
        self.watch_btn.pack(side="left", padx=6)
        self.progress = ttk.Progressbar(af, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=(6, 0))

        # --- Журнал
        self.log = tk.Text(main, height=8, state="disabled", wrap="none")
        self.log.grid(row=5, column=0, columnspan=3, sticky="nsew")
        self.log.tag_configure("err", foreground="#c0392b")
        self.log.tag_configure("ok", foreground="#1e7e34")

        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)
        main.rowconfigure(5, weight=1)

        self._sync()
        self.add_paths(initial_paths)
        self.root.after(100, self._poll)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    # ----- список
    def add_paths(self, paths):
        have = set(self.files.get(0, "end"))
        for f in core._collect(list(paths)):
            f = os.path.abspath(f)
            if f not in have:
                self.files.insert("end", f)
                have.add(f)

    def add_files(self):
        fs = filedialog.askopenfilenames(title="Выберите файлы Эльфорана",
                                         filetypes=[("Эльфоран", "*.mdf *.txt"),
                                                    ("Измерения Эльфоран", "*.mdf"),
                                                    ("TXT-экспорт", "*.txt"),
                                                    ("Все файлы", "*.*")])
        self.add_paths(fs)

    def add_folder(self):
        d = filedialog.askdirectory(title="Папка с файлами .mdf / .txt")
        if d:
            self.add_paths([d])

    def remove_selected(self):
        for i in reversed(self.files.curselection()):
            self.files.delete(i)

    def pick_out(self):
        d = filedialog.askdirectory(title="Куда сохранять .cdf")
        if d:
            self.out_dir.set(d)
            self.out_mode.set("dir")
            self._sync()

    def _sync(self):
        st = "normal" if self.out_mode.get() == "dir" else "disabled"
        self.out_entry.configure(state=st)
        self.out_btn.configure(state=st)

    def _out(self):
        if self.out_mode.get() != "dir":
            return None
        d = self.out_dir.get().strip()
        if not d:
            raise ValueError("Не выбрана папка для сохранения")
        os.makedirs(d, exist_ok=True)
        return d

    # ----- журнал
    def write(self, text, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self.write(*payload)
                elif kind == "progress":
                    self.progress["value"] = payload
                elif kind == "done":
                    ok, bad = payload
                    self.busy = False
                    self.go_btn.configure(state="normal")
                    self.write("Готово: успешно %d, ошибок %d" % (ok, bad), "err" if bad else "ok")
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    # ----- конвертация
    def _settings(self):
        """Читается в главном потоке: Tk-переменные нельзя трогать из фоновых."""
        core.TEXT_ENCODING = self.encoding.get() or "utf-8"
        return self._out(), self.current.get(), self.unit.get().strip() or "mAU", self.peaks.get()

    def _convert_one(self, f, cfg):
        out, with_current, unit, with_peaks = cfg
        try:
            res = core.convert(f, out, with_current, unit, verbose=False, with_peaks=with_peaks)
            self.q.put(("log", ("OK   %s → %s" % (os.path.basename(f),
                                                  ", ".join(os.path.basename(r) for r in res)), "ok")))
            return True
        except Exception as e:  # noqa
            self.q.put(("log", ("ОШИБКА  %s: %s" % (os.path.basename(f), e), "err")))
            return False

    def start(self):
        if self.busy:
            return
        files = list(self.files.get(0, "end"))
        if not files:
            messagebox.showinfo(APP_TITLE, "Добавьте файлы .mdf или .txt для конвертации.")
            return
        try:
            cfg = self._settings()
        except Exception as e:  # noqa
            messagebox.showerror(APP_TITLE, str(e))
            return
        self.busy = True
        self.go_btn.configure(state="disabled")
        self.progress.configure(maximum=len(files), value=0)

        def work():
            ok = bad = 0
            for i, f in enumerate(files, 1):
                if self._convert_one(f, cfg):
                    ok += 1
                else:
                    bad += 1
                self.q.put(("progress", i))
            self.q.put(("done", (ok, bad)))

        threading.Thread(target=work, daemon=True).start()

    # ----- слежение за папкой
    def toggle_watch(self):
        if self.watch_stop is not None:
            self.watch_stop.set()
            self.watch_stop = None
            self.watch_btn.configure(text="Следить за папкой…")
            self.write("Слежение остановлено")
            return
        folder = filedialog.askdirectory(title="Папка, куда Эльфоран сохраняет измерения")
        if not folder:
            return
        try:
            cfg = self._settings()
        except Exception as e:  # noqa
            messagebox.showerror(APP_TITLE, str(e))
            return
        out = cfg[0]
        stop = threading.Event()
        self.watch_stop = stop
        self.watch_btn.configure(text="Остановить слежение")
        self.write("Слежу за папкой: %s" % folder)

        def work():
            seen = {}
            while not stop.is_set():
                for f in core._collect([folder]):
                    try:
                        st = os.stat(f)
                    except OSError:
                        continue
                    key = (st.st_mtime, st.st_size)
                    if seen.get(f) == key or time.time() - st.st_mtime < 2:
                        continue
                    seen[f] = key
                    cdf = os.path.join(out or os.path.dirname(f),
                                       os.path.splitext(os.path.basename(f))[0] + ".cdf")
                    if os.path.exists(cdf) and os.path.getmtime(cdf) >= st.st_mtime:
                        continue
                    self._convert_one(f, cfg)
                stop.wait(2)

        threading.Thread(target=work, daemon=True).start()

    def close(self):
        if self.watch_stop is not None:
            self.watch_stop.set()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root, [p for p in sys.argv[1:] if os.path.exists(p)])
    root.mainloop()


if __name__ == "__main__":
    main()
