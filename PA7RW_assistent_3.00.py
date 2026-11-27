import tkinter as tk
from tkinter import ttk, messagebox
import serial
import time
import threading
import queue
import json
import os
import sys
from cw_decoder_1_06 import CWTerminalWindow
from rtty_terminal_1_00 import RTTYTerminalWindow
from waterfall_1_00 import WaterfallPanel

IC7300mk2_ADDRESS = 0xB6
CONTROLLER_ADDRESS = 0xE0
MAX_SWR = 2.8


def get_base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


DEFAULT_CONFIG = {
    "settings": {
        "com_port": "COM4",
        "baudrate": "115200",
        "civ_address": "B6",
        "max_swr": 2.8,
        "auto_connect": True,
        "auto_scope": True,
        "default_wf_source": "Radio RF Scope (CI-V)",
    },
    "station": {
        "callsign": "PA7RW",
        "operator": "Rob",
        "qth": "Heerhugowaard",
        "grid": "",
        "cq_zone": "14",
        "itu_zone": "27",
        "rig": "Icom IC-7300mk2",
    },
}

MODES = {
    "LSB":   b'\x00\x01',
    "USB":   b'\x01\x01',
    "AM":    b'\x02\x01',
    "CW":    b'\x03\x01',
    "RTTY":  b'\x04\x01',
    "FM":    b'\x05\x01',
    "LSB-D": b'\x00\x01',
    "USB-D": b'\x01\x01',
}

REVERSE_MODES = {
    0x00: "LSB",
    0x01: "USB",
    0x02: "AM",
    0x03: "CW",
    0x04: "RTTY",
    0x05: "FM",
    0x07: "CW-R",
    0x08: "RTTY-R",
}


class IC7300mk2ControlApp:
    def __init__(self, root):
        self.root = root
        self.config_path = os.path.join(get_base_dir(), "pa7rw_config.json")
        self.config_data = self.load_config()
        self.radio_address = self.parse_civ_hex(self.config_data["settings"].get("civ_address", "B6"), IC7300mk2_ADDRESS)
        self.max_swr = float(self.config_data["settings"].get("max_swr", MAX_SWR))

        self.com_port = self.config_data["settings"].get("com_port", "COM4")
        self.baudrate = str(self.config_data["settings"].get("baudrate", "115200"))

        self.ser = None
        self.ser_lock = threading.Lock()
        self.cmd_lock = threading.Lock()
        self.civ_resp_queue = queue.Queue(maxsize=64)

        self.is_connected = False
        self.manual_tx_active = False
        self.swr_tripped = False
        self.tripped_swr_val = 0.0
        self.current_swr_val = 1.00
        self.has_measured_swr = False
        self.saved_rf_power_bcd = b'\x01\x28'
        self.user_dragging_slider = False
        self.current_freq_hz = 7074000
        self.vfo_step = 100
        self.vfo_angle = 0.0

        self.create_menu()
        self.create_widgets()
        self.apply_config_to_ui()
        self.update_window_title()
        self.root.geometry("540x890")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.on_app_close)
        # Verbind automatisch met de radio bij opstarten na 200 ms indien ingeschakeld
        if self.config_data["settings"].get("auto_connect", True):
            self.root.after(200, self.auto_connect)

    def parse_civ_hex(self, hex_str, fallback):
        try:
            val = int(str(hex_str).strip().replace("0x", "").replace("0X", ""), 16)
            if 0x01 <= val <= 0xDF:
                return val
        except ValueError:
            pass
        return fallback

    def load_config(self):
        cfg = {
            "settings": dict(DEFAULT_CONFIG["settings"]),
            "station": dict(DEFAULT_CONFIG["station"]),
        }
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    if isinstance(loaded.get("settings"), dict):
                        cfg["settings"].update(loaded["settings"])
                    if isinstance(loaded.get("station"), dict):
                        cfg["station"].update(loaded["station"])
            except Exception:
                pass
        return cfg

    def save_config(self):
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.config_data, f, indent=2)

    def update_window_title(self):
        call = self.config_data["station"].get("callsign", "PA7RW").strip() or "PA7RW"
        rig = self.config_data["station"].get("rig", "Icom IC-7300mk2").strip() or "Icom IC-7300mk2"
        conn_txt = f"{self.com_port}: Verbonden" if getattr(self, "is_connected", False) else f"{self.com_port}: Niet verbonden"
        self.root.title(f"PA7RWAssist 3.00 - {call} ({rig}) | {conn_txt}")

    def create_menu(self):
        self.menubar = tk.Menu(self.root)
        self.file_menu = tk.Menu(self.menubar, tearoff=0)
        self.file_menu.add_command(label="Verbinden", command=self.toggle_connection)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="CW Decoder", command=self.open_cw_terminal)
        self.file_menu.add_command(label="RTTY Decoder", command=self.open_rtty_terminal)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Settings", command=self.open_settings_dialog)
        self.file_menu.add_command(label="Station Setup", command=self.open_station_setup_dialog)
        self.file_menu.add_separator()
        self.file_menu.add_command(label="Exit", command=self.on_app_close)
        self.menubar.add_cascade(label="File", menu=self.file_menu)
        self.root.config(menu=self.menubar)

    def apply_config_to_ui(self):
        s = self.config_data["settings"]
        self.com_port = s.get("com_port", "COM4")
        self.baudrate = str(s.get("baudrate", "115200"))
        self.radio_address = self.parse_civ_hex(s.get("civ_address", "B6"), IC7300mk2_ADDRESS)
        self.max_swr = float(s.get("max_swr", MAX_SWR))
        if hasattr(self, "tuner_frame"):
            self.tuner_frame.config(text=f" Transmit (Afstemmen) & Autotuner & SWR (Max {self.max_swr:.1f}) ")
        if hasattr(self, "wf_panel"):
            src = s.get("default_wf_source", "Radio RF Scope (CI-V)")
            if src in ("Radio RF Scope (CI-V)", "Audio RX (USB)") and self.wf_panel.source_var.get() != src:
                self.wf_panel.source_var.set(src)
                self.wf_panel.on_source_change()
        self.update_window_title()

    def open_settings_dialog(self):
        if hasattr(self, "settings_dlg") and self.settings_dlg.winfo_exists():
            self.settings_dlg.lift()
            return

        dlg = tk.Toplevel(self.root)
        dlg.title("Settings - PA7RWAssist 3.00")
        dlg.geometry("420x330")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()
        self.settings_dlg = dlg

        s = self.config_data["settings"]
        frm = ttk.Frame(dlg, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Standaard COM-poort:").grid(row=0, column=0, sticky="w", pady=4)
        port_var = tk.StringVar(value=s.get("com_port", "COM4"))
        ttk.Entry(frm, textvariable=port_var, width=14).grid(row=0, column=1, sticky="w", padx=8)

        ttk.Label(frm, text="Standaard Baudrate:").grid(row=1, column=0, sticky="w", pady=4)
        baud_var = tk.StringVar(value=str(s.get("baudrate", "115200")))
        ttk.Combobox(
            frm, textvariable=baud_var,
            values=["4800", "9600", "19200", "38400", "115200"],
            width=12, state="readonly"
        ).grid(row=1, column=1, sticky="w", padx=8)

        ttk.Label(frm, text="CI-V Radio Adres (hex):").grid(row=2, column=0, sticky="w", pady=4)
        civ_var = tk.StringVar(value=s.get("civ_address", "B6"))
        ttk.Entry(frm, textvariable=civ_var, width=14).grid(row=2, column=1, sticky="w", padx=8)

        ttk.Label(frm, text="Max SWR Beveiliging:").grid(row=3, column=0, sticky="w", pady=4)
        swr_var = tk.StringVar(value=str(s.get("max_swr", 2.8)))
        ttk.Entry(frm, textvariable=swr_var, width=14).grid(row=3, column=1, sticky="w", padx=8)

        ttk.Label(frm, text="Standaard Waterfall Bron:").grid(row=4, column=0, sticky="w", pady=4)
        wf_src_var = tk.StringVar(value=s.get("default_wf_source", "Radio RF Scope (CI-V)"))
        ttk.Combobox(
            frm, textvariable=wf_src_var,
            values=["Radio RF Scope (CI-V)", "Audio RX (USB)"],
            width=22, state="readonly"
        ).grid(row=4, column=1, sticky="w", padx=8)

        auto_conn_var = tk.BooleanVar(value=bool(s.get("auto_connect", True)))
        ttk.Checkbutton(frm, text="Automatisch verbinden bij opstarten", variable=auto_conn_var).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(8, 2)
        )

        auto_scope_var = tk.BooleanVar(value=bool(s.get("auto_scope", True)))
        ttk.Checkbutton(frm, text="RF Scope automatisch activeren bij verbinden", variable=auto_scope_var).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=2
        )

        btn_row = ttk.Frame(frm)
        btn_row.grid(row=7, column=0, columnspan=2, pady=(16, 0), sticky="e")

        def save_settings():
            try:
                max_swr_val = float(swr_var.get().replace(",", "."))
                if max_swr_val < 1.2 or max_swr_val > 10.0:
                    raise ValueError
            except ValueError:
                messagebox.showwarning("Ongeldige SWR", "Voer een geldige Max SWR in tussen 1.2 en 10.0.", parent=dlg)
                return

            civ_clean = civ_var.get().strip().upper().replace("0X", "")
            if self.parse_civ_hex(civ_clean, None) is None:
                messagebox.showwarning("Ongeldig adres", "Voer een geldig CI-V hex-adres in (bijv. B6 of 94).", parent=dlg)
                return

            self.config_data["settings"] = {
                "com_port": port_var.get().strip() or "COM4",
                "baudrate": baud_var.get(),
                "civ_address": civ_clean,
                "max_swr": round(max_swr_val, 2),
                "auto_connect": bool(auto_conn_var.get()),
                "auto_scope": bool(auto_scope_var.get()),
                "default_wf_source": wf_src_var.get(),
            }
            self.save_config()
            self.apply_config_to_ui()
            dlg.destroy()

        ttk.Button(btn_row, text="Annuleren", command=dlg.destroy).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Opslaan", command=save_settings).pack(side="left", padx=4)

    def open_station_setup_dialog(self):
        if hasattr(self, "station_dlg") and self.station_dlg.winfo_exists():
            self.station_dlg.lift()
            return

        dlg = tk.Toplevel(self.root)
        dlg.title("Station Setup - PA7RWAssist 3.00")
        dlg.geometry("420x320")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()
        self.station_dlg = dlg

        st = self.config_data["station"]
        frm = ttk.Frame(dlg, padding=12)
        frm.pack(fill="both", expand=True)

        fields = [
            ("Roepletters (Callsign):", "callsign", st.get("callsign", "PA7RW")),
            ("Naam Operator:", "operator", st.get("operator", "Rob")),
            ("QTH (Plaats):", "qth", st.get("qth", "Heerhugowaard")),
            ("Grid Locator:", "grid", st.get("grid", "")),
            ("CQ Zone:", "cq_zone", st.get("cq_zone", "14")),
            ("ITU Zone:", "itu_zone", st.get("itu_zone", "27")),
            ("Transceiver / Rig:", "rig", st.get("rig", "Icom IC-7300mk2")),
        ]
        vars_map = {}
        for row_idx, (label, key, default_val) in enumerate(fields):
            ttk.Label(frm, text=label).grid(row=row_idx, column=0, sticky="w", pady=4)
            v = tk.StringVar(value=default_val)
            ttk.Entry(frm, textvariable=v, width=26).grid(row=row_idx, column=1, sticky="ew", padx=8)
            vars_map[key] = v
        frm.columnconfigure(1, weight=1)

        btn_row = ttk.Frame(frm)
        btn_row.grid(row=len(fields), column=0, columnspan=2, pady=(16, 0), sticky="e")

        def save_station():
            self.config_data["station"] = {
                "callsign": vars_map["callsign"].get().strip().upper() or "PA7RW",
                "operator": vars_map["operator"].get().strip(),
                "qth": vars_map["qth"].get().strip(),
                "grid": vars_map["grid"].get().strip().upper(),
                "cq_zone": vars_map["cq_zone"].get().strip(),
                "itu_zone": vars_map["itu_zone"].get().strip(),
                "rig": vars_map["rig"].get().strip() or "Icom IC-7300mk2",
            }
            self.save_config()
            self.update_window_title()
            dlg.destroy()

        ttk.Button(btn_row, text="Annuleren", command=dlg.destroy).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Opslaan", command=save_station).pack(side="left", padx=4)

    def auto_connect(self):
        if not self.is_connected:
            self.toggle_connection()

    def create_widgets(self):
        main_panel = ttk.Frame(self.root)
        main_panel.pack(fill="both", expand=True, padx=8, pady=4)

        # --- Frequentie Paneel ---
        freq_frame = ttk.LabelFrame(main_panel, text=" Frequentie (Hz) ", padding=6)
        freq_frame.pack(fill="x", pady=2)

        freq_top_row = ttk.Frame(freq_frame)
        freq_top_row.pack(fill="x", pady=1)

        self.lbl_current_freq = ttk.Label(freq_top_row, text="00.000.000 Hz", font=("Arial", 15, "bold"))
        self.lbl_current_freq.pack(side="left", padx=4)

        btn_set_freq = ttk.Button(freq_top_row, text="Instellen", command=self.set_frequency)
        btn_set_freq.pack(side="right", padx=4)

        self.freq_entry = ttk.Entry(freq_top_row, width=12)
        self.freq_entry.pack(side="right", padx=4)
        self.freq_entry.insert(0, "7074000")

        # --- Live S-Meter (RX) ---
        smeter_frame = ttk.Frame(freq_frame)
        smeter_frame.pack(fill="x", pady=(4, 0))

        self.lbl_smeter = ttk.Label(smeter_frame, text="S-Meter: S0", font=("Arial", 9, "bold"), width=18)
        self.lbl_smeter.pack(side="left", padx=4)

        self.smeter_bar = ttk.Progressbar(smeter_frame, orient="horizontal", mode="determinate", maximum=241, value=0)
        self.smeter_bar.pack(side="right", fill="x", expand=True, padx=4)

        # --- VFO Draaiknop & Stapgrootte ---
        vfo_frame = ttk.Frame(freq_frame)
        vfo_frame.pack(fill="x", pady=(4, 1))

        self.vfo_canvas = tk.Canvas(vfo_frame, width=70, height=70, highlightthickness=0)
        self.vfo_canvas.pack(side="left", padx=8)
        self.draw_vfo_knob()

        self.vfo_canvas.bind("<MouseWheel>", self.on_vfo_wheel)
        self.vfo_canvas.bind("<B1-Motion>", self.on_vfo_drag)
        self.lbl_current_freq.bind("<MouseWheel>", self.on_vfo_wheel)
        step_box = ttk.Frame(vfo_frame)
        step_box.pack(side="left", fill="both", expand=True, padx=4)

        self.lbl_vfo_step = ttk.Label(step_box, text="VFO Stap: 100 Hz (Scroll of sleep knop)", font=("Arial", 9, "bold"))
        self.lbl_vfo_step.pack(anchor="w", pady=(1, 3))

        step_btn_row = ttk.Frame(step_box)
        step_btn_row.pack(fill="x")
        for s_label, s_val in [("10 Hz", 10), ("100 Hz", 100), ("1 kHz", 1000), ("5 kHz", 5000)]:
            ttk.Button(
                step_btn_row,
                text=s_label,
                width=6,
                command=lambda v=s_val, lbl=s_label: self.set_vfo_step(v, lbl)
            ).pack(side="left", expand=True, fill="x", padx=2)

        # --- Modus Paneel (zonder decoder-knoppen; die staan in File) ---
        mode_frame = ttk.LabelFrame(main_panel, text=" Modus ", padding=6)
        mode_frame.pack(fill="x", pady=2)

        self.lbl_current_mode = ttk.Label(mode_frame, text="Huidige modus: ONBEKEND", font=("Arial", 9, "italic"))
        self.lbl_current_mode.pack(pady=1)

        btn_buttons_frame = ttk.Frame(mode_frame)
        btn_buttons_frame.pack(fill="x", pady=2)

        modes_list = ["LSB", "USB", "CW", "RTTY", "LSB-D", "USB-D", "AM", "FM"]
        for i, m in enumerate(modes_list):
            rij = i // 4
            kolom = i % 4
            btn = ttk.Button(btn_buttons_frame, text=m, command=lambda mode=m: self.set_mode(mode))
            btn.grid(row=rij, column=kolom, padx=2, pady=2, sticky="ew")
            btn_buttons_frame.columnconfigure(kolom, weight=1)

        # --- Snelle Band-knoppen Paneel ---
        band_frame = ttk.LabelFrame(main_panel, text=" Snelle Bandkeuze ", padding=6)
        band_frame.pack(fill="x", pady=2)

        band_btn_frame = ttk.Frame(band_frame)
        band_btn_frame.pack(fill="x")

        banden_lijst = [
            ("160m", 1840000), ("80m", 3573000), ("60m", 5357000), ("40m", 7074000), ("30m", 10136000),
            ("20m", 14074000), ("17m", 18100000), ("15m", 21074000), ("12m", 24915000), ("10m", 28074000),
        ]
        for i, (naam, freq) in enumerate(banden_lijst):
            rij = i // 5
            kolom = i % 5
            btn = ttk.Button(
                band_btn_frame,
                text=naam,
                command=lambda f=freq: self.select_band(f)
            )
            btn.grid(row=rij, column=kolom, padx=2, pady=2, sticky="ew")
            band_btn_frame.columnconfigure(kolom, weight=1)

        # --- RF Power Schuifregelaar Paneel ---
        pwr_frame = ttk.LabelFrame(main_panel, text=" RF Zendvermogen (0% - 100%) ", padding=6)
        pwr_frame.pack(fill="x", pady=2)

        pwr_row = ttk.Frame(pwr_frame)
        pwr_row.pack(fill="x")

        self.pwr_var = tk.IntVar(value=50)
        self.pwr_slider = ttk.Scale(
            pwr_row,
            from_=0,
            to=100,
            orient="horizontal",
            variable=self.pwr_var,
            command=self.on_slider_move
        )
        self.pwr_slider.pack(side="left", fill="x", expand=True, padx=4)
        self.pwr_slider.bind("<ButtonPress-1>", lambda e: setattr(self, "user_dragging_slider", True))
        self.pwr_slider.bind("<ButtonRelease-1>", self.on_slider_release)

        self.lbl_pwr_val = ttk.Label(pwr_row, text="50%", font=("Arial", 10, "bold"), width=5)
        self.lbl_pwr_val.pack(side="right", padx=4)

        # --- Transmit (Afstemmen) & Autotuner & SWR Paneel ---
        self.tuner_frame = ttk.LabelFrame(main_panel, text=f" Transmit (Afstemmen) & Autotuner & SWR (Max {self.max_swr:.1f}) ", padding=6)
        self.tuner_frame.pack(fill="x", pady=2)
        tuner_frame = self.tuner_frame

        self.btn_transmit = tk.Button(
            tuner_frame,
            text="TRANSMIT: UIT (Klik om te zenden)",
            font=("Arial", 9, "bold"),
            bg="SystemButtonFace",
            fg="black",
            height=1,
            command=self.toggle_transmit
        )
        self.btn_transmit.pack(fill="x", pady=2)

        tuner_btn_row = ttk.Frame(tuner_frame)
        tuner_btn_row.pack(fill="x", pady=2)

        self.btn_tuner_on = tk.Button(tuner_btn_row, text="Tuner AAN", command=self.tuner_on, relief="raised")
        self.btn_tuner_on.pack(side="left", expand=True, fill="x", padx=2)

        self.btn_tuner_off = tk.Button(tuner_btn_row, text="Tuner UIT", command=self.tuner_off, relief="raised")
        self.btn_tuner_off.pack(side="left", expand=True, fill="x", padx=2)

        ttk.Button(tuner_btn_row, text="TUNE starten", command=self.start_tune).pack(side="left", expand=True, fill="x", padx=2)

        swr_info_row = ttk.Frame(tuner_frame)
        swr_info_row.pack(fill="x", pady=2)

        self.lbl_tuner_status = ttk.Label(swr_info_row, text="Tuner: Onbekend | RF Power: --%", font=("Arial", 9, "bold"))
        self.lbl_tuner_status.pack(side="left", padx=2)

        self.lbl_swr = ttk.Label(swr_info_row, text="SWR: 1.00 (RX)", font=("Arial", 11, "bold"), foreground="green")
        self.lbl_swr.pack(side="right", padx=2)

        self.btn_reset_swr = ttk.Button(
            tuner_frame,
            text="Reset SWR Beveiliging (Herstel RF Power)",
            command=self.reset_swr_trip,
            state="disabled"
        )
        self.btn_reset_swr.pack(fill="x", pady=(2, 0))

        # --- Ingebouwde Waterfall onderin het hoofdscherm ---
        wf_container = ttk.Frame(main_panel)
        wf_container.pack(fill="x", pady=(2, 0))
        self.wf_panel = WaterfallPanel(wf_container, self)

    # --- Transmit Aan/Uit-schakelaar (Afstemmen) ---
    def toggle_transmit(self):
        if not self.is_connected:
            messagebox.showwarning("Niet verbonden", "Maak eerst verbinding met de IC-7300MK2.")
            return
        if not self.manual_tx_active:
            self.manual_tx_active = True
            self.send_cmd(b'\x1C\x00', b'\x01')
            self.btn_transmit.config(
                text="TRANSMIT: ACTIEF (Klik voor RX)",
                bg="#dc3545",
                fg="white",
                activebackground="#c82333"
            )
        else:
            self.manual_tx_active = False
            self.send_cmd(b'\x1C\x00', b'\x00')
            self.btn_transmit.config(
                text="TRANSMIT: UIT (Klik om te zenden)",
                bg="SystemButtonFace",
                fg="black"
            )

    # --- RF Power Schuifregelaar functies ---
    def on_slider_move(self, val):
        pct = int(round(float(val)))
        self.lbl_pwr_val.config(text=f"{pct}%")

    def on_slider_release(self, event):
        self.user_dragging_slider = False
        if self.swr_tripped:
            messagebox.showwarning("Beveiliging actief", "SWR-beveiliging is actief! Klik eerst op 'Reset SWR Beveiliging'.")
            self.pwr_var.set(0)
            self.lbl_pwr_val.config(text="0%")
            return
        pct = int(round(self.pwr_var.get()))
        self.set_rf_power_pct(pct)

    def set_rf_power_pct(self, pct):
        pct = max(0, min(100, pct))
        raw_val = round((pct / 100.0) * 255)
        bcd_str = f"{raw_val:04d}"
        bcd_bytes = bytes([int(bcd_str[0:2], 16), int(bcd_str[2:4], 16)])
        if raw_val > 0:
            self.saved_rf_power_bcd = bcd_bytes
        self.send_cmd(b'\x14\x0A', bcd_bytes)

    def toggle_connection(self):
        if not self.is_connected:
            port = self.com_port.strip()
            baud = int(self.baudrate)
            try:
                self.ser = serial.Serial(
                    port=port,
                    baudrate=baud,
                    timeout=0.05,
                    rtscts=False,
                    dsrdtr=False
                )
                self.ser.dtr = False
                self.ser.rts = False
                self.is_connected = True
                self.file_menu.entryconfig(0, label="Ontkoppelen")
                self.update_window_title()
                threading.Thread(target=self.serial_reader_loop, daemon=True).start()
                threading.Thread(target=self.update_loop, daemon=True).start()
                if self.config_data["settings"].get("auto_scope", True):
                    self.wf_panel.on_radio_connected()
            except Exception as e:
                messagebox.showerror("Fout", f"Kan geen verbinding maken met {port}:\n{str(e)}")
        else:
            self.wf_panel.on_radio_disconnected()
            if self.manual_tx_active:
                self.send_cmd(b'\x1C\x00', b'\x00')
                self.manual_tx_active = False
            self.send_cmd(b'\x27\x11', b'\x00')
            self.is_connected = False
            time.sleep(0.08)
            if self.ser and self.ser.is_open:
                self.ser.close()
            self.file_menu.entryconfig(0, label="Verbinden")
            self.update_window_title()
            self.btn_transmit.config(text="TRANSMIT: UIT (Klik om te zenden)", bg="SystemButtonFace", fg="black")
            self.lbl_current_freq.config(text="00.000.000 Hz")
            self.lbl_current_mode.config(text="Huidige modus: ONBEKEND")
            self.lbl_tuner_status.config(text="Tuner: Niet verbonden | RF Power: --%")
            self.lbl_swr.config(text="SWR: Niet verbonden", foreground="gray")
            self.btn_tuner_on.config(bg="SystemButtonFace", fg="black")
            self.btn_tuner_off.config(bg="SystemButtonFace", fg="black")

    def serial_reader_loop(self):
        """Centrale CI-V lezer: routeert 27 00 scope-pakketten naar de waterfall en andere antwoorden naar send_cmd."""
        buf = bytearray()
        while self.is_connected and self.ser and self.ser.is_open:
            try:
                waiting = self.ser.in_waiting
                chunk = self.ser.read(waiting if waiting > 0 else 1)
            except Exception:
                break
            if not chunk:
                continue

            buf.extend(chunk)
            while True:
                end_idx = buf.find(b'\xFD')
                if end_idx == -1:
                    break
                raw = bytes(buf[:end_idx + 1])
                del buf[:end_idx + 1]

                start_idx = raw.rfind(b'\xFE\xFE')
                if start_idx == -1:
                    continue
                frame = raw[start_idx:]
                if len(frame) < 6:
                    continue

                # Negeer echo van onze eigen uitgaande commando's
                if frame[3] == CONTROLLER_ADDRESS:
                    continue

                if frame[4] == 0x27 and len(frame) >= 7 and frame[5] == 0x00:
                    self.wf_panel.handle_civ_scope_frame(frame)
                else:
                    try:
                        self.civ_resp_queue.put_nowait(frame)
                    except queue.Full:
                        try:
                            self.civ_resp_queue.get_nowait()
                        except queue.Empty:
                            pass
                        try:
                            self.civ_resp_queue.put_nowait(frame)
                        except queue.Full:
                            pass

    def send_cmd(self, cmd, data=b''):
        if not self.is_connected or not self.ser or not self.ser.is_open:
            return None
        frame = b'\xFE\xFE' + bytes([self.radio_address, CONTROLLER_ADDRESS]) + cmd + data + b'\xFD'
        with self.cmd_lock:
            while not self.civ_resp_queue.empty():
                try:
                    self.civ_resp_queue.get_nowait()
                except queue.Empty:
                    break
            try:
                with self.ser_lock:
                    self.ser.write(frame)
            except Exception:
                return None

            deadline = time.monotonic() + 0.25
            collected = b''
            while time.monotonic() < deadline:
                rem = max(0.005, deadline - time.monotonic())
                try:
                    resp = self.civ_resp_queue.get(timeout=min(0.05, rem))
                    collected += resp
                    if len(resp) >= 6 and (resp[4] == cmd[0] or resp[4] in (0xFB, 0xFA)):
                        return collected
                except queue.Empty:
                    if collected:
                        return collected
            return collected or None

    def tuner_on(self):
        self.send_cmd(b'\x1C\x01', b'\x01')

    def tuner_off(self):
        self.send_cmd(b'\x1C\x01', b'\x00')

    def start_tune(self):
        if self.swr_tripped:
            messagebox.showwarning("Beveiliging actief", "Klik eerst op 'Reset SWR Beveiliging' voordat je gaat tunen.")
            return
        self.send_cmd(b'\x1C\x01', b'\x02')

    def trigger_swr_protection(self, swr_val):
        if not self.swr_tripped or swr_val > self.tripped_swr_val:
            self.tripped_swr_val = swr_val
        if not self.swr_tripped:
            self.swr_tripped = True
            self.send_cmd(b'\x14\x0A', b'\x00\x00')
            self.send_cmd(b'\x1C\x00', b'\x00')
            self.pwr_var.set(0)
            self.lbl_pwr_val.config(text="0%")
            self.btn_reset_swr.config(state="normal")
        self.lbl_swr.config(
            text=f"ALARM SWR: {self.tripped_swr_val:.2f} (> {self.max_swr:.1f}) -> 0%!",
            foreground="red"
        )

    def reset_swr_trip(self):
        self.send_cmd(b'\x14\x0A', self.saved_rf_power_bcd)
        self.swr_tripped = False
        self.tripped_swr_val = 0.0
        self.btn_reset_swr.config(state="disabled")
        self.lbl_swr.config(text=f"SWR: {self.current_swr_val:.2f} (RX - Gereset)", foreground="green")

    def raw_to_smeter(self, raw_val):
        if raw_val <= 120:
            s_punt = round((raw_val / 120.0) * 9)
            return f"S{s_punt}"
        else:
            db_over = round(((raw_val - 120) / 121.0) * 60)
            return f"S9+{db_over}dB"

    def raw_to_swr(self, raw_val):
        if raw_val <= 48:
            return 1.0 + (raw_val / 48.0) * 0.5
        elif raw_val <= 80:
            return 1.5 + ((raw_val - 48) / 32.0) * 0.5
        elif raw_val <= 120:
            return 2.0 + ((raw_val - 80) / 40.0) * 1.0
        else:
            return 3.0 + ((raw_val - 120) / 30.0) * 1.0

    def select_band(self, freq_hz):
        self.freq_entry.delete(0, tk.END)
        self.freq_entry.insert(0, str(freq_hz))
        self.set_frequency()
        huidige_modus = self.lbl_current_mode.cget("text")
        if "LSB" in huidige_modus or "USB" in huidige_modus:
            is_data = "-D" in huidige_modus
            nieuwe_modus = ("LSB" if freq_hz < 10_000_000 else "USB") + ("-D" if is_data else "")
            time.sleep(0.06)
            self.set_mode(nieuwe_modus)

    def draw_vfo_knob(self):
        import math
        self.vfo_canvas.delete("all")
        self.vfo_canvas.create_oval(4, 4, 66, 66, fill="#2b2b2b", outline="#555555", width=3)
        self.vfo_canvas.create_oval(9, 9, 61, 61, fill="#3c3f41", outline="#1e1e1e", width=1)
        cx, cy, r = 35, 35, 17
        rad = math.radians(self.vfo_angle)
        dx = cx + r * math.cos(rad)
        dy = cy + r * math.sin(rad)
        self.vfo_canvas.create_oval(dx - 5, dy - 5, dx + 5, dy + 5, fill="#d9d9d9", outline="#111111", width=1)

    def set_vfo_step(self, step_hz, label):
        self.vfo_step = step_hz
        self.lbl_vfo_step.config(text=f"VFO Stap: {label} (Scroll of sleep knop)")

    def step_vfo(self, direction):
        self.current_freq_hz = max(30000, min(74800000, self.current_freq_hz + direction * self.vfo_step))
        self.vfo_angle = (self.vfo_angle + direction * 18) % 360
        self.draw_vfo_knob()
        self.lbl_current_freq.config(text=f"{self.current_freq_hz / 1_000_000:.6f} MHz")
        self.freq_entry.delete(0, tk.END)
        self.freq_entry.insert(0, str(self.current_freq_hz))
        self.set_frequency()

    def on_vfo_wheel(self, event):
        direction = 1 if event.delta > 0 else -1
        self.step_vfo(direction)

    def on_vfo_drag(self, event):
        import math
        new_angle = math.degrees(math.atan2(event.y - 35, event.x - 35))
        diff = (new_angle - self.vfo_angle + 180) % 360 - 180
        if abs(diff) >= 12:
            direction = 1 if diff > 0 else -1
            self.vfo_angle = new_angle
            self.step_vfo(direction)

    def set_frequency(self):
        try:
            freq_hz = int(self.freq_entry.get())
            self.current_freq_hz = freq_hz
            freq_str = f"{freq_hz:010d}"
            bcd = bytes([
                int(freq_str[8:10], 16),
                int(freq_str[6:8], 16),
                int(freq_str[4:6], 16),
                int(freq_str[2:4], 16),
                int(freq_str[0:2], 16)
            ])
            self.send_cmd(b'\x05', bcd)
        except ValueError:
            messagebox.showwarning("Invoerfout", "Voer een geldige frequentie in Hz in (bijv. 7074000).")

    def open_cw_terminal(self):
        if hasattr(self, "cw_win") and self.cw_win.win.winfo_exists():
            self.cw_win.win.lift()
        else:
            self.cw_win = CWTerminalWindow(self)

    def open_rtty_terminal(self):
        if hasattr(self, "rtty_win") and self.rtty_win.win.winfo_exists():
            self.rtty_win.win.lift()
        else:
            self.rtty_win = RTTYTerminalWindow(self)

    def set_mode(self, mode_name):
        if mode_name in MODES:
            self.send_cmd(b'\x06', MODES[mode_name])
            time.sleep(0.06)
            if mode_name.endswith("-D"):
                self.send_cmd(b'\x1A\x06', b'\x01\x01')
            else:
                self.send_cmd(b'\x1A\x06', b'\x00\x00')
            if mode_name in ["RTTY", "RTTY-R"]:
                self.open_rtty_terminal()
            elif mode_name in ["CW", "CW-R"]:
                self.open_cw_terminal()

    def on_app_close(self):
        if self.is_connected:
            try:
                self.send_cmd(b'\x27\x11', b'\x00')
                if self.manual_tx_active:
                    self.send_cmd(b'\x1C\x00', b'\x00')
            except Exception:
                pass
        self.is_connected = False
        self.wf_panel.on_close()
        if hasattr(self, "cw_win") and self.cw_win.win.winfo_exists():
            try:
                self.cw_win.on_close()
            except Exception:
                pass
        if hasattr(self, "rtty_win") and self.rtty_win.win.winfo_exists():
            try:
                self.rtty_win.on_close()
            except Exception:
                pass
        time.sleep(0.08)
        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
            except Exception:
                pass
        self.root.destroy()

    def query_status(self):
        civ_prefix = b'\xFE\xFE' + bytes([CONTROLLER_ADDRESS, self.radio_address])
        res_ptt = self.send_cmd(b'\x1C\x00')
        is_tx = self.manual_tx_active
        if res_ptt:
            p_idx = res_ptt.find(civ_prefix + b'\x1C\x00')
            if p_idx != -1 and len(res_ptt) >= p_idx + 8:
                if res_ptt[p_idx + 6] == 0x01:
                    is_tx = True

        res_swr = self.send_cmd(b'\x15\x12')
        if res_swr:
            s_idx = res_swr.find(civ_prefix + b'\x15\x12')
            if s_idx != -1 and len(res_swr) >= s_idx + 9:
                try:
                    raw_swr = int(res_swr[s_idx+6:s_idx+8].hex())
                    if is_tx or raw_swr > 0:
                        is_tx = True
                        swr_val = self.raw_to_swr(raw_swr)
                        self.current_swr_val = swr_val
                        self.has_measured_swr = True

                        if swr_val > self.max_swr:
                            if self.manual_tx_active:
                                self.lbl_swr.config(text=f"SWR: {swr_val:.2f} (TX - HOOG!)", foreground="red")
                            else:
                                self.trigger_swr_protection(swr_val)
                        elif not self.swr_tripped:
                            kleur = "orange" if swr_val > 2.0 else "green"
                            self.lbl_swr.config(text=f"SWR: {swr_val:.2f} (TX)", foreground=kleur)
                        return True
                except ValueError:
                    pass

        if is_tx:
            return True

        res_pwr = self.send_cmd(b'\x14\x0A')
        pwr_pct = "--"
        if res_pwr:
            w_idx = res_pwr.find(civ_prefix + b'\x14\x0A')
            if w_idx != -1 and len(res_pwr) >= w_idx + 9:
                bcd_bytes = res_pwr[w_idx+6:w_idx+8]
                try:
                    raw_pwr = int(bcd_bytes.hex())
                    pwr_pct = round((raw_pwr / 255) * 100)
                    if not self.swr_tripped and raw_pwr > 0:
                        self.saved_rf_power_bcd = bcd_bytes
                    if not self.user_dragging_slider:
                        self.pwr_var.set(pwr_pct)
                        self.lbl_pwr_val.config(text=f"{pwr_pct}%")
                except ValueError:
                    pass

        res_tuner = self.send_cmd(b'\x1C\x01')
        tuner_txt = "Onbekend"
        if res_tuner:
            t_idx = res_tuner.find(civ_prefix + b'\x1C\x01')
            if t_idx != -1 and len(res_tuner) >= t_idx + 8:
                t_state = res_tuner[t_idx + 6]
                tuner_txt = {0x00: "UIT", 0x01: "AAN", 0x02: "BEZIG MET TUNEN..."}.get(t_state, "Onbekend")

        self.lbl_tuner_status.config(text=f"Tuner: {tuner_txt} | RF Power: {pwr_pct}%")

        if tuner_txt in ["AAN", "BEZIG MET TUNEN..."]:
            self.btn_tuner_on.config(bg="#28a745", fg="white", activebackground="#218838")
            self.btn_tuner_off.config(bg="SystemButtonFace", fg="black")
        elif tuner_txt == "UIT":
            self.btn_tuner_on.config(bg="SystemButtonFace", fg="black")
            self.btn_tuner_off.config(bg="#dc3545", fg="white", activebackground="#c82333")
        else:
            self.btn_tuner_on.config(bg="SystemButtonFace", fg="black")
            self.btn_tuner_off.config(bg="SystemButtonFace", fg="black")

        if self.swr_tripped:
            self.lbl_swr.config(
                text=f"ALARM SWR: {self.tripped_swr_val:.2f} (> {self.max_swr:.1f}) -> 0%!",
                foreground="red"
            )
        elif self.has_measured_swr:
            kleur = "red" if self.current_swr_val > self.max_swr else ("orange" if self.current_swr_val > 2.0 else "green")
            self.lbl_swr.config(text=f"SWR: {self.current_swr_val:.2f} (RX)", foreground=kleur)
        else:
            self.lbl_swr.config(text="SWR: 1.00 (RX)", foreground="green")

        res_freq = self.send_cmd(b'\x03')
        res_mode = self.send_cmd(b'\x04')
        res_data = self.send_cmd(b'\x1A\x06')
        self.parse_and_update(res_freq, res_mode, res_data)

        # Lees live S-meter tijdens ontvangst (CI-V commando 15 02)
        res_smeter = self.send_cmd(b'\x15\x02')
        if res_smeter:
            sm_idx = res_smeter.find(civ_prefix + b'\x15\x02')
            if sm_idx != -1 and len(res_smeter) >= sm_idx + 9:
                try:
                    raw_sm = int(res_smeter[sm_idx+6:sm_idx+8].hex())
                    s_tekst = self.raw_to_smeter(raw_sm)
                    self.lbl_smeter.config(text=f"S-Meter: {s_tekst}")
                    self.smeter_bar["value"] = min(241, raw_sm)
                except ValueError:
                    pass

        return False

    def parse_and_update(self, res_freq, res_mode, res_data=None):
        civ_prefix = b'\xFE\xFE' + bytes([CONTROLLER_ADDRESS, self.radio_address])
        if res_freq and len(res_freq) >= 11:
            idx = res_freq.find(civ_prefix + b'\x03')
            if idx != -1 and len(res_freq) >= idx + 11:
                data = res_freq[idx+5:idx+10]
                freq_str = data[::-1].hex()
                try:
                    freq_hz = int(freq_str)
                    self.current_freq_hz = freq_hz
                    freq_mhz = freq_hz / 1_000_000
                    self.lbl_current_freq.config(text=f"{freq_mhz:.6f} MHz")
                except ValueError:
                    pass

        if res_mode and len(res_mode) >= 8:
            idx = res_mode.find(civ_prefix + b'\x04')
            if idx != -1 and len(res_mode) >= idx + 7:
                mode_byte = res_mode[idx+5]
                mode_name = REVERSE_MODES.get(mode_byte, f"Onbekend ({hex(mode_byte)})")

                if res_data:
                    d_idx = res_data.find(civ_prefix + b'\x1A\x06')
                    if d_idx != -1 and len(res_data) >= d_idx + 8 and res_data[d_idx+6] == 0x01:
                        if mode_name in ["LSB", "USB", "AM", "FM"]:
                            mode_name += "-D"

                self.lbl_current_mode.config(text=f"Huidige modus: {mode_name}")

    def update_loop(self):
        while self.is_connected:
            is_tx = self.query_status()
            time.sleep(0.1 if is_tx else 1.0)


if __name__ == "__main__":
    root = tk.Tk()
    app = IC7300mk2ControlApp(root)
    root.mainloop()
