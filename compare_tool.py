#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PlexiPlay Media Check : examine un fichier vidéo, le compare à un fichier qui fonctionne,
puis le réencode avec les mêmes caractéristiques.

S'appuie sur ffprobe / ffmpeg (embarqués dans la version Windows).
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading

APP_NAME = "PlexiPlay Media Check"
APP_VERSION = "1.0.0"

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# --------------------------------------------------------------------------- outils

def find_tool(name):
    """ffmpeg / ffprobe : d'abord ceux embarqués dans l'exe, puis ceux du système."""
    exe = name + (".exe" if os.name == "nt" else "")
    base = getattr(sys, "_MEIPASS", None)
    if base and os.path.exists(os.path.join(base, exe)):
        return os.path.join(base, exe)
    here = os.path.dirname(os.path.abspath(sys.argv[0] if sys.argv else __file__))
    for folder in (here, os.path.dirname(os.path.abspath(__file__))):
        local = os.path.join(folder, exe)
        if os.path.exists(local):
            return local
    return shutil.which(name)


def run_capture(cmd):
    return subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW
    )


# --------------------------------------------------------------------------- analyse

def top_level_atoms(path):
    """Liste des blocs de premier niveau d'un MP4/MOV (pour savoir si moov est avant mdat)."""
    atoms = []
    try:
        size_total = os.path.getsize(path)
        with open(path, "rb") as f:
            pos = 0
            while pos + 8 <= size_total:
                f.seek(pos)
                head = f.read(16)
                if len(head) < 8:
                    break
                size = int.from_bytes(head[0:4], "big")
                kind = head[4:8].decode("latin1")
                if size == 1 and len(head) >= 16:
                    size = int.from_bytes(head[8:16], "big")
                elif size == 0:
                    size = size_total - pos
                if size < 8:
                    break
                atoms.append(kind)
                pos += size
    except OSError:
        pass
    return atoms


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fps(text):
    if not text or "/" not in str(text):
        return _num(text)
    a, b = str(text).split("/", 1)
    a, b = _num(a), _num(b)
    return a / b if b else 0.0


def probe(path):
    """Caractéristiques d'un fichier, sous forme de dictionnaire."""
    ffprobe = find_tool("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe est introuvable.")
    out = run_capture([ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", path])
    if out.returncode != 0:
        raise RuntimeError(out.stderr.decode("utf-8", "replace").strip() or "Fichier illisible.")
    data = json.loads(out.stdout.decode("utf-8", "replace"))
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    subs = [s for s in streams if s.get("codec_type") == "subtitle"]

    info = {
        "path": path,
        "name": os.path.basename(path),
        "size": int(_num(fmt.get("size"))),
        "duration": _num(fmt.get("duration")),
        "bitrate": int(_num(fmt.get("bit_rate"))),
        "container": fmt.get("format_name", ""),
        "ext": os.path.splitext(path)[1].lower(),
        "subs": len(subs),
        "video": None,
        "audio": None,
        "faststart": None,
    }

    if info["ext"] in (".mp4", ".m4v", ".mov", ".m4a"):
        atoms = top_level_atoms(path)
        if "moov" in atoms and "mdat" in atoms:
            info["faststart"] = atoms.index("moov") < atoms.index("mdat")

    if video:
        keyframes, packets = 0, 0
        pk = run_capture([ffprobe, "-v", "error", "-select_streams", f"v:{video.get('index', 0)}",
                          "-show_entries", "packet=flags", "-of", "csv=p=0", path])
        if pk.returncode == 0:
            for line in pk.stdout.decode("utf-8", "replace").splitlines():
                packets += 1
                if "K" in line:
                    keyframes += 1
        nb_frames = int(_num(video.get("nb_frames"), packets))
        gop = round(packets / keyframes) if keyframes else 0
        vbit = int(_num(video.get("bit_rate")))
        if not vbit and info["duration"] > 0 and info["size"]:
            vbit = int(info["bitrate"] * 0.95)
        level = video.get("level", 0)
        info["video"] = {
            "index": video.get("index", 0),
            "codec": video.get("codec_name", ""),
            "profile": video.get("profile", ""),
            "level": int(level) if isinstance(level, (int, float)) and level > 0 else 0,
            "width": int(video.get("width", 0)),
            "height": int(video.get("height", 0)),
            "fps": round(_fps(video.get("avg_frame_rate") or video.get("r_frame_rate")), 3),
            "bitrate": vbit,
            "pix_fmt": video.get("pix_fmt", ""),
            "bits": video.get("bits_per_raw_sample", "") or ("10" if "10" in str(video.get("pix_fmt", "")) else "8"),
            "color_range": video.get("color_range", ""),
            "color_space": video.get("color_space", ""),
            "color_transfer": video.get("color_transfer", ""),
            "color_primaries": video.get("color_primaries", ""),
            "field_order": video.get("field_order", ""),
            "bframes": int(_num(video.get("has_b_frames"))),
            "refs": int(_num(video.get("refs"))),
            "gop": gop,
            "frames": nb_frames,
            "tag": video.get("codec_tag_string", ""),
            "rotation": next((int(_num(sd.get("rotation"))) for sd in video.get("side_data_list", []) if "rotation" in sd), 0),
            "hdr": any(k in str(video.get("color_transfer", "")) for k in ("smpte2084", "arib-std-b67")),
            "dovi": any("DOVI" in str(sd.get("side_data_type", "")).upper() or "DOLBY" in str(sd.get("side_data_type", "")).upper()
                        for sd in video.get("side_data_list", [])),
        }
    if audio:
        info["audio"] = {
            "index": audio.get("index", 0),
            "codec": audio.get("codec_name", ""),
            "profile": audio.get("profile", ""),
            "channels": int(_num(audio.get("channels"))),
            "layout": audio.get("channel_layout", ""),
            "rate": int(_num(audio.get("sample_rate"))),
            "bitrate": int(_num(audio.get("bit_rate"))),
            "language": audio.get("tags", {}).get("language", ""),
        }
    return info


# --------------------------------------------------------------------------- affichage

def human_bitrate(bps):
    if not bps:
        return "inconnu"
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.2f} Mbit/s"
    return f"{bps / 1000:.0f} kbit/s"


def human_size(n):
    for unit in ("octets", "Ko", "Mo", "Go"):
        if n < 1024 or unit == "Go":
            return f"{n:.0f} {unit}" if unit == "octets" else f"{n:.1f} {unit}"
        n /= 1024


def human_duration(sec):
    sec = int(round(sec))
    h, rest = divmod(sec, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def level_text(level):
    if not level:
        return "inconnu"
    return f"{level / 10:.1f}" if level < 100 else str(level)


def rows(info):
    """Lignes (section, libellé, valeur) pour l'affichage d'un fichier."""
    out = []
    out.append(("Fichier", "Nom", info["name"]))
    out.append(("Fichier", "Conteneur", info["container"] or "inconnu"))
    out.append(("Fichier", "Taille", human_size(info["size"])))
    out.append(("Fichier", "Durée", human_duration(info["duration"])))
    out.append(("Fichier", "Débit total", human_bitrate(info["bitrate"])))
    if info["faststart"] is not None:
        out.append(("Fichier", "Démarrage rapide (index au début)", "oui" if info["faststart"] else "non"))
    v = info["video"]
    if v:
        out.append(("Vidéo", "Codec", v["codec"].upper()))
        out.append(("Vidéo", "Profil", v["profile"] or "inconnu"))
        out.append(("Vidéo", "Niveau", level_text(v["level"])))
        out.append(("Vidéo", "Résolution", f'{v["width"]} x {v["height"]}'))
        out.append(("Vidéo", "Images par seconde", f'{v["fps"]:g}'))
        out.append(("Vidéo", "Débit vidéo", human_bitrate(v["bitrate"])))
        out.append(("Vidéo", "Format de pixels", v["pix_fmt"] or "inconnu"))
        out.append(("Vidéo", "Profondeur de couleur", f'{v["bits"]} bits'))
        out.append(("Vidéo", "Images B (réordonnancement)", str(v["bframes"])))
        out.append(("Vidéo", "Images de référence", str(v["refs"]) if v["refs"] else "inconnu"))
        out.append(("Vidéo", "Images clés (une toutes les…)", f'{v["gop"]} images' if v["gop"] else "inconnu"))
        out.append(("Vidéo", "Balayage", v["field_order"] or "inconnu"))
        out.append(("Vidéo", "Plage de couleur", v["color_range"] or "inconnu"))
        out.append(("Vidéo", "Espace colorimétrique", v["color_space"] or "inconnu"))
        out.append(("Vidéo", "Courbe de transfert", v["color_transfer"] or "inconnu"))
        out.append(("Vidéo", "HDR", "oui" if v["hdr"] else "non"))
        out.append(("Vidéo", "Dolby Vision", "oui" if v["dovi"] else "non"))
        out.append(("Vidéo", "Étiquette (tag)", v["tag"] or "inconnu"))
    else:
        out.append(("Vidéo", "Vidéo", "aucune piste vidéo"))
    a = info["audio"]
    if a:
        out.append(("Audio", "Codec", a["codec"].upper() + (f' ({a["profile"]})' if a["profile"] else "")))
        out.append(("Audio", "Canaux", f'{a["channels"]} ({a["layout"]})' if a["layout"] else str(a["channels"])))
        out.append(("Audio", "Fréquence", f'{a["rate"]} Hz'))
        out.append(("Audio", "Débit audio", human_bitrate(a["bitrate"])))
        out.append(("Audio", "Langue", a["language"] or "non indiquée"))
    else:
        out.append(("Audio", "Audio", "aucune piste audio"))
    return out


def comparison(a, b):
    """Lignes (section, libellé, valeur A, valeur B, différent ?)."""
    ra, rb = rows(a), rows(b)
    kb = {(s, l): v for s, l, v in rb}
    result = []
    for s, l, va in ra:
        if l in ("Nom", "Taille", "Durée"):
            vb = kb.get((s, l), "")
            result.append((s, l, va, vb, False))
            continue
        vb = kb.get((s, l), "")
        diff = va != vb
        if diff and _close(l, a, b):
            diff = False
        result.append((s, l, va, vb, diff))
    return result


def _close(label, a, b):
    """Écarts négligeables (quelques pour cent de débit, intervalle d'images clés voisin)."""
    def within(x, y, tol):
        return x > 0 and y > 0 and abs(x - y) / max(x, y) <= tol
    va, vb = a.get("video"), b.get("video")
    aa, ab = a.get("audio"), b.get("audio")
    if label == "Débit total":
        return within(a["bitrate"], b["bitrate"], 0.05)
    if label == "Débit vidéo" and va and vb:
        return within(va["bitrate"], vb["bitrate"], 0.05)
    if label == "Images clés (une toutes les…)" and va and vb:
        return within(va["gop"], vb["gop"], 0.25)
    if label == "Débit audio" and aa and ab:
        return within(aa["bitrate"], ab["bitrate"], 0.05)
    return False


def report_text(a, b=None):
    lines = [f"{APP_NAME} {APP_VERSION}", ""]
    if b is None:
        for s, l, v in rows(a):
            lines.append(f"[{s}] {l} : {v}")
    else:
        lines.append(f"Fichier examiné : {a['name']}")
        lines.append(f"Fichier modèle  : {b['name']}")
        lines.append("")
        for s, l, va, vb, diff in comparison(a, b):
            mark = "  <== DIFFÉRENT" if diff else ""
            lines.append(f"[{s}] {l} : {va}  |  {vb}{mark}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- réencodage

H264_PROFILES = {
    "baseline": "baseline", "constrained baseline": "baseline",
    "main": "main", "high": "high", "high 10": "high10",
    "high 4:2:2": "high422", "high 4:4:4 predictive": "high444",
}
H265_PROFILES = {"main": "main", "main 10": "main10", "main still picture": "mainstillpicture"}
AUDIO_ENCODERS = {
    "aac": "aac", "ac3": "ac3", "eac3": "eac3", "mp3": "libmp3lame",
    "opus": "libopus", "vorbis": "libvorbis", "flac": "flac",
}


def output_path(src_path, ref):
    base, _ = os.path.splitext(src_path)
    ext = ref["ext"] or os.path.splitext(src_path)[1] or ".mp4"
    candidate = f"{base}_conforme{ext}"
    n = 2
    while os.path.exists(candidate):
        candidate = f"{base}_conforme{n}{ext}"
        n += 1
    return candidate


def build_command(src, ref, out_path, preset="medium"):
    """Commande ffmpeg qui recrée `src` avec les caractéristiques de `ref`."""
    ffmpeg = find_tool("ffmpeg") or "ffmpeg"
    sv, rv = src["video"], ref["video"]
    if not sv or not rv:
        raise RuntimeError("Les deux fichiers doivent contenir une piste vidéo.")
    cmd = [ffmpeg, "-hide_banner", "-y", "-i", src["path"], "-map", "0:v:0"]
    if src["audio"]:
        cmd += ["-map", "0:a:0"]

    notes = []
    codec = rv["codec"]
    if codec == "h264":
        cmd += ["-c:v", "libx264", "-preset", preset]
        prof = H264_PROFILES.get(rv["profile"].lower())
        if prof:
            cmd += ["-profile:v", prof]
        if rv["level"]:
            cmd += ["-level:v", level_text(rv["level"])]
        cmd += ["-bf", str(rv["bframes"])]
        if rv["refs"]:
            cmd += ["-refs", str(rv["refs"])]
    elif codec == "hevc":
        cmd += ["-c:v", "libx265", "-preset", preset, "-tag:v", "hvc1"]
        prof = H265_PROFILES.get(rv["profile"].lower())
        if prof:
            cmd += ["-profile:v", prof]
        params = []
        if rv["level"]:
            params.append(f'level-idc={rv["level"] / 30:.1f}' if rv["level"] > 100 else f'level-idc={rv["level"] / 10:.1f}')
        params.append(f'bframes={rv["bframes"]}')
        if rv["gop"]:
            params.append(f'keyint={rv["gop"]}')
        params.append("log-level=error")
        cmd += ["-x265-params", ":".join(params)]
    else:
        raise RuntimeError(f"Le codec du modèle ({codec.upper()}) n'est pas géré (H.264 et H.265 seulement).")

    if rv["bitrate"]:
        br = rv["bitrate"]
        cmd += ["-b:v", str(br), "-maxrate", str(int(br * 1.1)), "-bufsize", str(br * 2)]
    if rv["gop"] and codec == "h264":
        cmd += ["-g", str(rv["gop"]), "-keyint_min", str(max(1, rv["gop"] // 2))]
    if rv["pix_fmt"]:
        cmd += ["-pix_fmt", rv["pix_fmt"]]
    filters = []
    if (sv["width"], sv["height"]) != (rv["width"], rv["height"]) and rv["width"] and rv["height"]:
        filters.append(f'scale={rv["width"]}:{rv["height"]}:flags=lanczos')
        notes.append("changement de résolution")
    if filters:
        cmd += ["-vf", ",".join(filters)]
    if rv["fps"] and abs(sv["fps"] - rv["fps"]) > 0.001:
        cmd += ["-r", f'{rv["fps"]:g}']
        notes.append("changement de cadence")
    if rv["color_range"] in ("tv", "pc"):
        cmd += ["-color_range", rv["color_range"]]
    for opt, key in (("-colorspace", "color_space"), ("-color_primaries", "color_primaries"), ("-color_trc", "color_transfer")):
        if rv[key] and rv[key] != "unknown":
            cmd += [opt, rv[key]]

    sa, ra = src["audio"], ref["audio"]
    if sa:
        if ra and ra["codec"] in AUDIO_ENCODERS:
            same = (sa["codec"] == ra["codec"] and sa["channels"] == ra["channels"] and sa["rate"] == ra["rate"]
                    and abs(sa["bitrate"] - ra["bitrate"]) < 16000)
            if same:
                cmd += ["-c:a", "copy"]
            else:
                cmd += ["-c:a", AUDIO_ENCODERS[ra["codec"]]]
                if ra["bitrate"] and ra["codec"] not in ("flac",):
                    cmd += ["-b:a", str(ra["bitrate"])]
                if ra["rate"]:
                    cmd += ["-ar", str(ra["rate"])]
                if ra["channels"]:
                    cmd += ["-ac", str(ra["channels"])]
        else:
            cmd += ["-c:a", "copy"]
    cmd += ["-sn"]
    if ref["ext"] in (".mp4", ".m4v", ".mov") and ref["faststart"] is not False:
        cmd += ["-movflags", "+faststart"]
    cmd += ["-progress", "pipe:1", "-nostats", out_path]
    return cmd, notes


def quote_cmd(cmd):
    out = []
    for part in cmd:
        out.append(f'"{part}"' if re.search(r"\s", part) else part)
    return " ".join(out)


def run_encode(cmd, duration, on_progress, on_done):
    """Lance ffmpeg dans un thread ; on_progress(pourcentage) puis on_done(code, erreur)."""
    def worker():
        err_lines = []
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW,
                universal_newlines=True, encoding="utf-8", errors="replace",
            )
        except OSError as exc:
            on_done(-1, str(exc))
            return

        def drain():
            for line in proc.stderr:
                err_lines.append(line.rstrip())
                del err_lines[:-30]
        t = threading.Thread(target=drain, daemon=True)
        t.start()
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                try:
                    us = int(line.split("=", 1)[1])
                    if duration > 0:
                        on_progress(min(99.0, us / 1_000_000 / duration * 100))
                except ValueError:
                    pass
        proc.wait()
        t.join(timeout=2)
        on_done(proc.returncode, "\n".join(err_lines[-8:]))
    threading.Thread(target=worker, daemon=True).start()


# --------------------------------------------------------------------------- interface

def main_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    FILETYPES = [("Vidéos", "*.mp4 *.m4v *.mkv *.mov *.avi *.wmv *.ts *.webm *.mpg *.mpeg"), ("Tous les fichiers", "*.*")]

    root = tk.Tk()
    root.title(f"{APP_NAME} {APP_VERSION}")
    root.geometry("1040x740")
    root.minsize(900, 600)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure("Treeview", rowheight=24, font=("Segoe UI", 10))
    style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
    style.configure("Big.TButton", font=("Segoe UI", 10, "bold"), padding=8)

    state = {"src": None, "ref": None, "busy": False}

    top = ttk.Frame(root, padding=12)
    top.pack(fill="x")
    ttk.Label(top, text="Examiner un fichier vidéo, le comparer à un fichier qui fonctionne, puis le réencoder à l'identique.",
              font=("Segoe UI", 11, "bold")).pack(anchor="w")
    ttk.Label(top, text="Étape 1 : choisissez le fichier à examiner.   Étape 2 (facultative) : choisissez un fichier qui fonctionne.   "
                        "Étape 3 : réencodez le premier comme le second.",
              foreground="#555").pack(anchor="w", pady=(2, 8))

    buttons = ttk.Frame(top)
    buttons.pack(fill="x")
    lbl_src = ttk.Label(top, text="Fichier à examiner : (aucun)")
    lbl_ref = ttk.Label(top, text="Fichier modèle : (aucun)")

    table_frame = ttk.Frame(root, padding=(12, 0))
    table_frame.pack(fill="both", expand=True)
    cols = ("label", "src", "ref")
    tree = ttk.Treeview(table_frame, columns=cols, show="headings", selectmode="browse")
    tree.heading("label", text="Caractéristique")
    tree.heading("src", text="Fichier examiné")
    tree.heading("ref", text="Fichier modèle")
    tree.column("label", width=270, anchor="w")
    tree.column("src", width=330, anchor="w")
    tree.column("ref", width=330, anchor="w")
    tree.tag_configure("section", background="#2b3a4a", foreground="white", font=("Segoe UI", 10, "bold"))
    tree.tag_configure("diff", background="#ffe3a3")
    scroll = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    tree.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")

    bottom = ttk.Frame(root, padding=12)
    bottom.pack(fill="x")
    progress = ttk.Progressbar(bottom, maximum=100)
    progress.pack(fill="x")
    status = tk.StringVar(value="Prêt.")
    ttk.Label(bottom, textvariable=status).pack(anchor="w", pady=(4, 2))
    log = tk.Text(bottom, height=5, wrap="word", font=("Consolas", 9), background="#f4f4f4")
    log.pack(fill="x")

    def set_log(text):
        log.configure(state="normal")
        log.delete("1.0", "end")
        log.insert("end", text)
        log.configure(state="disabled")

    def refresh():
        tree.heading("src", text=f"Fichier examiné : {state['src']['name']}" if state["src"] else "Fichier examiné")
        tree.heading("ref", text=f"Fichier modèle : {state['ref']['name']}" if state["ref"] else "Fichier modèle")
        for item in tree.get_children():
            tree.delete(item)
        src, ref = state["src"], state["ref"]
        if not src:
            return
        last = None
        data = comparison(src, ref) if ref else [(s, l, v, "", False) for s, l, v in rows(src)]
        n_diff = 0
        for s, l, va, vb, diff in data:
            if s != last:
                tree.insert("", "end", values=(s.upper(), "", ""), tags=("section",))
                last = s
            tree.insert("", "end", values=(l, va, vb), tags=("diff",) if diff else ())
            n_diff += 1 if diff else 0
        if ref:
            status.set(f"{n_diff} différence(s) repérée(s) (lignes en jaune)." if n_diff else "Aucune différence technique repérée.")
        btn_ref.configure(state="normal")
        btn_copy.configure(state="normal")
        btn_enc.configure(state="normal" if ref else "disabled")

    def load(path):
        try:
            return probe(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_NAME, f"Impossible de lire ce fichier :\n{exc}")
            return None

    def pick_src():
        path = filedialog.askopenfilename(title="Fichier à examiner", filetypes=FILETYPES)
        if not path:
            return
        info = load(path)
        if info:
            state["src"] = info
            state["ref"] = None
            lbl_src.configure(text=f"Fichier à examiner : {path}")
            lbl_ref.configure(text="Fichier modèle : (aucun)")
            progress["value"] = 0
            set_log("")
            refresh()
            status.set("Caractéristiques affichées. Vous pouvez maintenant choisir un fichier modèle qui fonctionne.")

    def pick_ref():
        path = filedialog.askopenfilename(title="Fichier modèle (qui fonctionne)", filetypes=FILETYPES)
        if not path:
            return
        info = load(path)
        if info:
            state["ref"] = info
            lbl_ref.configure(text=f"Fichier modèle : {path}")
            refresh()

    def copy_report():
        if not state["src"]:
            return
        root.clipboard_clear()
        root.clipboard_append(report_text(state["src"], state["ref"]))
        status.set("Rapport copié dans le presse-papiers.")

    def encode():
        if state["busy"] or not state["src"] or not state["ref"]:
            return
        src, ref = state["src"], state["ref"]
        try:
            out = output_path(src["path"], ref)
            cmd, notes = build_command(src, ref, out)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_NAME, str(exc))
            return
        if not messagebox.askyesno(
            APP_NAME,
            f"Créer une copie de « {src['name']} » avec les caractéristiques de « {ref['name']} » ?\n\n"
            f"Fichier créé : {os.path.basename(out)}\nL'original n'est pas modifié."
            + (f"\n\nAttention : {', '.join(notes)}." if notes else ""),
        ):
            return
        state["busy"] = True
        for b in (btn_src, btn_ref, btn_enc):
            b.configure(state="disabled")
        progress["value"] = 0
        status.set("Réencodage en cours…")
        set_log("Commande utilisée :\n" + quote_cmd(cmd))

        def on_progress(pct):
            root.after(0, lambda: (progress.configure(value=pct), status.set(f"Réencodage en cours… {pct:.0f} %")))

        def on_done(code, err):
            def finish():
                state["busy"] = False
                for b in (btn_src, btn_ref):
                    b.configure(state="normal")
                btn_enc.configure(state="normal")
                if code == 0 and os.path.exists(out):
                    progress["value"] = 100
                    status.set(f"Terminé : {out}")
                    try:
                        new = probe(out)
                        ok = messagebox.askyesno(APP_NAME, f"Réencodage terminé :\n{out}\n\nAfficher ses caractéristiques (à la place du fichier examiné) ?")
                        if ok:
                            state["src"] = new
                            lbl_src.configure(text=f"Fichier à examiner : {out}")
                            refresh()
                    except Exception:  # noqa: BLE001
                        pass
                else:
                    status.set("Échec du réencodage.")
                    messagebox.showerror(APP_NAME, "Le réencodage a échoué.\n\n" + (err or "Erreur inconnue."))
            root.after(0, finish)

        run_encode(cmd, src["duration"], on_progress, on_done)

    btn_src = ttk.Button(buttons, text="1. Choisir le fichier à examiner", style="Big.TButton", command=pick_src)
    btn_ref = ttk.Button(buttons, text="2. Comparer à un fichier qui fonctionne", style="Big.TButton", command=pick_ref, state="disabled")
    btn_enc = ttk.Button(buttons, text="3. Réencoder comme le modèle", style="Big.TButton", command=encode, state="disabled")
    btn_copy = ttk.Button(buttons, text="Copier le rapport", command=copy_report, state="disabled")
    btn_src.pack(side="left", padx=(0, 8))
    btn_ref.pack(side="left", padx=(0, 8))
    btn_enc.pack(side="left", padx=(0, 8))
    btn_copy.pack(side="right")
    lbl_src.pack(anchor="w", pady=(8, 0))
    lbl_ref.pack(anchor="w")

    missing = [t for t in ("ffprobe", "ffmpeg") if not find_tool(t)]
    if missing:
        messagebox.showwarning(APP_NAME, "Outil introuvable : " + ", ".join(missing) + ".\nInstallez FFmpeg ou placez-le à côté du programme.")
    root.mainloop()


# --------------------------------------------------------------------------- ligne de commande (essais)

def main_cli(argv):
    if len(argv) >= 2 and argv[0] == "--analyse":
        a = probe(argv[1])
        b = probe(argv[2]) if len(argv) >= 3 else None
        print(report_text(a, b))
        return 0
    if len(argv) >= 3 and argv[0] == "--commande":
        a, b = probe(argv[1]), probe(argv[2])
        cmd, notes = build_command(a, b, output_path(argv[1], b))
        print(quote_cmd(cmd))
        if notes:
            print("Notes :", ", ".join(notes))
        return 0
    return None


if __name__ == "__main__":
    code = main_cli(sys.argv[1:]) if len(sys.argv) > 1 else None
    if code is None:
        main_gui()
    else:
        sys.exit(code)
