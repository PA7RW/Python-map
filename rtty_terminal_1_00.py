import tkinter as tk
from tkinter import ttk, messagebox
import numpy as np
import sounddevice as sd
import serial
import time
import threading
import json
import os
import sys
import re

def get_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

# 5-bits ITA2 / Baudot tabellen (LTRS en FIGS)
BAUDOT_LTRS = {
    0x00: "",   0x01: "E",  0x02: "\n", 0x03: "A",  0x04: " ",  0x05: "S",  0x06: "I",  0x07: "U",
    0x08: "\r", 0x09: "D",  0x0A: "R",  0x0B: "J",  0x0C: "N",  0x0D: "F",  0x0E: "C",  0x0F: "K",
    0x10: "T",  0x11: "Z",  0x12: "L",  0x13: "W",  0x14: "H",  0x15: "Y",  0x16: "P",  0x17: "Q",
    0x18: "O",  0x19: "B",  0x1A: "G",  0x1B: "<FIGS>", 0x1C: "M", 0x1D: "X", 0x1E: "V", 0x1F: "<LTRS>"
}

BAUDOT_FIGS = {
    0x00: "",   0x01: "3",  0x02: "\n", 0x03: "-",  0x04: " ",  0x05: "'",  0x06: "8",  0x07: "7",
    0x08: "\r", 0x09: "$",  0x0A: "4",  0x0B: "\a", 0x0C: ",",  0x0D: "!",  0x0E: ":",  0x0F: "(",
    0x10: "5",  0x11: "+",  0x12: ")",  0x13: "2",  0x14: "#",  0x15: "6",  0x16: "0",  0x17: "1",
    0x18: "9",  0x19: "?",  0x1A: "&",  0x1B: "<FIGS>", 0x1C: ".", 0x1D: "/",  0x1E: "=",  0x1F: "<LTRS>"
}

CHAR_TO_BAUDOT = {}
for code, ch in BAUDOT_LTRS.items():
    if ch and not ch.startswith("<"):
        CHAR_TO_BAUDOT[ch] = ("LTRS", code)
for code, ch in BAUDOT_FIGS.items():
    if ch and not ch.startswith("<") and ch != " ":
        CHAR_TO_BAUDOT[ch] = ("FIGS", code)


class RTTYTerminalWindow:
    def __init__(self, app):
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.title("PA7RW RTTY Decoder & N1MM-stijl Schrijver (45.45 Baud / 170 Hz)")
        self.win.geometry("740x710")
        self.win.protocol("WM_DELETE_WINDOW", self.on_close)

        self.is_decoding = False
        self.is_transmitting = False
        self.stop_tx_flag = False

        self.sample_rate = 16000
        self.baud_rate = 45.45
        self.mark_freq = 2125.0
        self.space_freq = 2295.0  # 170 Hz verschuiving
        self.detected_offset = 0

        self.contest_mode_var = tk.BooleanVar(value=False)
        self.serial_nr_var = tk.IntVar(value=1)
        self.dx_call_var = tk.StringVar(value="")
        self.uos_var = tk.BooleanVar(value=True)       # Unshift-On-Space
        self.reverse_var = tk.BooleanVar(value=False)  # Mark/Space omkeren
        self.fsk_actief_var = tk.BooleanVar(value=True)  # FSK zenden via COM3 DTR
        self.fsk_poort_var = tk.StringVar(value="COM3")  # Standaard port B IC-7300

        self.create_widgets()
        self.refresh_audio_devices()
        self.bind_function_keys()

    def create_widgets(self):
        # --- Bovenbalk: Audio RX & FSK/AFSK TX & Spot-On Afstemming ---
        top_frame = ttk.LabelFrame(self.win, text=" Audio RX / FSK TX & RTTY Spot-On Afstemming (2125 / 2295 Hz) ", padding=8)
        top_frame.pack(fill="x", padx=10, pady=5)

        row1 = ttk.Frame(top_frame)
        row1.pack(fill="x", pady=2)

        ttk.Label(row1, text="Audio RX:").pack(side="left")
        self.rx_audio_combo = ttk.Combobox(row1, width=22, state="readonly")
        self.rx_audio_combo.pack(side="left", padx=(4, 8))

        ttk.Label(row1, text="FSK Poort:").pack(side="left")
        self.fsk_port_entry = ttk.Entry(row1, width=7, textvariable=self.fsk_poort_var)
        self.fsk_port_entry.pack(side="left", padx=(3, 8))

        ttk.Label(row1, text="Audio TX:").pack(side="left")
        self.tx_audio_combo = ttk.Combobox(row1, width=22, state="readonly")
        self.tx_audio_combo.pack(side="left", padx=4)

        row1b = ttk.Frame(top_frame)
        row1b.pack(fill="x", pady=(4, 2))

        self.btn_decode = tk.Button(
            row1b, text="Start RTTY Decoder", bg="#28a745", fg="white",
            font=("Arial", 9, "bold"), command=self.toggle_decoder
        )
        self.btn_decode.pack(side="left", padx=(0, 8))

        ttk.Checkbutton(row1b, text="FSK via DTR", variable=self.fsk_actief_var).pack(side="left", padx=5)
        ttk.Checkbutton(row1b, text="Omkeren (Mark/Space)", variable=self.reverse_var).pack(side="left", padx=5)
        ttk.Checkbutton(row1b, text="UOS (Spatie -> Letters)", variable=self.uos_var).pack(side="left", padx=5)

        row2 = ttk.Frame(top_frame)
        row2.pack(fill="x", pady=(6, 2))

        self.lbl_pitch = ttk.Label(row2, text="Mark/Space: 2125 / 2295 Hz | Offset: 0 Hz", font=("Arial", 9, "bold"))
        self.lbl_pitch.pack(side="left")

        self.btn_spot_on = tk.Button(
            row2, text="SPOT ON! (0 Hz)", bg="#007bff", fg="white",
            font=("Arial", 10, "bold"), width=18, command=self.apply_spot_on
        )
        self.btn_spot_on.pack(side="right", padx=5)

        self.tune_canvas = tk.Canvas(top_frame, height=28, bg="#1e1e1e", highlightthickness=1)
        self.tune_canvas.pack(fill="x", pady=4)
        self.draw_tune_bar(0, 0.0, 0.0)

        # --- RX Decoder Scherm ---
        rx_frame = ttk.LabelFrame(self.win, text=" Ontvangen RTTY Tekst (RX - 1x klikken op callsign neemt hem over) ", padding=8)
        rx_frame.pack(fill="both", expand=True, padx=10, pady=5)

        rx_top = ttk.Frame(rx_frame)
        rx_top.pack(fill="x", pady=(0, 4))

        ttk.Label(rx_top, text="Squelch:").pack(side="left")
        self.squelch_var = tk.DoubleVar(value=0.015)
        ttk.Scale(rx_top, from_=0.002, to=0.15, variable=self.squelch_var, length=130).pack(side="left", padx=5)

        ttk.Button(rx_top, text="Wis RX scherm", command=lambda: self.txt_rx.delete("1.0", tk.END)).pack(side="right")

        self.txt_rx = tk.Text(
            rx_frame, height=9, bg="#101820", fg="#00ff66",
            font=("Consolas", 12, "bold"), wrap="word", cursor="hand2"
        )
        self.txt_rx.pack(fill="both", expand=True)
        self.txt_rx.tag_configure("rx_text", foreground="#00ff66")
        self.txt_rx.tag_configure("tx_text", foreground="#ff4444")
        self.txt_rx.tag_configure("picked_call", background="#005a9e", foreground="white")
        self.txt_rx.bind("<ButtonRelease-1>", self.on_rx_click)

        # --- N1MM-stijl Callsign, Contest-vinkje & Macro's ---
        tx_frame = ttk.LabelFrame(self.win, text=" RTTY Writer (N1MM-stijl F1-F8 & Free Speech AFSK TX) ", padding=8)
        tx_frame.pack(fill="x", padx=10, pady=5)

        top_tx_row = ttk.Frame(tx_frame)
        top_tx_row.pack(fill="x", pady=(0, 4))

        ttk.Button(top_tx_row, text="Macro's bewerken", command=self.edit_macros_dialog).pack(side="left")
        self.lbl_tx_status = ttk.Label(top_tx_row, text="Status: RX (Gereed)", font=("Arial", 9, "bold"), foreground="green")
        self.lbl_tx_status.pack(side="left", padx=15)

        tk.Button(
            top_tx_row, text="STOP TX!", bg="#dc3545", fg="white",
            font=("Arial", 9, "bold"), command=self.stop_rtty_tx
        ).pack(side="right")

        # Callsign & Contest-modus balk
        call_row = ttk.Frame(tx_frame)
        call_row.pack(fill="x", pady=4)

        ttk.Label(call_row, text="Tegenstation ({CALL}):", font=("Arial", 9, "bold")).pack(side="left")
        self.dx_call_entry = ttk.Entry(call_row, width=14, font=("Consolas", 11, "bold"), textvariable=self.dx_call_var)
        self.dx_call_entry.pack(side="left", padx=5)
        self.dx_call_var.trace_add("write", lambda *args: self.update_f1_button())
        ttk.Button(call_row, text="Wis", width=5, command=self.clear_callsign).pack(side="left", padx=(0, 12))

        self.chk_contest = ttk.Checkbutton(
            call_row,
            text="Contest-modus (Auto {NR})",
            variable=self.contest_mode_var,
            command=self.toggle_contest_state
        )
        self.chk_contest.pack(side="left", padx=4)

        ttk.Label(call_row, text="Volgnr:").pack(side="left", padx=(6, 2))
        self.nr_spin = ttk.Spinbox(call_row, from_=1, to=9999, width=5, textvariable=self.serial_nr_var, state="disabled")
        self.nr_spin.pack(side="left")

        # 8 N1MM F1-F8 Macro-knoppen
        self.macro_frame = ttk.Frame(tx_frame)
        self.macro_frame.pack(fill="x", pady=4)
        self.macros = self.load_macros()
        self.macro_buttons = []
        self.rebuild_macro_buttons()

        # Free Speech kader
        free_lbl = ttk.Label(tx_frame, text="Free Speech (typ vrije tekst + druk op Enter):", font=("Arial", 8, "italic"))
        free_lbl.pack(anchor="w", pady=(4, 0))

        send_row = ttk.Frame(tx_frame)
        send_row.pack(fill="x", pady=2)

        self.tx_entry = ttk.Entry(send_row, font=("Consolas", 11))
        self.tx_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.tx_entry.bind("<Return>", lambda e: self.send_from_entry())

        ttk.Button(send_row, text="Zend RTTY (Enter)", command=self.send_from_entry).pack(side="right")

    def bind_function_keys(self):
        for i in range(8):
            self.win.bind(f"<F{i+1}>", lambda e, idx=i: self.trigger_macro(idx, self.macros[idx][1]))

    def refresh_audio_devices(self):
        devices = sd.query_devices()
        in_devs, out_devs = [], []
        usb_in, usb_out = None, None

        for idx, d in enumerate(devices):
            # Beperk tot MME host-API (voorkomt 4x dubbele apparaten via DirectSound/WASAPI/WDM-KS)
            if d.get("hostapi") != 0:
                continue

            naam = d["name"]
            naam_laag = naam.lower()

            # Filter virtuele Windows mappers en beeldschermluidsprekers weg
            if any(ign in naam_laag for ign in ["geluidstoewijzing", "primair", "primary", "display audio", "hd audio driver"]):
                continue

            label = f"{idx}: {naam}"

            if d["max_input_channels"] > 0:
                in_devs.append(label)
                # Geef voorrang aan de USB-geluidskaart van de transceiver (USB Audio Device)
                if "USB AUDIO DEVICE" in naam.upper():
                    usb_in = label
                elif "USB" in naam.upper() and usb_in is None:
                    usb_in = label

            if d["max_output_channels"] > 0:
                out_devs.append(label)
                if "USB AUDIO DEVICE" in naam.upper():
                    usb_out = label
                elif "USB" in naam.upper() and usb_out is None:
                    usb_out = label

        self.rx_audio_combo["values"] = in_devs
        self.tx_audio_combo["values"] = out_devs
        if usb_in:
            self.rx_audio_combo.set(usb_in)
        elif in_devs:
            self.rx_audio_combo.set(in_devs[0])

        if usb_out:
            self.tx_audio_combo.set(usb_out)
        elif out_devs:
            self.tx_audio_combo.set(out_devs[0])

    # --- N1MM Callsign Klik & Macro's ---
    def on_rx_click(self, event):
        try:
            index = self.txt_rx.index(f"@{event.x},{event.y}")
            line_text = self.txt_rx.get(f"{index} linestart", f"{index} lineend").upper()
            col = int(index.rpartition(".")[2])
            clicked_word = ""
            word_start, word_end = index, index
            for match in re.finditer(r"[A-Z0-9/]{3,}", line_text):
                if match.start() <= col <= match.end():
                    clicked_word = match.group(0)
                    word_start = f"{index.split('.')[0]}.{match.start()}"
                    word_end = f"{index.split('.')[0]}.{match.end()}"
                    break

            if len(clicked_word) >= 3 and any(c.isdigit() for c in clicked_word):
                self.dx_call_var.set(clicked_word)
                self.txt_rx.tag_remove("picked_call", "1.0", tk.END)
                self.txt_rx.tag_add("picked_call", word_start, word_end)
        except Exception:
            pass
    def clear_callsign(self):
        self.dx_call_var.set("")
        self.txt_rx.tag_remove("picked_call", "1.0", tk.END)

    def toggle_contest_state(self):
        self.nr_spin.config(state="normal" if self.contest_mode_var.get() else "disabled")

    def load_macros(self):
        default_macros = [
            ["F1 CQ", "\r\nCQ CQ DE PA7RW PA7RW K\r\n"],
            ["F2 Exch", "\r\n{CALL} 599 {NR} {NR} BK\r\n"],
            ["F3 TU", "\r\n{CALL} TU DE PA7RW QRZ?\r\n"],
            ["F4 MyCall", "DE PA7RW PA7RW "],
            ["F5 HisCall", "{CALL} "],
            ["F6 QTH/Naam", "\r\n{CALL} DE PA7RW NAME ROB QTH HEERHUGOWAARD HW? BK\r\n"],
            ["F7 AGN?", "\r\nAGN? AGN? K\r\n"],
            ["F8 73 SK", "\r\n{CALL} DE PA7RW TNX FER RTTY QSO 73 SK\r\n"]
        ]
        macro_path = os.path.join(get_base_dir(), "rtty_macros.json")
        if os.path.exists(macro_path):
            try:
                with open(macro_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return default_macros

    def save_macros(self):
        macro_path = os.path.join(get_base_dir(), "rtty_macros.json")
        with open(macro_path, "w", encoding="utf-8") as f:
            json.dump(self.macros, f, indent=2)

    def rebuild_macro_buttons(self):
        for btn in self.macro_buttons:
            btn.destroy()
        self.macro_buttons.clear()
        for idx, (label, text) in enumerate(self.macros):
            rij = idx // 4
            kolom = idx % 4
            btn = ttk.Button(
                self.macro_frame,
                text=label,
                command=lambda i=idx, t=text: self.trigger_macro(i, t)
            )
            btn.grid(row=rij, column=kolom, padx=2, pady=2, sticky="ew")
            self.macro_frame.columnconfigure(kolom, weight=1)
            self.macro_buttons.append(btn)
        self.update_f1_button()

    def update_f1_button(self):
        if not hasattr(self, "macro_buttons") or not self.macro_buttons:
            return
        call = self.dx_call_var.get().strip().upper()
        if call:
            self.macro_buttons[0].config(text="Him de Me")
        else:
            orig_lbl = self.macros[0][0] if self.macros else "F1 CQ"
            self.macro_buttons[0].config(text=orig_lbl)

    def edit_macros_dialog(self):
        dlg = tk.Toplevel(self.win)
        dlg.title("RTTY Macro's Bewerken (F1-F8)")
        dlg.geometry("540x360")
        entries = []
        for idx, (lbl, txt) in enumerate(self.macros):
            row = ttk.Frame(dlg)
            row.pack(fill="x", padx=10, pady=3)
            e_lbl = ttk.Entry(row, width=14)
            e_lbl.insert(0, lbl)
            e_lbl.pack(side="left", padx=(0, 5))
            e_txt = ttk.Entry(row)
            e_txt.insert(0, txt.replace("\r", "").replace("\n", "\\n"))
            e_txt.pack(side="left", fill="x", expand=True)
            entries.append((e_lbl, e_txt))

        def save_and_close():
                self.macros = [[l.get(), t.get().replace("\\n", "\r\n")] for l, t in entries]
                self.save_macros()
                self.rebuild_macro_buttons()
                dlg.destroy()

        ttk.Button(dlg, text="Opslaan", command=save_and_close).pack(pady=10)

    def trigger_macro(self, idx, template):
        call = self.dx_call_var.get().strip().upper()

        # Dynamische F1: als er een tegenstation is ingevuld -> "Him de Me"
        if idx == 0 and call:
            msg = f"\r\n{call} DE PA7RW K\r\n"
            self.send_rtty_text(msg)
            return

        if self.contest_mode_var.get():
            # In contest-modus: vul volgnummer in en hoog volgnummer op
            nr_str = f"{int(self.serial_nr_var.get()):03d}"
            msg = template.replace("{CALL}", call).replace("{NR}", nr_str)
            if "{NR}" in template:
                self.serial_nr_var.set(int(self.serial_nr_var.get()) + 1)
        else:
            # Buiten contest-modus: haal {NR} weg en ruim dubbele spaties netjes op
            msg = template.replace("{CALL}", call).replace("{NR}", "")
            regels = []
            for regel in msg.splitlines(keepends=True):
                eind = "\r\n" if regel.endswith("\r\n") else ("\n" if regel.endswith("\n") else "")
                inhoud = regel.rstrip("\r\n")
                opgeschoond = re.sub(r' {2,}', ' ', inhoud).strip(' ')
                regels.append(opgeschoond + eind if inhoud else eind)
            msg = "".join(regels)

        self.send_rtty_text(msg)

    def send_from_entry(self):
        txt = self.tx_entry.get().strip()
        if txt:
            self.send_rtty_text(txt + " ")
            self.tx_entry.delete(0, tk.END)

    # --- RTTY AFSK Encoder (TX) + IC-7300 PTT ---
    def text_to_baudot_bits(self, text):
        bits = []
        for _ in range(3):
            bits.extend(self._char_bits(0x1F))
        mode = "LTRS"
        for ch in text.upper():
            if ch == "\r":
                bits.extend(self._char_bits(0x08))
            elif ch == "\n":
                bits.extend(self._char_bits(0x02))
            elif ch == " ":
                bits.extend(self._char_bits(0x04))
                if self.uos_var.get():
                    mode = "LTRS"
            elif ch in CHAR_TO_BAUDOT:
                req_mode, code = CHAR_TO_BAUDOT[ch]
                if req_mode != mode:
                    bits.extend(self._char_bits(0x1B if req_mode == "FIGS" else 0x1F))
                    mode = req_mode
                bits.extend(self._char_bits(code))
        bits.extend(self._char_bits(0x1F))
        return bits

    def _char_bits(self, code):
        seq = [(0, 1.0)]
        for b in range(5):
            seq.append(((code >> b) & 1, 1.0))
        seq.append((1, 1.5))
        return seq

    def send_rtty_text(self, text):
        if self.is_transmitting:
            return
        if not self.app.is_connected:
            messagebox.showwarning("Niet verbonden", "Maak eerst verbinding met de IC-7300MK2 (COM4) in het hoofdscherm.")
            return
        self.append_tx_text(text)
        if self.fsk_actief_var.get():
            com_poort = self.fsk_poort_var.get().strip().upper()
            if not com_poort:
                messagebox.showerror("Poortfout", "Geen FSK COM-poort ingevuld (bijv. COM3).")
                return
            threading.Thread(target=self._tx_fsk_worker, args=(text, com_poort), daemon=True).start()
        else:
            if not self.tx_audio_combo.get():
                messagebox.showerror("Audiofout", "Geen TX geluidskaart geselecteerd.")
                return
            out_dev = int(self.tx_audio_combo.get().split(":")[0])
            threading.Thread(target=self._tx_worker, args=(text, out_dev), daemon=True).start()

    def _tx_fsk_worker(self, text, com_poort):
        self.is_transmitting = True
        self.stop_tx_flag = False
        self.win.after(0, lambda: self.lbl_tx_status.config(text=f"Status: FSK ZENDEN ({com_poort} DTR)...", foreground="red"))

        fsk_ser = None
        winmm = None
        try:
            # Schakel Windows multimedia timer in op 1 ms resolutie voor strakke timing
            try:
                import ctypes
                winmm = ctypes.WinDLL('winmm')
                winmm.timeBeginPeriod(1)
            except Exception:
                winmm = None

            fsk_ser = serial.Serial(com_poort, baudrate=9600, timeout=1, rtscts=False, dsrdtr=False)

            # Bepaal DTR toestand:
            # Normaal (IC-7300 Normal): Mark = False (hoog/open), Space = True (laag/gesloten)
            # Omgekeerd: Mark = True, Space = False
            is_omgekeerd = self.reverse_var.get()
            mark_pin = True if is_omgekeerd else False
            space_pin = False if is_omgekeerd else True

            # Zet de FSK lijn in rust direct op Mark (uitsluitend DTR sleuteling)
            fsk_ser.dtr = mark_pin

            # Schakel PTT in via CI-V op de transceiver
            if self.app.is_connected:
                self.app.send_cmd(b'\x1C\x00', b'\x01')

            # Wacht 60 ms zodat de zender stabiel RF-vermogen levert (TX pre-amble)
            time.sleep(0.06)

            seq = self.text_to_baudot_bits(text)
            bit_duur = 1.0 / self.baud_rate  # 45.45 baud = ~0.0220022 s

            start_tijd = time.perf_counter()
            for bit_waarde, dur_mult in seq:
                if self.stop_tx_flag:
                    break

                # Stel DTR-pin in: 1 = Mark, 0 = Space
                lijn_status = mark_pin if bit_waarde == 1 else space_pin
                fsk_ser.dtr = lijn_status

                target_duur = bit_duur * dur_mult
                eind_tijd = start_tijd + target_duur

                # Precieze timerlus met minimale CPU-belasting
                while time.perf_counter() < eind_tijd:
                    resterend = eind_tijd - time.perf_counter()
                    if resterend > 0.003:
                        time.sleep(0.001)

                start_tijd = eind_tijd

            # Na afloop van de tekst: zendlijn netjes op Mark en korte post-amble
            fsk_ser.dtr = mark_pin
            time.sleep(0.05)

        except Exception as e:
            self.win.after(0, lambda: messagebox.showerror("FSK TX Fout", f"Fout bij zenden op {com_poort}:\n{str(e)}"))
        finally:
            # Schakel PTT uit via CI-V
            if self.app.is_connected:
                self.app.send_cmd(b'\x1C\x00', b'\x00')
            if fsk_ser and fsk_ser.is_open:
                try:
                    fsk_ser.dtr = False
                    fsk_ser.close()
                except Exception:
                    pass
            if winmm:
                try:
                    winmm.timeEndPeriod(1)
                except Exception:
                    pass
            self.is_transmitting = False
            self.win.after(0, lambda: self.lbl_tx_status.config(text="Status: RX (Gereed)", foreground="green"))
            if winmm:
                try:
                    winmm.timeEndPeriod(1)
                except Exception:
                    pass
            self.is_transmitting = False
            self.win.after(0, lambda: self.lbl_tx_status.config(text="Status: RX (Gereed)", foreground="green"))

    def _tx_worker(self, text, out_dev):
        self.is_transmitting = True
        self.stop_tx_flag = False
        self.win.after(0, lambda: self.lbl_tx_status.config(text="Status: ZENDEN (TX)...", foreground="red"))

        try:
            fs = 48000
            bit_len = 1.0 / self.baud_rate
            seq = self.text_to_baudot_bits(text)

            m_freq = self.space_freq if self.reverse_var.get() else self.mark_freq
            s_freq = self.mark_freq if self.reverse_var.get() else self.space_freq

            chunks = []
            phase = 0.0
            for bit_val, dur_mult in seq:
                n_samples = int(round(fs * bit_len * dur_mult))
                freq = m_freq if bit_val == 1 else s_freq
                dphi = 2.0 * np.pi * freq / fs
                phases = phase + dphi * np.arange(1, n_samples + 1, dtype=np.float32)
                phase = float(phases[-1] % (2.0 * np.pi))
                chunks.append(0.45 * np.sin(phases))

            audio_wave = np.concatenate(chunks).astype(np.float32)

            if self.app.is_connected:
                self.app.send_cmd(b'\x1C\x00', b'\x01')

            with sd.OutputStream(device=out_dev, channels=1, samplerate=fs, blocksize=1024) as stream:
                for i in range(0, len(audio_wave), 1024):
                    if self.stop_tx_flag:
                        break
                    stream.write(audio_wave[i:i+1024].reshape(-1, 1))

        except Exception as e:
            self.win.after(0, lambda: messagebox.showerror("TX Fout", str(e)))
        finally:
            if self.app.is_connected:
                self.app.send_cmd(b'\x1C\x00', b'\x00')
            self.is_transmitting = False
            self.win.after(0, lambda: self.lbl_tx_status.config(text="Status: RX (Gereed)", foreground="green"))

    def stop_rtty_tx(self):
        self.stop_tx_flag = True
        if self.app.is_connected:
            self.app.send_cmd(b'\x1C\x00', b'\x00')

    # --- Spot-On & Tuning Balk ---
    def draw_tune_bar(self, offset_hz, mark_lvl, space_lvl):
        self.tune_canvas.delete("all")
        w = self.tune_canvas.winfo_width() or 700
        mid = w // 2
        px_per_hz = (mid - 30) / 250.0
        m_ref = mid - int(85 * px_per_hz)
        s_ref = mid + int(85 * px_per_hz)
        self.tune_canvas.create_line(m_ref, 0, m_ref, 28, fill="#00ff66", width=2)
        self.tune_canvas.create_line(s_ref, 0, s_ref, 28, fill="#00ccff", width=2)

        clamped = max(-200, min(200, offset_hz))
        shift_px = int(clamped * px_per_hz)
        kleur = "#00ff66" if abs(offset_hz) <= 15 else ("orange" if abs(offset_hz) <= 50 else "#ff4444")

        if mark_lvl > 0.005 or space_lvl > 0.005:
            self.tune_canvas.create_oval(m_ref + shift_px - 7, 6, m_ref + shift_px + 7, 22, fill=kleur, outline="white")
            self.tune_canvas.create_oval(s_ref + shift_px - 7, 6, s_ref + shift_px + 7, 22, fill=kleur, outline="white")

    def apply_spot_on(self):
        if not self.app.is_connected:
            return
        huidige_modus = self.app.lbl_current_mode.cget("text")
        if "USB" in huidige_modus or "RTTY-R" in huidige_modus:
            nieuwe_freq = self.app.current_freq_hz + self.detected_offset
        else:
            nieuwe_freq = self.app.current_freq_hz - self.detected_offset
        self.app.freq_entry.delete(0, tk.END)
        self.app.freq_entry.insert(0, str(int(nieuwe_freq)))
        self.app.set_frequency()
        self.detected_offset = 0
        self.draw_tune_bar(0, 0.0, 0.0)

    # --- RTTY 45.45 Baud RX Decoder ---
    def toggle_decoder(self):
        if not self.is_decoding:
            if not self.rx_audio_combo.get():
                messagebox.showerror("Audiofout", "Geen RX geluidsingang gevonden.")
                return
            dev_id = int(self.rx_audio_combo.get().split(":")[0])
            self.is_decoding = True
            self.btn_decode.config(text="Stop RTTY Decoder", bg="#dc3545")
            threading.Thread(target=self.decode_worker, args=(dev_id,), daemon=True).start()
        else:
            self.is_decoding = False
            self.btn_decode.config(text="Start RTTY Decoder", bg="#28a745")

    def decode_worker(self, dev_id):
        fs = self.sample_rate
        bit_samples = fs / self.baud_rate
        block_size = 64

        state = "IDLE"
        sample_counter = 0.0
        bit_idx = 0
        byte_val = 0
        shift_mode = "LTRS"
        ui_counter = 0

        ring_buf = np.zeros(2048, dtype=np.float32)
        freqs = np.fft.rfftfreq(2048, d=1.0 / fs)
        band_mask = (freqs >= 1900) & (freqs <= 2550)
        band_freqs = freqs[band_mask]

        try:
            t_vec = np.arange(block_size, dtype=np.float32) / fs
            cos_m = np.cos(2.0 * np.pi * self.mark_freq * t_vec)
            sin_m = np.sin(2.0 * np.pi * self.mark_freq * t_vec)
            cos_s = np.cos(2.0 * np.pi * self.space_freq * t_vec)
            sin_s = np.sin(2.0 * np.pi * self.space_freq * t_vec)

            with sd.InputStream(device=dev_id, channels=1, samplerate=fs, blocksize=block_size) as stream:
                while self.is_decoding:
                    data, _ = stream.read(block_size)
                    samples = data[:, 0]

                    m_mag = np.hypot(float(np.dot(samples, cos_m)), float(np.dot(samples, sin_m))) / (block_size * 0.5)
                    s_mag = np.hypot(float(np.dot(samples, cos_s)), float(np.dot(samples, sin_s))) / (block_size * 0.5)

                    if self.reverse_var.get():
                        m_mag, s_mag = s_mag, m_mag

                    total_sig = max(m_mag, s_mag)
                    is_mark = m_mag >= s_mag
                    squelch_ok = total_sig >= float(self.squelch_var.get())

                    ring_buf = np.roll(ring_buf, -block_size)
                    ring_buf[-block_size:] = samples
                    ui_counter += 1
                    if ui_counter >= 20:
                        ui_counter = 0
                        if squelch_ok:
                            fft = np.abs(np.fft.rfft(ring_buf * np.hanning(2048)))
                            b_fft = fft[band_mask]
                            p_freq = float(band_freqs[int(np.argmax(b_fft))])
                            off_m = p_freq - self.mark_freq
                            off_s = p_freq - self.space_freq
                            offset = int(round(off_m if abs(off_m) < abs(off_s) else off_s))
                            self.detected_offset = offset
                            self.win.after(0, self.update_tune_ui, offset, m_mag, s_mag)
                        else:
                            self.win.after(0, self.draw_tune_bar, 0, 0.0, 0.0)

                    if not squelch_ok:
                        state = "IDLE"
                        continue

                    if state == "IDLE":
                        if not is_mark:
                            state = "START"
                            sample_counter = block_size
                    elif state == "START":
                        sample_counter += block_size
                        if sample_counter >= bit_samples * 0.5:
                            if not is_mark:
                                state = "DATA"
                                sample_counter = 0.0
                                bit_idx = 0
                                byte_val = 0
                            else:
                                state = "IDLE"
                    elif state == "DATA":
                        sample_counter += block_size
                        if sample_counter >= bit_samples:
                            sample_counter -= bit_samples
                            if is_mark:
                                byte_val |= (1 << bit_idx)
                            bit_idx += 1
                            if bit_idx >= 5:
                                state = "STOP"
                    elif state == "STOP":
                        sample_counter += block_size
                        if sample_counter >= bit_samples * 0.8:
                            if is_mark:
                                if byte_val == 0x1B:
                                    shift_mode = "FIGS"
                                elif byte_val == 0x1F:
                                    shift_mode = "LTRS"
                                else:
                                    table = BAUDOT_FIGS if shift_mode == "FIGS" else BAUDOT_LTRS
                                    ch = table.get(byte_val, "")
                                    if ch == " " and self.uos_var.get():
                                        shift_mode = "LTRS"
                                    if ch and not ch.startswith("<"):
                                        self.win.after(0, self.append_rx_text, ch)
                            state = "IDLE"

        except Exception as e:
            self.is_decoding = False
            self.win.after(0, lambda: messagebox.showerror("RTTY Audiofout", str(e)))

    def update_tune_ui(self, offset, m_lvl, s_lvl):
        self.lbl_pitch.config(text=f"Mark/Space: 2125 / 2295 Hz | Offset: {offset:+d} Hz")
        self.btn_spot_on.config(text=f"SPOT ON! ({offset:+d} Hz)")
        self.draw_tune_bar(offset, m_lvl, s_lvl)

    def append_rx_text(self, ch):
        if self.win.winfo_exists():
            self.txt_rx.insert(tk.END, ch, "rx_text")
            self.txt_rx.see(tk.END)

    def append_tx_text(self, text):
        if self.win.winfo_exists():
            huidige_tekst = self.txt_rx.get("1.0", tk.END)
            prefix = "\n" if huidige_tekst.strip() and not huidige_tekst.endswith("\n") else ""
            weergave_tekst = text.strip("\r\n")
            if weergave_tekst:
                self.txt_rx.insert(tk.END, f"{prefix}{weergave_tekst}\n", "tx_text")
                self.txt_rx.see(tk.END)

    def on_close(self):
        self.is_decoding = False
        self.stop_rtty_tx()
        self.win.destroy()
