#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
elforan2cdf — конвертер электрофореграмм Эльфоран (Люмэкс, Капель 105/205):
родные файлы .mdf и TXT-экспорт -> ANDI/AIA netCDF (.cdf, ASTM E1947).
Из .mdf переносятся также параметры анализа и таблица пиков.

Без внешних зависимостей: netCDF classic (v1) пишется вручную, работает
на Python 3.6+ (Windows/macOS/Linux) и легко собирается в .exe через PyInstaller.

Формат входа (как выгружает Эльфоран):
    Time  Signal  Current
    0.1  0.0000    0.0
    ...
Time — секунды, Signal — сигнал детектора, Current — ток, мкА.

Использование:
    elforan2cdf файл.mdf [файл.txt ...]        конвертировать файлы
    elforan2cdf папка                          все *.mdf и *.txt в папке
    elforan2cdf --watch папка                  следить за папкой и конвертировать новые
    elforan2cdf --current файл.txt             дополнительно сохранить ток в *_current.cdf
Можно просто перетащить файлы или папку на elforan2cdf-cli.exe.
"""

import argparse
import datetime as _dt
import os
import struct
import sys
import time

__version__ = "1.1.0"

# ---------------------------------------------------------------------------
# Минимальный писатель netCDF classic (CDF-1), только фиксированные измерения
# ---------------------------------------------------------------------------

NC_CHAR, NC_SHORT, NC_INT, NC_FLOAT, NC_DOUBLE = 2, 3, 4, 5, 6
_NC_DIMENSION, _NC_VARIABLE, _NC_ATTRIBUTE = 0x0A, 0x0B, 0x0C
_TYPE_FMT = {NC_SHORT: "h", NC_INT: "i", NC_FLOAT: "f", NC_DOUBLE: "d"}
_TYPE_SIZE = {NC_CHAR: 1, NC_SHORT: 2, NC_INT: 4, NC_FLOAT: 4, NC_DOUBLE: 8}


def _pad4(b):
    return b + b"\x00" * ((4 - len(b) % 4) % 4)


def _name(s):
    b = s.encode("ascii")
    return struct.pack(">i", len(b)) + _pad4(b)


# Кодировка строк в CDF. UTF-8 — соглашение netCDF (OpenChrom, Python, MATLAB…);
# cp1251 — для старого Windows-ПО, которое читает строки как ANSI.
TEXT_ENCODING = "utf-8"


def _enc_text(s, limit=None):
    b = s.encode(TEXT_ENCODING, errors="replace")
    if limit is not None and len(b) > limit:
        # обрезаем, не разрывая многобайтовый символ
        b = b[:limit].decode(TEXT_ENCODING, errors="ignore").encode(TEXT_ENCODING)
    return b


def _attr_value(v):
    if isinstance(v, str):
        b = _enc_text(v)
        return NC_CHAR, len(b), _pad4(b)
    if isinstance(v, float):
        return NC_DOUBLE, 1, struct.pack(">d", v)
    if isinstance(v, int):
        return NC_INT, 1, struct.pack(">i", v)
    raise TypeError("unsupported attribute type: %r" % (v,))


def _attr_list(attrs):
    if not attrs:
        return b"\x00" * 8
    out = struct.pack(">ii", _NC_ATTRIBUTE, len(attrs))
    for k, v in attrs:
        t, n, data = _attr_value(v)
        out += _name(k) + struct.pack(">ii", t, n) + data
    return out


class NetCDFWriter(object):
    def __init__(self):
        self.dims = []      # [(name, size)]
        self.gattrs = []    # [(name, value)]
        self.vars = []      # [dict]

    def dim(self, name, size):
        self.dims.append((name, int(size)))

    def attr(self, name, value):
        self.gattrs.append((name, value))

    def var(self, name, nc_type, dims, data, attrs=()):
        """data: число, список чисел или строка/список строк для NC_CHAR."""
        idx = {d[0]: i for i, d in enumerate(self.dims)}
        dimids = [idx[d] for d in dims]
        shape = [self.dims[i][1] for i in dimids]
        count = 1
        for s in shape:
            count *= s
        if nc_type == NC_CHAR:
            if isinstance(data, str):
                data = [data]
            width = shape[-1] if shape else 1
            raw = b"".join(_enc_text(s, width).ljust(width, b"\x00") for s in data)
            raw = raw.ljust(count, b"\x00")
        else:
            if not isinstance(data, (list, tuple)):
                data = [data]
            if len(data) != count:
                raise ValueError("%s: expected %d values, got %d" % (name, count, len(data)))
            raw = struct.pack(">%d%s" % (count, _TYPE_FMT[nc_type]), *data)
        self.vars.append(dict(name=name, type=nc_type, dimids=dimids,
                              attrs=list(attrs), raw=_pad4(raw)))

    def _header(self, offsets):
        h = b"CDF\x01" + struct.pack(">i", 0)
        if self.dims:
            h += struct.pack(">ii", _NC_DIMENSION, len(self.dims))
            for n, s in self.dims:
                h += _name(n) + struct.pack(">i", s)
        else:
            h += b"\x00" * 8
        h += _attr_list(self.gattrs)
        if self.vars:
            h += struct.pack(">ii", _NC_VARIABLE, len(self.vars))
            for v, off in zip(self.vars, offsets):
                h += _name(v["name"]) + struct.pack(">i", len(v["dimids"]))
                h += b"".join(struct.pack(">i", d) for d in v["dimids"])
                h += _attr_list(v["attrs"])
                h += struct.pack(">iii", v["type"], len(v["raw"]), off)
        else:
            h += b"\x00" * 8
        return h

    def write(self, path):
        hlen = len(self._header([0] * len(self.vars)))
        offsets, pos = [], hlen
        for v in self.vars:
            offsets.append(pos)
            pos += len(v["raw"])
        if pos > 2 ** 31 - 1:
            raise ValueError("file too large for netCDF classic")
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(self._header(offsets))
            for v in self.vars:
                f.write(v["raw"])
        os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Общая модель данных
# ---------------------------------------------------------------------------

class Peak(object):
    def __init__(self, name, time, start, end, height, area, area_pct=None,
                 amount=None, asymmetry=None, efficiency=None):
        self.name, self.time, self.start, self.end = name, time, start, end
        self.height, self.area, self.area_pct = height, area, area_pct
        self.amount, self.asymmetry, self.efficiency = amount, asymmetry, efficiency


class Chromatogram(object):
    def __init__(self):
        self.times = []           # секунды
        self.signal = []          # сигнал детектора
        self.signal_unit = "mAU"
        self.current = None       # ток, мкА (если есть)
        self.injection_dt = None  # datetime с часовым поясом
        self.operator = ""
        self.instrument = "Kapel"
        self.detector = "Kapel UV detector"
        self.detection_method = ""
        self.comments = ""
        self.peaks = []
        self.source_format = ""


def _local_tz_dt(naive):
    return naive.replace(tzinfo=_dt.datetime.now().astimezone().tzinfo)


# ---------------------------------------------------------------------------
# Чтение TXT Эльфорана
# ---------------------------------------------------------------------------

def _num(tok):
    return float(tok.replace(",", "."))


def read_elforan_txt(path, signal_unit="mAU"):
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "cp1251"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    rows = []
    for line in text.splitlines():
        parts = line.replace(";", " ").replace("\t", " ").split()
        if not parts:
            continue
        try:
            vals = [_num(p) for p in parts]
        except ValueError:
            if not rows:
                continue   # строка заголовка
            raise ValueError("не удалось разобрать строку: %r" % line)
        rows.append(vals)
    if len(rows) < 2:
        raise ValueError("в файле нет данных")
    ncol = min(len(r) for r in rows)
    if ncol < 2:
        raise ValueError("ожидаются минимум 2 колонки (время, сигнал)")
    chrom = Chromatogram()
    chrom.source_format = "Elforan TXT export"
    chrom.times = [r[0] for r in rows]
    chrom.signal = [r[1] for r in rows]
    chrom.signal_unit = signal_unit
    if ncol > 2:
        chrom.current = [r[2] for r in rows]
    return chrom


# ---------------------------------------------------------------------------
# Чтение родного файла Эльфорана .mdf
# ---------------------------------------------------------------------------
# Структура: INI-подобный текст (cp1251) до строки "[Begin]", затем двоичные
# массивы little-endian в порядке секций [Array ...] (размер Size, тип Type),
# в конце 4 байта контрольной суммы. Ток (int32) хранится в 0.1 мкА.

_MDF_TYPES = {"real64": "d", "real32": "f", "int32": "i", "int16": "h",
              "uint16": "H", "uint32": "I"}


def _parse_ini(text):
    sections, order, cur = {}, [], None
    for line in text.splitlines():
        line = line.rstrip("\r")
        if line.startswith("[") and line.endswith("]"):
            cur = line[1:-1]
            sections[cur] = {}
            order.append(cur)
        elif cur is not None and "=" in line:
            k, v = line.split("=", 1)
            sections[cur][k.strip()] = v.strip()
    return sections, order


def _f(s, default=None):
    try:
        return float(str(s).replace(",", "."))
    except (TypeError, ValueError):
        return default


def read_mdf(path):
    with open(path, "rb") as f:
        raw = f.read()
    marker = b"[Begin]\n"
    k = raw.find(b"\n" + marker)
    if not raw.startswith(b"[") or k < 0:
        raise ValueError("не похоже на файл Эльфорана .mdf (нет секции [Begin])")
    begin = k + 1 + len(marker)
    sec, order = _parse_ini(raw[:k].decode("cp1251", errors="replace"))

    iv = sec.get("Interval Time", {})
    t0, step = _f(iv.get("From")), _f(iv.get("Step"))
    if step is None or step <= 0:
        raise ValueError("в .mdf не задан шаг по времени ([Interval Time])")
    if t0 is None:
        t0 = step
    tscale = 60.0 if iv.get("Units", "sec").lower().startswith("min") else 1.0

    # двоичные массивы
    arrays, pos = {}, begin
    for name in order:
        if not name.startswith("Array "):
            continue
        a = sec[name]
        fmt = _MDF_TYPES.get(a.get("Type", "").lower())
        n = int(_f(a.get("Size"), 0))
        if fmt is None:
            raise ValueError("неизвестный тип массива %r в %s" % (a.get("Type"), name))
        size = struct.calcsize("<" + fmt) * n
        if pos + size > len(raw):
            raise ValueError("файл обрезан: не хватает данных массива %s" % name)
        arrays[name[6:].strip().lower()] = (list(struct.unpack("<%d%s" % (n, fmt), raw[pos:pos + size])), a)
        pos += size

    sig_key = "photometric" if "photometric" in arrays else next(
        (key for key in arrays if key != "current"), None)
    if sig_key is None:
        raise ValueError("в .mdf нет массива сигнала")
    sig, sig_meta = arrays[sig_key]
    n = len(sig)

    chrom = Chromatogram()
    chrom.source_format = "Elforan MDF"
    chrom.times = [(t0 + i * step) * tscale for i in range(n)]
    chrom.signal = [float(v) for v in sig]
    chrom.signal_unit = sig_meta.get("Units", "mAU") or "mAU"
    if "current" in arrays:
        cur, cur_meta = arrays["current"]
        scale = 0.1 if cur_meta.get("Type", "").lower().startswith("int") else 1.0
        chrom.current = [v * scale for v in cur[:n]]

    m = sec.get("Measurement", {})
    p = sec.get("Parameters", {})
    chrom.operator = m.get("Operator", "")
    instr = m.get("Instrument", "Kapel")
    if m.get("Serial Number"):
        instr += " s/n " + m["Serial Number"]
    chrom.instrument = instr
    wave = p.get("Wave", "")
    chrom.detector = "%s UV detector" % m.get("Instrument", "Kapel")
    chrom.detection_method = ("UV %s nm" % wave) if wave else ""
    try:
        chrom.injection_dt = _local_tz_dt(
            _dt.datetime.strptime(" ".join(m.get("Time", "").split()), "%d.%m.%Y %H:%M:%S"))
    except ValueError:
        pass

    info = []
    if p.get("Cappillary number"):
        info.append("capillary %s" % p["Cappillary number"])
    if p.get("Internal diameter"):
        info.append("ID %s um" % p["Internal diameter"])
    if p.get("Overall length"):
        info.append("L %s/%s cm" % (p["Overall length"], p.get("Useful length", "?")))
    if p.get("Fixed temperature"):
        info.append("%s C" % p["Fixed temperature"])
    if wave:
        info.append("%s nm" % wave)
    cm = sec.get("Commentary measurement", {})
    texts = [v for key, v in sorted(cm.items()) if key.startswith("string_comment") and v]
    texts += [v for key, v in sorted(sec.get("Commentary autosampler", {}).items()) if v]
    chrom.comments = "; ".join(info + texts)

    # таблица пиков. Time/Region_* — номера точек с 1; Square — сумма по точкам
    # (mAU·точка), переводим в mAU·с умножением на шаг.
    def t_of(idx):
        return (t0 + (idx - 1) * step) * tscale

    area_k = step * tscale

    for name in order:
        if not name.startswith("Point_find") or name == "Point_finder":
            continue
        pk = sec[name]
        if pk.get("Enabled", "1") == "0" or _f(pk.get("Time")) is None:
            continue
        chrom.peaks.append(Peak(
            name=pk.get("Name", ""),
            time=t_of(_f(pk["Time"])),
            start=t_of(_f(pk.get("Region_begin"), _f(pk["Time"]))),
            end=t_of(_f(pk.get("Region_end"), _f(pk["Time"]))),
            height=_f(pk.get("Amplitude"), 0.0),
            area=_f(pk.get("Square"), 0.0) * area_k,
            area_pct=_f(pk.get("PSV")),
            amount=_f(pk.get("Concentration")),
            asymmetry=_f(pk.get("As")),
            efficiency=_f(pk.get("TT")),
        ))
    chrom.peaks.sort(key=lambda x: x.time)
    return chrom


def read_any(path, signal_unit="mAU"):
    if path.lower().endswith(".mdf"):
        return read_mdf(path)
    return read_elforan_txt(path, signal_unit)


# ---------------------------------------------------------------------------
# Сборка ANDI/AIA Chromatography netCDF
# ---------------------------------------------------------------------------

def _stamp(dt):
    off = dt.utcoffset() or _dt.timedelta(0)
    mins = int(off.total_seconds() // 60)
    sign = "+" if mins >= 0 else "-"
    mins = abs(mins)
    return dt.strftime("%Y%m%d%H%M%S") + "%s%02d%02d" % (sign, mins // 60, mins % 60)


def _guess_injection_time(basename, fallback):
    """Имя вида '..._240320_0215_2' → 2024-03-20 02:15 (как называет Эльфоран)."""
    import re
    m = re.search(r"_(\d{6})_(\d{4})(?:_\d+)?$", basename)
    if m:
        try:
            d = _dt.datetime.strptime(m.group(1) + m.group(2), "%y%m%d%H%M")
            return d.replace(tzinfo=fallback.tzinfo)
        except ValueError:
            pass
    return fallback


def build_andi(chrom, values, unit, detector_name, sample_name, source_file,
               injection_dt, peaks=()):
    times = chrom.times
    n = len(values)
    if n < 2:
        raise ValueError("слишком мало точек")
    dts = [b - a for a, b in zip(times, times[1:])]
    interval = sum(dts) / len(dts)
    uniform = all(abs(d - interval) <= 1e-6 + 1e-3 * interval for d in dts)
    now = _dt.datetime.now().astimezone()
    peaks = list(peaks)

    w = NetCDFWriter()
    for size in (2, 4, 8, 16, 32, 64, 128, 255):
        w.dim("_%d_byte_string" % size, size)
    w.dim("range", 2)
    w.dim("point_number", n)
    w.dim("error_number", 1)
    if peaks:
        w.dim("peak_number", len(peaks))

    comments = "Converted from %s by elforan2cdf %s" % (chrom.source_format, __version__)
    for k, v in [
        ("dataset_completeness", "C1+C2" if peaks else "C1"),
        ("aia_template_revision", "1.0"),
        ("netcdf_revision", "2.3"),
        ("languages", "English"),
        ("administrative_comments", comments),
        ("dataset_origin", chrom.instrument + " / Elforan"),
        ("dataset_owner", ""),
        ("dataset_date_time_stamp", _stamp(now)),
        ("injection_date_time_stamp", _stamp(injection_dt)),
        ("experiment_title", sample_name),
        ("operator_name", chrom.operator),
        ("separation_experiment_type", "capillary electrophoresis"),
        ("company_method_name", ""),
        ("company_method_id", ""),
        ("pre_experiment_program_name", ""),
        ("post_experiment_program_name", ""),
        ("source_file_reference", source_file),
        ("sample_id_comments", chrom.comments),
        ("sample_id", sample_name),
        ("sample_name", sample_name),
        ("sample_type", "unknown"),
        ("sample_injection_volume", 0.0),
        ("sample_amount", 0.0),
        ("detection_method_table_name", ""),
        ("detection_method_comments", chrom.comments),
        ("detection_method_name", chrom.detection_method),
        ("detector_name", detector_name),
        ("detector_maximum_value", float(max(values))),
        ("detector_minimum_value", float(min(values))),
        ("detector_unit", unit),
        ("raw_data_table_name", sample_name),
        ("retention_unit", "seconds"),
        ("peak_processing_results_table_name", "Elforan" if peaks else ""),
        ("peak_processing_results_comments",
         ("peaks as integrated in Elforan; peak_area in %s*s "
          "(Elforan 'Square' = peak_area / sampling interval)" % unit) if peaks else ""),
        ("peak_processing_method_name", ""),
        ("peak_processing_date_time_stamp", ""),
        ("peak_amount_unit", ""),
    ]:
        w.attr(k, v)

    w.var("error_log", NC_CHAR, ["error_number", "_64_byte_string"], "")
    w.var("detector_maximum_value", NC_FLOAT, [], float(max(values)))
    w.var("detector_minimum_value", NC_FLOAT, [], float(min(values)))
    w.var("actual_run_time_length", NC_FLOAT, [], float(times[-1] - times[0] + interval))
    w.var("actual_sampling_interval", NC_FLOAT, [], float(interval))
    w.var("actual_delay_time", NC_FLOAT, [], float(times[0]))
    w.var("ordinate_values", NC_FLOAT, ["point_number"], [float(x) for x in values],
          attrs=[("uniform_sampling_flag", "Y" if uniform else "N"),
                 ("autosampler_position", "")])
    if not uniform:
        w.var("raw_data_retention", NC_FLOAT, ["point_number"], [float(t) for t in times])

    if peaks:
        def nearest(t):
            i = int(round((t - times[0]) / interval)) if uniform else min(
                range(n), key=lambda j: abs(times[j] - t))
            return float(values[max(0, min(n - 1, i))])

        tot_h = sum(p.height for p in peaks) or 1.0
        tot_a = sum(p.area for p in peaks) or 1.0
        pn = ["peak_number"]
        fl = lambda xs: [float(x) for x in xs]  # noqa
        w.var("peak_retention_time", NC_FLOAT, pn, fl(p.time for p in peaks))
        name_dim = "_32_byte_string" if all(
            len(_enc_text(p.name)) <= 32 for p in peaks) else "_64_byte_string"
        w.var("peak_name", NC_CHAR, pn + [name_dim], [p.name for p in peaks])
        w.var("peak_amount", NC_FLOAT, pn, fl(p.amount or 0.0 for p in peaks))
        w.var("peak_start_time", NC_FLOAT, pn, fl(p.start for p in peaks))
        w.var("peak_end_time", NC_FLOAT, pn, fl(p.end for p in peaks))
        w.var("peak_width", NC_FLOAT, pn, fl(p.end - p.start for p in peaks))
        w.var("peak_area", NC_FLOAT, pn, fl(p.area for p in peaks))
        w.var("peak_area_percent", NC_FLOAT, pn,
              fl(p.area_pct if p.area_pct is not None else 100.0 * p.area / tot_a for p in peaks))
        w.var("peak_height", NC_FLOAT, pn, fl(p.height for p in peaks))
        w.var("peak_height_percent", NC_FLOAT, pn, fl(100.0 * p.height / tot_h for p in peaks))
        w.var("baseline_start_time", NC_FLOAT, pn, fl(p.start for p in peaks))
        w.var("baseline_start_value", NC_FLOAT, pn, [nearest(p.start) for p in peaks])
        w.var("baseline_stop_time", NC_FLOAT, pn, fl(p.end for p in peaks))
        w.var("baseline_stop_value", NC_FLOAT, pn, [nearest(p.end) for p in peaks])
        w.var("peak_start_detection_code", NC_CHAR, pn + ["_2_byte_string"], ["B"] * len(peaks))
        w.var("peak_stop_detection_code", NC_CHAR, pn + ["_2_byte_string"], ["B"] * len(peaks))
        w.var("migration_time", NC_FLOAT, pn, fl(p.time for p in peaks))
        w.var("peak_asymmetry", NC_FLOAT, pn, fl(p.asymmetry or 0.0 for p in peaks))
        w.var("peak_efficiency", NC_FLOAT, pn, fl(p.efficiency or 0.0 for p in peaks))
    return w


def convert(path, out_dir=None, with_current=False, signal_unit="mAU", verbose=True,
            with_peaks=True):
    chrom = read_any(path, signal_unit)
    base = os.path.splitext(os.path.basename(path))[0]
    out_dir = out_dir or os.path.dirname(os.path.abspath(path))
    mtime = _dt.datetime.fromtimestamp(os.path.getmtime(path)).astimezone()
    inj = chrom.injection_dt or _guess_injection_time(base, mtime)

    jobs = [(chrom.signal, chrom.signal_unit, chrom.detector, "",
             chrom.peaks if with_peaks else [])]
    if with_current and chrom.current:
        jobs.append((chrom.current, "uA", "Kapel current", "_current", []))
    outputs = []
    for values, unit, det, suffix, peaks in jobs:
        out = os.path.join(out_dir, base + suffix + ".cdf")
        build_andi(chrom, values, unit, det, base, os.path.basename(path), inj, peaks).write(out)
        outputs.append(out)
        if verbose:
            extra = (", пиков: %d" % len(peaks)) if peaks else ""
            print("OK  %s -> %s  (%d точек%s)" % (os.path.basename(path), os.path.basename(out),
                                                  len(values), extra))
    return outputs


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

INPUT_EXT = (".txt", ".mdf")


def _collect(paths):
    files = []
    for p in paths:
        if os.path.isdir(p):
            files += [os.path.join(p, f) for f in sorted(os.listdir(p))
                      if f.lower().endswith(INPUT_EXT)]
        else:
            files.append(p)
    return files


def _watch(folder, args):
    print("Слежу за папкой %s (Ctrl+C — выход)" % folder)
    seen = {}
    while True:
        try:
            for f in _collect([folder]):
                try:
                    st = os.stat(f)
                except OSError:
                    continue
                key = (st.st_mtime, st.st_size)
                if seen.get(f) == key:
                    continue
                # ждём, пока файл перестанет расти
                if time.time() - st.st_mtime < 2:
                    continue
                seen[f] = key
                cdf = os.path.join(args.out or os.path.dirname(f),
                                   os.path.splitext(os.path.basename(f))[0] + ".cdf")
                if os.path.exists(cdf) and os.path.getmtime(cdf) >= st.st_mtime:
                    continue
                try:
                    convert(f, args.out, args.current, args.unit, with_peaks=not args.no_peaks)
                except Exception as e:  # noqa
                    print("ERR %s: %s" % (os.path.basename(f), e))
            time.sleep(2)
        except KeyboardInterrupt:
            return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="elforan2cdf",
        description="Конвертер файлов Эльфоран (Капель): .mdf и TXT-экспорт -> ANDI/AIA netCDF (.cdf)")
    ap.add_argument("paths", nargs="*", help=".mdf / .txt файлы или папки")
    ap.add_argument("-o", "--out", help="папка для .cdf (по умолчанию рядом с исходным)")
    ap.add_argument("--current", action="store_true",
                    help="дополнительно записать ток в отдельный *_current.cdf")
    ap.add_argument("--no-peaks", action="store_true",
                    help="не переносить таблицу пиков из .mdf")
    ap.add_argument("--unit", default="mAU",
                    help="единица сигнала для TXT (в .mdf берётся из файла), по умолчанию mAU")
    ap.add_argument("--encoding", default="utf-8", choices=("utf-8", "cp1251"),
                    help="кодировка русских строк в CDF (по умолчанию utf-8)")
    ap.add_argument("--watch", metavar="ПАПКА", help="следить за папкой и конвертировать новые файлы")
    ap.add_argument("--version", action="version", version=__version__)
    args = ap.parse_args(argv)
    global TEXT_ENCODING
    TEXT_ENCODING = args.encoding

    if args.out:
        os.makedirs(args.out, exist_ok=True)
    if args.watch:
        return _watch(args.watch, args)
    if not args.paths:
        ap.print_help()
        if getattr(sys, "frozen", False):
            input("\nПеретащите .mdf или TXT-файлы на программу. Enter — выход...")
        return 1

    errors = 0
    for f in _collect(args.paths):
        try:
            convert(f, args.out, args.current, args.unit, with_peaks=not args.no_peaks)
        except Exception as e:  # noqa
            errors += 1
            print("ERR %s: %s" % (os.path.basename(f), e))
    if getattr(sys, "frozen", False):
        input("\nГотово. Enter — выход...")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
