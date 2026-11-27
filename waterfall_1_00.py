import tkinter as tk
from tkinter import ttk, messagebox
import numpy as np
import sounddevice as sd
import threading
import queue
import time


WF_WIDTH = 501
SPEC_HEIGHT = 165
WF_HEIGHT = 82
AXIS_HEIGHT = 24

# IC-7300 / IC-7300mk2 CI-V Center-mode spans: label -> (half_span_hz, bcd_5bytes)
CIV_SPANS = [
    ("±2.5 kHz (5 kHz)",   2500,   b'\x00\x25\x00\x00\x00'),
    ("±5 kHz (10 kHz)",    5000,   b'\x00\x50\x00\x00\x00'),
    ("±10 kHz (20 kHz)",   10000,  b'\x00\x00\x01\x00\x00'),
    ("±25 kHz (50 kHz)",   25000,  b'\x00\x50\x02\x00\x00'),
    ("±50 kHz (100 kHz)",  50000,  b'\x00\x00\x05\x00\x00'),
    ("±100 kHz (200 kHz)", 100000, b'\x00\x00\x10\x00\x00'),
    ("±250 kHz (500 kHz)", 250000, b'\x00\x00\x25\x00\x00'),
    ("±500 kHz (1 MHz)",   500000, b'\x00\x00\x50\x00\x00'),
]
CIV_SPAN_MAP = {lbl: (hz, bcd) for lbl, hz, bcd in CIV_SPANS}

AUDIO_SPANS = [
    ("Audio 0 - 3.0 kHz", 3000),
    ("Audio 0 - 3.5 kHz", 3500),
    ("Audio 0 - 4.0 kHz", 4000),
]
AUDIO_SPAN_MAP = dict(AUDIO_SPANS)


def bcd_byte_to_int(b):
    hi = (b >> 4) & 0x0F
    lo = b & 0x0F
    if hi <= 9 and lo <= 9:
        return hi * 10 + lo
    return b


def decode_bcd_freq(data5):
    """Zet 5-byte Icom little-endian BCD frequentie om naar Hz."""
    if len(data5) != 5:
        return None
    try:
        return int(data5[::-1].hex())
    except ValueError:
        return None


def build_colormap(name):
    """Bouw een 256x3 uint8 kleurkaart (LUT) voor de waterfall."""
    x = np.linspace(0.0, 1.0, 256)

    if name == "Vuur":
        anchors = np.array([0.0, 0.25, 0.55, 0.80, 1.0])
        r = np.interp(x, anchors, [8, 140, 235, 255, 255])
        g = np.interp(x, anchors, [8, 12, 95, 235, 255])
        b = np.interp(x, anchors, [16, 20, 10, 40, 255])
    elif name == "Groen":
        anchors = np.array([0.0, 0.45, 0.80, 1.0])
        r = np.interp(x, anchors, [5, 10, 140, 245])
        g = np.interp(x, anchors, [16, 160, 255, 255])
        b = np.interp(x, anchors, [10, 30, 80, 220])
    elif name == "Grijswaarden":
        r = g = b = x * 255.0
    else:
        anchors = np.array([0.0, 0.22, 0.48, 0.72, 0.90, 1.0])
        r = np.interp(x, anchors, [6, 10, 20, 245, 255, 255])
        g = np.interp(x, anchors, [12, 45, 195, 225, 55, 255])
        b = np.interp(x, anchors, [38, 155, 225, 30, 20, 255])

    return np.stack([r, g, b], axis=1).clip(0, 255).astype(np.uint8)


class WaterfallPanel:
    """Ingebouwd Spectrum & Waterfall paneel voor het PA7RW Assistent hoofdscherm."""

    def __init__(self, parent, app):
        self.parent = parent
        self.app = app

        self.is_running = False
        self.is_paused = False
        self.destroyed = False

        # Audio FFT instellingen
        self.sample_rate = 16000
        self.n_fft = 4096
        self.hop_size = 1024
        self.audio_max_hz = 3500

        # RF Bandscope (CI-V) status
        self.civ_half_span_hz = 10000  # ±10 kHz = 20 kHz totaal (bijv. 10.100 - 10.120)
        self.civ_scope_mode = 0        # 0 = Center, 1 = Fixed
        self.civ_low_hz = None
        self.civ_high_hz = None
        self.civ_wave_chunks = {}
        self.civ_frame_meta = None
        self.last_civ_frame_ts = 0.0

        # Weergave & dB-schaal
        self.floor_db = -92.0
        self.range_db = 60.0
        self.hover_x = None
        self.last_spectrum_db = None
        self.last_axis_key = None

        self.frame_queue = queue.Queue(maxsize=40)
        self.wf_rgb = np.full((WF_HEIGHT, WF_WIDTH, 3), fill_value=10, dtype=np.uint8)
        self.ppm_header = f"P6\n{WF_WIDTH} {WF_HEIGHT}\n255\n".encode("ascii")
        self.lut = build_colormap("Klassiek")

        self.source_var = tk.StringVar(value="Radio RF Scope (CI-V)")
        self.span_var = tk.StringVar(value="±10 kHz (20 kHz)")
        self.cmap_var = tk.StringVar(value="Klassiek")
        self.floor_var = tk.DoubleVar(value=self.floor_db)
        self.range_var = tk.DoubleVar(value=self.range_db)
        self.qsy_on_click_var = tk.BooleanVar(value=True)

        self.create_widgets()
        self.refresh_audio_devices()
        self.update_source_controls()
        self.draw_freq_axis()
        self.draw_spectrum(None)
        self.refresh_waterfall_image()
        self.parent.after(35, self.ui_update_loop)

    def create_widgets(self):
        outer = ttk.LabelFrame(self.parent, text=" Spectrum & Waterfall (Klik om af te stemmen) ", padding=6)
        outer.pack(fill="x", anchor="n")

        # --- Rij 1: Bron, Span, Start/Stop, Pauze ---
        row1 = ttk.Frame(outer)
        row1.pack(fill="x", pady=(0, 3))

        ttk.Label(row1, text="Bron:").pack(side="left")
        self.source_combo = ttk.Combobox(
            row1,
            values=["Radio RF Scope (CI-V)", "Audio RX (USB)"],
            textvariable=self.source_var,
            width=19,
            state="readonly"
        )
        self.source_combo.pack(side="left", padx=(3, 6))
        self.source_combo.bind("<<ComboboxSelected>>", self.on_source_change)

        ttk.Label(row1, text="Span:").pack(side="left")
        self.span_combo = ttk.Combobox(
            row1,
            values=[lbl for lbl, _, _ in CIV_SPANS],
            textvariable=self.span_var,
            width=16,
            state="readonly"
        )
        self.span_combo.pack(side="left", padx=(3, 6))
        self.span_combo.bind("<<ComboboxSelected>>", self.on_span_change)

        self.btn_start = tk.Button(
            row1, text="Scope Activeren", bg="#28a745", fg="white",
            font=("Arial", 8, "bold"), width=13, command=self.toggle_waterfall
        )
        self.btn_start.pack(side="left", padx=2)

        self.btn_pause = tk.Button(
            row1, text="Pauze", bg="SystemButtonFace", fg="black",
            font=("Arial", 8), width=6, command=self.toggle_pause
        )
        self.btn_pause.pack(side="left", padx=2)

        # --- Rij 2: Kleur, Vloer, Bereik, Auto, Wis ---
        row2 = ttk.Frame(outer)
        row2.pack(fill="x", pady=2)

        ttk.Label(row2, text="Kleur:").pack(side="left")
        self.cmap_combo = ttk.Combobox(
            row2,
            values=["Klassiek", "Vuur", "Groen", "Grijswaarden"],
            textvariable=self.cmap_var,
            width=10,
            state="readonly"
        )
        self.cmap_combo.pack(side="left", padx=(3, 6))
        self.cmap_combo.bind("<<ComboboxSelected>>", self.on_cmap_change)

        ttk.Label(row2, text="Vloer:").pack(side="left")
        self.floor_scale = ttk.Scale(
            row2, from_=-115, to=-25, orient="horizontal",
            variable=self.floor_var, command=self.on_levels_change, length=75
        )
        self.floor_scale.pack(side="left", padx=2)
        self.lbl_floor = ttk.Label(row2, text="-92 dB", width=6)
        self.lbl_floor.pack(side="left")

        ttk.Label(row2, text="Bereik:").pack(side="left", padx=(4, 0))
        self.range_scale = ttk.Scale(
            row2, from_=20, to=90, orient="horizontal",
            variable=self.range_var, command=self.on_levels_change, length=75
        )
        self.range_scale.pack(side="left", padx=2)
        self.lbl_range = ttk.Label(row2, text="60 dB", width=5)
        self.lbl_range.pack(side="left")

        ttk.Button(row2, text="Auto", width=5, command=self.auto_calibrate).pack(side="right", padx=1)
        ttk.Button(row2, text="Wis", width=4, command=self.clear_waterfall).pack(side="right", padx=1)

        # --- Rij 3: Audio-ingang (voor Audio RX modus) & Klik-QSY ---
        row3 = ttk.Frame(outer)
        row3.pack(fill="x", pady=(1, 4))

        ttk.Label(row3, text="USB Audio:").pack(side="left")
        self.audio_combo = ttk.Combobox(row3, width=22, state="readonly")
        self.audio_combo.pack(side="left", padx=(3, 8))

        ttk.Checkbutton(
            row3, text="Klik = Radio afstemmen (QSY)",
            variable=self.qsy_on_click_var
        ).pack(side="left")

        # --- Spectrum, Frequentie-as en Waterfall Canvassen ---
        disp_frame = ttk.Frame(outer)
        disp_frame.pack()

        self.spec_canvas = tk.Canvas(
            disp_frame, width=WF_WIDTH, height=SPEC_HEIGHT,
            bg="#0b0f14", highlightthickness=1, highlightbackground="#30363d", cursor="crosshair"
        )
        self.spec_canvas.pack()

        self.axis_canvas = tk.Canvas(
            disp_frame, width=WF_WIDTH, height=AXIS_HEIGHT,
            bg="#161b22", highlightthickness=0
        )
        self.axis_canvas.pack()

        self.wf_canvas = tk.Canvas(
            disp_frame, width=WF_WIDTH, height=WF_HEIGHT,
            bg="#0b0f14", highlightthickness=1, highlightbackground="#30363d", cursor="crosshair"
        )
        self.wf_canvas.pack()

        self.wf_photo = tk.PhotoImage(data=self.ppm_header + self.wf_rgb.tobytes(), format="PPM")
        self.wf_img_id = self.wf_canvas.create_image(0, 0, anchor="nw", image=self.wf_photo)
        self.wf_needle_id = None
        self.wf_hover_id = None

        for cvs in (self.wf_canvas, self.spec_canvas):
            cvs.bind("<Motion>", self.on_mouse_move)
            cvs.bind("<Leave>", self.on_mouse_leave)
            cvs.bind("<Button-1>", self.on_canvas_click)

        # --- Statusbalk onderaan ---
        status_row = ttk.Frame(outer)
        status_row.pack(fill="x", pady=(6, 0))

        self.lbl_cursor = ttk.Label(
            status_row,
            text="Beweeg over het scherm voor exacte RF-frequentie",
            font=("Consolas", 10, "bold")
        )
        self.lbl_cursor.pack(side="left")

        self.lbl_scope_status = ttk.Label(
            status_row,
            text="VFO: -- | Span: ±10 kHz",
            font=("Arial", 9, "italic")
        )
        self.lbl_scope_status.pack(side="right")

    # --- Audio-apparaten ---
    def refresh_audio_devices(self):
        try:
            devices = sd.query_devices()
        except Exception:
            devices = []
        input_devs = []
        default_idx = 0
        for idx, dev in enumerate(devices):
            if dev.get("hostapi") != 0:
                continue
            naam = dev["name"]
            naam_laag = naam.lower()
            if any(ign in naam_laag for ign in ["geluidstoewijzing", "primair", "primary", "display audio", "hd audio driver"]):
                continue
            if dev["max_input_channels"] > 0:
                name = f"{idx}: {naam}"
                input_devs.append(name)
                if "USB AUDIO DEVICE" in naam.upper():
                    default_idx = len(input_devs) - 1
                elif "USB" in naam.upper() and default_idx == 0:
                    default_idx = len(input_devs) - 1
        self.audio_combo["values"] = input_devs
        if input_devs:
            self.audio_combo.current(default_idx)

    # --- Frequentiebereik & Naald-berekening ---
    def get_radio_mode(self):
        txt = self.app.lbl_current_mode.cget("text")
        if ":" in txt:
            return txt.split(":", 1)[1].strip()
        return "ONBEKEND"

    def get_freq_bounds_hz(self):
        """Geeft (low_hz, high_hz, vfo_hz) terug voor de zichtbare schaal."""
        vfo_hz = int(self.app.current_freq_hz)
        if self.source_var.get() == "Radio RF Scope (CI-V)":
            if (
                self.civ_scope_mode in (1, 3)
                and self.civ_low_hz is not None
                and self.civ_high_hz is not None
                and self.civ_high_hz > self.civ_low_hz
            ):
                return self.civ_low_hz, self.civ_high_hz, vfo_hz
            return vfo_hz - self.civ_half_span_hz, vfo_hz + self.civ_half_span_hz, vfo_hz

        # Audio RX (USB) modus: toon RF-schaal langs het doorlaatbandbereik
        mode = self.get_radio_mode()
        if any(m in mode for m in ("LSB", "CW-R")) or mode == "RTTY":
            return vfo_hz - self.audio_max_hz, vfo_hz, vfo_hz
        return vfo_hz, vfo_hz + self.audio_max_hz, vfo_hz

    def freq_to_x(self, freq_hz):
        low_hz, high_hz, _ = self.get_freq_bounds_hz()
        span = max(1, high_hz - low_hz)
        return int(round(((freq_hz - low_hz) / float(span)) * (WF_WIDTH - 1)))

    def x_to_freq(self, x):
        low_hz, high_hz, _ = self.get_freq_bounds_hz()
        clamped = max(0, min(WF_WIDTH - 1, x))
        frac = clamped / float(WF_WIDTH - 1)
        return int(round(low_hz + frac * (high_hz - low_hz)))

    def get_needle_x(self):
        """Positie van de VFO-naald (in Center mode exact in het midden)."""
        low_hz, high_hz, vfo_hz = self.get_freq_bounds_hz()
        if self.source_var.get() == "Radio RF Scope (CI-V)" and self.civ_scope_mode in (0, 2):
            return WF_WIDTH // 2
        if self.source_var.get() == "Audio RX (USB)":
            mode = self.get_radio_mode()
            tone = 600 if "CW" in mode else (2125 if "RTTY" in mode else 0)
            target_rf = (vfo_hz - tone) if (any(m in mode for m in ("LSB", "CW-R")) or mode == "RTTY") else (vfo_hz + tone)
            return max(0, min(WF_WIDTH - 1, self.freq_to_x(target_rf)))
        if vfo_hz < low_hz or vfo_hz > high_hz:
            return None
        return self.freq_to_x(vfo_hz)

    # --- Bron & Span ---
    def update_source_controls(self):
        is_civ = self.source_var.get() == "Radio RF Scope (CI-V)"
        if is_civ:
            self.span_combo["values"] = [lbl for lbl, _, _ in CIV_SPANS]
            if self.span_var.get() not in CIV_SPAN_MAP:
                self.span_var.set("±10 kHz (20 kHz)")
            self.audio_combo.config(state="disabled")
            self.btn_start.config(
                text="Scope Stoppen" if self.is_running else "Scope Activeren",
                bg="#dc3545" if self.is_running else "#28a745"
            )
        else:
            self.span_combo["values"] = [lbl for lbl, _ in AUDIO_SPANS]
            if self.span_var.get() not in AUDIO_SPAN_MAP:
                self.span_var.set("Audio 0 - 3.5 kHz")
            self.audio_combo.config(state="readonly")
            self.btn_start.config(
                text="Audio Stoppen" if self.is_running else "Audio Starten",
                bg="#dc3545" if self.is_running else "#28a745"
            )

    def on_source_change(self, _=None):
        was_running = self.is_running
        if was_running:
            self.stop_waterfall()
        self.clear_waterfall()
        self.update_source_controls()
        self.draw_freq_axis()
        self.draw_spectrum(None)
        if was_running:
            self.start_waterfall()

    def on_span_change(self, _=None):
        val = self.span_var.get()
        if self.source_var.get() == "Radio RF Scope (CI-V)":
            if val in CIV_SPAN_MAP:
                half_hz, bcd = CIV_SPAN_MAP[val]
                self.civ_half_span_hz = half_hz
                if self.app.is_connected and self.is_running:
                    threading.Thread(target=self.apply_civ_span, args=(bcd,), daemon=True).start()
        else:
            self.audio_max_hz = AUDIO_SPAN_MAP.get(val, 3500)
        self.draw_freq_axis()
        self.draw_spectrum(self.last_spectrum_db)

    def on_cmap_change(self, _=None):
        self.lut = build_colormap(self.cmap_var.get())

    def on_levels_change(self, _=None):
        self.floor_db = float(self.floor_var.get())
        self.range_db = float(self.range_var.get())
        self.lbl_floor.config(text=f"{int(round(self.floor_db))} dB")
        self.lbl_range.config(text=f"{int(round(self.range_db))} dB")
        self.draw_spectrum(self.last_spectrum_db)

    def auto_calibrate(self):
        if self.last_spectrum_db is None:
            return
        med = float(np.median(self.last_spectrum_db))
        peak = float(np.percentile(self.last_spectrum_db, 99.5))
        new_floor = max(-115.0, min(-25.0, round(med - 5.0)))
        new_range = max(25.0, min(85.0, round(max(45.0, peak - new_floor + 6.0))))
        self.floor_var.set(new_floor)
        self.range_var.set(new_range)
        self.on_levels_change()

    def clear_waterfall(self):
        self.wf_rgb[:] = self.lut[0]
        self.refresh_waterfall_image()

    def toggle_pause(self):
        self.is_paused = not self.is_paused
        if self.is_paused:
            self.btn_pause.config(text="Hervat", bg="#ffc107", fg="black")
        else:
            self.btn_pause.config(text="Pauze", bg="SystemButtonFace", fg="black")

    # --- Muisinteractie ---
    def on_mouse_move(self, event):
        self.hover_x = max(0, min(WF_WIDTH - 1, event.x))
        rf_hz = self.x_to_freq(self.hover_x)
        self.lbl_cursor.config(text=f"Cursor RF: {rf_hz / 1_000_000:.6f} MHz  ({rf_hz:,} Hz)".replace(",", "."))
        self.update_waterfall_overlays()

    def on_mouse_leave(self, _=None):
        self.hover_x = None
        self.lbl_cursor.config(text="Beweeg over het scherm voor exacte RF-frequentie")
        self.update_waterfall_overlays()

    def on_canvas_click(self, event):
        if not self.qsy_on_click_var.get():
            return
        if not self.app.is_connected:
            messagebox.showwarning("Niet verbonden", "Maak eerst verbinding met de IC-7300mk2.")
            return

        rf_hz = self.x_to_freq(event.x)
        if self.source_var.get() == "Audio RX (USB)":
            mode = self.get_radio_mode()
            tone = 600 if "CW" in mode else (2125 if "RTTY" in mode else 0)
            if any(m in mode for m in ("LSB", "CW-R")) or mode == "RTTY":
                rf_hz = rf_hz + tone
            else:
                rf_hz = rf_hz - tone

        new_freq = max(30000, min(74800000, int(round(rf_hz))))
        self.app.current_freq_hz = new_freq
        self.app.lbl_current_freq.config(text=f"{new_freq / 1_000_000:.6f} MHz")
        self.app.freq_entry.delete(0, tk.END)
        self.app.freq_entry.insert(0, str(new_freq))
        self.app.set_frequency()
        self.draw_freq_axis()

    # --- Tekenen: Frequentie-as, Spectrum, Waterfall ---
    def choose_tick_steps(self, span_hz):
        if span_hz <= 4500:
            return 500, 100
        if span_hz <= 6000:
            return 1000, 250
        if span_hz <= 12000:
            return 2000, 500
        if span_hz <= 25000:
            return 5000, 1000
        if span_hz <= 60000:
            return 10000, 2000
        if span_hz <= 120000:
            return 20000, 5000
        if span_hz <= 250000:
            return 50000, 10000
        if span_hz <= 600000:
            return 100000, 25000
        return 200000, 50000

    def draw_freq_axis(self):
        self.axis_canvas.delete("all")
        self.axis_canvas.create_line(0, 0, WF_WIDTH, 0, fill="#30363d")

        low_hz, high_hz, vfo_hz = self.get_freq_bounds_hz()
        span_hz = max(1, high_hz - low_hz)
        major_step, minor_step = self.choose_tick_steps(span_hz)

        first_minor = ((low_hz + minor_step - 1) // minor_step) * minor_step
        for f in range(first_minor, high_hz + 1, minor_step):
            x = self.freq_to_x(f)
            if f % major_step == 0:
                self.axis_canvas.create_line(x, 0, x, 9, fill="#8b949e", width=1)
                if x < 28:
                    anchor = "w"
                elif x > WF_WIDTH - 28:
                    anchor = "e"
                else:
                    anchor = "center"
                if major_step < 1000:
                    lbl = f"{f / 1_000_000:.4f}"
                else:
                    lbl = f"{f / 1_000_000:.3f}"
                self.axis_canvas.create_text(
                    x, 18, text=lbl, fill="#d0d7de", font=("Consolas", 8, "bold"), anchor=anchor
                )
            else:
                self.axis_canvas.create_line(x, 0, x, 4, fill="#484f58")

        # VFO-"naald" driehoekje bovenaan de frequentie-as
        nx = self.get_needle_x()
        if nx is not None:
            self.axis_canvas.create_polygon(
                nx - 6, 0, nx + 6, 0, nx, 9,
                fill="#ff4d4d", outline="#ffffff", width=1
            )

    def draw_spectrum(self, spec_db):
        self.spec_canvas.delete("all")

        low_hz, high_hz, _ = self.get_freq_bounds_hz()
        span_hz = max(1, high_hz - low_hz)
        major_step, _ = self.choose_tick_steps(span_hz)

        first_major = ((low_hz + major_step - 1) // major_step) * major_step
        for f in range(first_major, high_hz + 1, major_step):
            x = self.freq_to_x(f)
            self.spec_canvas.create_line(x, 0, x, SPEC_HEIGHT, fill="#1b232c", dash=(2, 4))

        for frac in (1 / 3, 2 / 3):
            y = int(round(SPEC_HEIGHT * frac))
            self.spec_canvas.create_line(0, y, WF_WIDTH, y, fill="#1b232c", dash=(2, 4))

        # Spectrum-lijn en subtiele vulling
        if spec_db is not None and len(spec_db) == WF_WIDTH:
            norm = np.clip((spec_db - self.floor_db) / max(1.0, self.range_db), 0.0, 1.0)
            ys = (SPEC_HEIGHT - 4) - norm * (SPEC_HEIGHT - 12)
            coords = []
            for x in range(0, WF_WIDTH, 2):
                coords.append(x)
                coords.append(float(ys[x]))
            poly_coords = [0, SPEC_HEIGHT - 1] + coords + [WF_WIDTH - 1, SPEC_HEIGHT - 1]
            self.spec_canvas.create_polygon(poly_coords, fill="#0e2a3d", outline="")
            self.spec_canvas.create_line(coords, fill="#38bdf8", width=1.5)
        elif self.source_var.get() == "Radio RF Scope (CI-V)":
            if not self.app.is_connected:
                msg = "Verbind met de IC-7300mk2 om de live RF-waterfall te starten"
            elif not self.is_running:
                msg = "Klik op 'Scope Activeren' om de CI-V Bandscope te starten"
            else:
                msg = "Wacht op CI-V Scope data (27 00)... Controleer radio: Baudrate 115200 & CI-V USB Port Unlinked"
            self.spec_canvas.create_text(
                WF_WIDTH // 2, SPEC_HEIGHT // 2,
                text=msg, fill="#8b949e", font=("Arial", 9, "italic")
            )

        # Rode VFO-naald in het midden (of op VFO-positie)
        nx = self.get_needle_x()
        if nx is not None:
            self.spec_canvas.create_line(nx, 0, nx, SPEC_HEIGHT, fill="#ff4d4d", width=1.5)

        # Hoverlijn
        if self.hover_x is not None:
            self.spec_canvas.create_line(self.hover_x, 0, self.hover_x, SPEC_HEIGHT, fill="#ffffff", width=1)

    def update_waterfall_overlays(self):
        if self.wf_needle_id is not None:
            self.wf_canvas.delete(self.wf_needle_id)
            self.wf_needle_id = None
        if self.wf_hover_id is not None:
            self.wf_canvas.delete(self.wf_hover_id)
            self.wf_hover_id = None

        nx = self.get_needle_x()
        if nx is not None:
            self.wf_needle_id = self.wf_canvas.create_line(
                nx, 0, nx, WF_HEIGHT, fill="#ff4d4d", width=1, dash=(4, 4)
            )
        if self.hover_x is not None:
            self.wf_hover_id = self.wf_canvas.create_line(
                self.hover_x, 0, self.hover_x, WF_HEIGHT, fill="#ffffff", width=1
            )

    def refresh_waterfall_image(self):
        self.wf_photo.configure(data=self.ppm_header + self.wf_rgb.tobytes())
        self.update_waterfall_overlays()

    # --- Start/Stop & Radio CI-V Scope Control ---
    def toggle_waterfall(self):
        if not self.is_running:
            self.start_waterfall()
        else:
            self.stop_waterfall()

    def start_waterfall(self):
        if self.is_running:
            return
        if self.source_var.get() == "Radio RF Scope (CI-V)":
            if not self.app.is_connected:
                messagebox.showwarning("Niet verbonden", "Maak eerst verbinding met de IC-7300mk2.")
                return
            self.is_running = True
            self.update_source_controls()
            threading.Thread(target=self.enable_civ_scope, daemon=True).start()
        else:
            if not self.audio_combo.get():
                messagebox.showerror("Audiofout", "Geen USB geluidsingang gevonden.")
                return
            dev_id = int(self.audio_combo.get().split(":", 1)[0])
            self.is_running = True
            self.update_source_controls()
            threading.Thread(target=self.audio_worker, args=(dev_id,), daemon=True).start()

    def stop_waterfall(self):
        was_civ = self.source_var.get() == "Radio RF Scope (CI-V)"
        self.is_running = False
        self.update_source_controls()
        if was_civ and self.app.is_connected:
            threading.Thread(target=self.disable_civ_scope, daemon=True).start()

    def on_radio_connected(self):
        """Wordt aangeroepen nadat verbinding met de radio is gemaakt."""
        if self.source_var.get() == "Radio RF Scope (CI-V)":
            self.is_running = True
            self.update_source_controls()
            threading.Thread(target=self.enable_civ_scope, daemon=True).start()

    def on_radio_disconnected(self):
        if self.source_var.get() == "Radio RF Scope (CI-V)":
            self.is_running = False
            self.update_source_controls()

    def enable_civ_scope(self):
        if not self.app.is_connected:
            return
        self.app.send_cmd(b'\x27\x10', b'\x01')
        time.sleep(0.04)
        self.app.send_cmd(b'\x27\x11', b'\x01')
        time.sleep(0.04)
        res = self.app.send_cmd(b'\x27\x14', b'\x00\x00')
        if res and b'\xFA\xFD' in res:
            self.app.send_cmd(b'\x27\x14', b'\x00')
        time.sleep(0.04)
        val = self.span_var.get()
        if val in CIV_SPAN_MAP:
            self.apply_civ_span(CIV_SPAN_MAP[val][1])

    def disable_civ_scope(self):
        if self.app.is_connected:
            self.app.send_cmd(b'\x27\x11', b'\x00')

    def apply_civ_span(self, span_bcd):
        if not self.app.is_connected:
            return
        res = self.app.send_cmd(b'\x27\x15', b'\x00' + span_bcd)
        if res and b'\xFA\xFD' in res:
            self.app.send_cmd(b'\x27\x15', span_bcd)

    # --- CI-V 0x27 0x00 Parser ---
    def push_line_db(self, line_db):
        try:
            self.frame_queue.put_nowait(line_db)
        except queue.Full:
            try:
                self.frame_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.frame_queue.put_nowait(line_db)
            except queue.Full:
                pass

    def handle_civ_scope_frame(self, frame):
        """Verwerk een binnenkomend CI-V 27 00 pakket vanuit de seriële lezer."""
        if not self.is_running or self.is_paused or self.source_var.get() != "Radio RF Scope (CI-V)":
            return
        if len(frame) < 10:
            return
        payload = frame[6:-1]
        if len(payload) < 3:
            return

        seq = bcd_byte_to_int(payload[1])
        max_div = bcd_byte_to_int(payload[2])
        if max_div <= 0:
            return

        if seq == 1:
            self.civ_wave_chunks = {}
            if len(payload) < 15:
                return
            scope_mode = payload[3]
            f1 = decode_bcd_freq(payload[4:9])
            f2 = decode_bcd_freq(payload[9:14])
            oor = payload[14]
            self.civ_frame_meta = (scope_mode, f1, f2, oor, max_div)

            if max_div == 1 and oor == 0 and len(payload) > 15:
                self.emit_civ_waveform(np.frombuffer(payload[15:], dtype=np.uint8), scope_mode, f1, f2)
        else:
            if self.civ_frame_meta is None:
                return
            self.civ_wave_chunks[seq] = payload[3:]
            if seq == max_div:
                scope_mode, f1, f2, oor, expected_max = self.civ_frame_meta
                if oor != 0:
                    return
                parts = [self.civ_wave_chunks[i] for i in range(2, expected_max + 1) if i in self.civ_wave_chunks]
                if not parts:
                    return
                raw = b"".join(parts)
                if len(raw) >= 50:
                    self.emit_civ_waveform(np.frombuffer(raw, dtype=np.uint8), scope_mode, f1, f2)

    def emit_civ_waveform(self, wave_u8, scope_mode, f1, f2):
        self.last_civ_frame_ts = time.monotonic()
        self.civ_scope_mode = scope_mode
        if scope_mode in (0, 2) and f2 is not None and f2 >= 2500:
            self.civ_half_span_hz = f2
        elif scope_mode in (1, 3) and f1 is not None and f2 is not None and f2 > f1:
            self.civ_low_hz = f1
            self.civ_high_hz = f2

        # Zet IC-7300 amplitude (0..160) om naar dB-schaal (-110..-20 dB) en schaal naar WF_WIDTH
        wave_db = -110.0 + (wave_u8.astype(np.float32) / 160.0) * 90.0
        x_src = np.linspace(0.0, 1.0, len(wave_db))
        x_dst = np.linspace(0.0, 1.0, WF_WIDTH)
        line_db = np.interp(x_dst, x_src, wave_db).astype(np.float32)
        self.push_line_db(line_db)

    # --- Audio FFT Worker (optionele USB Audio modus) ---
    def resample_fft_to_width(self, fft_db, fft_freqs):
        bin_hz = fft_freqs[1] - fft_freqs[0]
        max_bin = min(len(fft_db) - 1, int(np.ceil(self.audio_max_hz / bin_hz)))
        if max_bin <= 2:
            return np.full(WF_WIDTH, self.floor_db, dtype=np.float32)

        sub_freqs = fft_freqs[:max_bin + 1]
        sub_db = fft_db[:max_bin + 1]

        # Bij LSB/CW-R/RTTY loopt audio omgekeerd t.o.v. oplopende RF-frequentie
        mode = self.get_radio_mode()
        reverse_rf = any(m in mode for m in ("LSB", "CW-R")) or mode == "RTTY"

        if len(sub_db) > WF_WIDTH:
            edges = np.linspace(0, len(sub_db) - 1, WF_WIDTH + 1, dtype=int)
            out = np.empty(WF_WIDTH, dtype=np.float32)
            for i in range(WF_WIDTH):
                lo = edges[i]
                hi = max(lo + 1, edges[i + 1])
                out[i] = np.max(sub_db[lo:hi])
        else:
            target_freqs = np.linspace(0.0, float(self.audio_max_hz), WF_WIDTH)
            out = np.interp(target_freqs, sub_freqs, sub_db).astype(np.float32)

        return out[::-1].copy() if reverse_rf else out

    def audio_worker(self, dev_id):
        window = np.hanning(self.n_fft).astype(np.float32)
        win_norm = float(np.sum(window)) * 0.5
        ring = np.zeros(self.n_fft, dtype=np.float32)
        fft_freqs = np.fft.rfftfreq(self.n_fft, d=1.0 / self.sample_rate)

        try:
            with sd.InputStream(
                device=dev_id,
                channels=1,
                samplerate=self.sample_rate,
                blocksize=self.hop_size,
                dtype="float32"
            ) as stream:
                while self.is_running and not self.destroyed:
                    data, _ = stream.read(self.hop_size)
                    if self.is_paused:
                        continue
                    samples = data[:, 0]
                    ring = np.roll(ring, -self.hop_size)
                    ring[-self.hop_size:] = samples

                    mag = np.abs(np.fft.rfft(ring * window)) / win_norm
                    fft_db = 20.0 * np.log10(mag + 1e-6)
                    line_db = self.resample_fft_to_width(fft_db, fft_freqs)
                    self.push_line_db(line_db)

        except Exception as e:
            self.is_running = False
            if not self.destroyed and self.parent.winfo_exists():
                self.parent.after(0, lambda err=str(e): self.handle_audio_error(err))

    def handle_audio_error(self, err_msg):
        if not self.destroyed and self.parent.winfo_exists():
            self.update_source_controls()
            messagebox.showerror("Audiofout", err_msg)

    # --- UI Update Loop ---
    def ui_update_loop(self):
        if self.destroyed or not self.parent.winfo_exists():
            return

        low_hz, high_hz, vfo_hz = self.get_freq_bounds_hz()
        mode = self.get_radio_mode()
        axis_key = (low_hz, high_hz, vfo_hz, mode, self.source_var.get())
        if axis_key != self.last_axis_key:
            self.last_axis_key = axis_key
            self.draw_freq_axis()
            span_khz = (high_hz - low_hz) / 1000.0
            self.lbl_scope_status.config(
                text=f"VFO: {vfo_hz / 1_000_000:.6f} MHz | Span: {span_khz:g} kHz"
            )
            if not self.is_running:
                self.draw_spectrum(self.last_spectrum_db)
                self.update_waterfall_overlays()

        updated = False
        latest_db = None
        while not self.frame_queue.empty():
            try:
                line_db = self.frame_queue.get_nowait()
            except queue.Empty:
                break

            latest_db = line_db
            norm = (line_db - self.floor_db) / max(1.0, self.range_db)
            idx = np.clip(norm * 255.0, 0, 255).astype(np.uint8)
            row_rgb = self.lut[idx]

            self.wf_rgb[1:] = self.wf_rgb[:-1]
            self.wf_rgb[0] = row_rgb
            updated = True

        if updated and latest_db is not None:
            self.last_spectrum_db = latest_db
            self.refresh_waterfall_image()
            self.draw_spectrum(latest_db)

        self.parent.after(35, self.ui_update_loop)

    def on_close(self):
        self.destroyed = True
        self.is_running = False
