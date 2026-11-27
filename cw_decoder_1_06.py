import tkinter as tk
from tkinter import ttk, messagebox
import numpy as np
import sounddevice as sd
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

MORSE_TO_CHAR = {
    ".-": "A", "-...": "B", "-.-.": "C", "-..": "D", ".": "E",
    "..-.": "F", "--.": "G", "....": "H", "..": "I", ".---": "J",
    "-.-": "K", ".-..": "L", "--": "M", "-.": "N", "---": "O",
    ".--.": "P", "--.-": "Q", ".-.": "R", "...": "S", "-": "T",
    "..-": "U", "...-": "V", ".--": "W", "-..-": "X", "-.--": "Y",
    "--..": "Z", "-----": "0", ".----": "1", "..---": "2", "...--": "3",
    "....-": "4", ".....": "5", "-....": "6", "--...": "7", "---..": "8",
    "----.": "9", ".-.-.-": ".", "--..--": ",", "..--..": "?", "-..-.": "/",
    "-...-": "=", ".-.-.": "+", "...-.-": "<SK>", "-.-.-": "<KA>"
}


class CWTerminalWindow:
    def __init__(self, app):
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.title("PA7RW CW Decoder & N1MM-Style Writer")
        self.win.geometry("720x660")
        self.win.protocol("WM_DELETE_WINDOW", self.on_close)

        self.is_decoding = False
        self.sample_rate = 16000
        self.target_pitch = 600
        self.detected_pitch = 600
        self.detected_offset = 0
        self.dot_len = 0.060

        self.contest_mode_var = tk.BooleanVar(value=False)
        self.serial_nr_var = tk.IntVar(value=1)
        self.dx_call_var = tk.StringVar(value="")

        self.create_widgets()
        self.refresh_audio_devices()
        self.read_radio_cw_settings()

    def create_widgets(self):
        # --- Bovenbalk: Audio & Spot-On Afstemming ---
        top_frame = ttk.LabelFrame(self.win, text=" Audio Ingang & Spot-On Afstemming ", padding=8)
        top_frame.pack(fill="x", padx=10, pady=5)

        row1 = ttk.Frame(top_frame)
        row1.pack(fill="x", pady=2)

        ttk.Label(row1, text="USB Audio (RX):").pack(side="left")
        self.audio_combo = ttk.Combobox(row1, width=38, state="readonly")
        self.audio_combo.pack(side="left", padx=5)

        self.btn_decode = tk.Button(
            row1, text="Start Decoder", bg="#28a745", fg="white",
            font=("Arial", 9, "bold"), command=self.toggle_decoder
        )
        self.btn_decode.pack(side="left", padx=5)

        row2 = ttk.Frame(top_frame)
        row2.pack(fill="x", pady=(6, 2))

        self.lbl_pitch = ttk.Label(row2, text="Radio Pitch: 600 Hz | Signaal: -- Hz", font=("Arial", 9, "bold"))
        self.lbl_pitch.pack(side="left")

        self.btn_spot_on = tk.Button(
            row2, text="SPOT ON! (0 Hz)", bg="#007bff", fg="white",
            font=("Arial", 10, "bold"), width=18, command=self.apply_spot_on
        )
        self.btn_spot_on.pack(side="right", padx=5)

        self.tune_canvas = tk.Canvas(top_frame, height=24, bg="#1e1e1e", highlightthickness=1)
        self.tune_canvas.pack(fill="x", pady=4)
        self.draw_tune_bar(0)

        # --- RX Decoder Scherm (1x klikken op Callsign neemt hem over) ---
        rx_frame = ttk.LabelFrame(self.win, text=" Ontvangen CW Tekst (RX - 1x klikken op callsign neemt hem over) ", padding=8)
        rx_frame.pack(fill="both", expand=True, padx=10, pady=5)

        rx_top = ttk.Frame(rx_frame)
        rx_top.pack(fill="x", pady=(0, 4))
        self.lbl_rx_wpm = ttk.Label(rx_top, text="RX Snelheid: ~20 WPM", font=("Arial", 9, "italic"))
        self.lbl_rx_wpm.pack(side="left")
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
        tx_frame = ttk.LabelFrame(self.win, text=" CW Writer (N1MM-stijl F1-F8 & Free Speech) ", padding=8)
        tx_frame.pack(fill="x", padx=10, pady=5)

        wpm_row = ttk.Frame(tx_frame)
        wpm_row.pack(fill="x", pady=(0, 4))

        ttk.Label(wpm_row, text="TX Snelheid (WPM):").pack(side="left")
        self.wpm_var = tk.IntVar(value=20)
        self.wpm_spin = ttk.Spinbox(wpm_row, from_=6, to=48, width=5, textvariable=self.wpm_var, command=self.set_tx_wpm)
        self.wpm_spin.pack(side="left", padx=5)

        ttk.Button(wpm_row, text="Macro's bewerken", command=self.edit_macros_dialog).pack(side="left", padx=8)
        tk.Button(wpm_row, text="STOP TX!", bg="#dc3545", fg="white", font=("Arial", 9, "bold"), command=self.stop_cw_tx).pack(side="right")

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

        ttk.Button(send_row, text="Zend Free Speech (Enter)", command=self.send_from_entry).pack(side="right")
             # --- N1MM Callsign Klik & Contest Nummer Logica ---
    def on_rx_click(self, event):
        try:
            index = self.txt_rx.index(f"@{event.x},{event.y}")
            word_start = self.txt_rx.index(f"{index} wordstart")
            word_end = self.txt_rx.index(f"{index} wordend")
            clicked_word = self.txt_rx.get(word_start, word_end).strip().upper()
            # Ondersteun ook samengestelde calls met '/' zoals PA7RW/P of DL/PA7RW
            line_text = self.txt_rx.get(f"{index} linestart", f"{index} lineend").upper()
            col = int(index.rpartition(".")[2])
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
        if self.contest_mode_var.get():
            self.nr_spin.config(state="normal")
        else:
            self.nr_spin.config(state="disabled")

    def load_macros(self):
        default_macros = [
            ["F1 CQ", "CQ CQ DE PA7RW PA7RW K "],
            ["F2 Exch", "{CALL} 599 {NR} BK "],
            ["F3 TU", "TU DE PA7RW QRZ? "],
            ["F4 MyCall", "PA7RW "],
            ["F5 HisCall", "{CALL} "],
            ["F6 QTH/Naam", "{CALL} DE PA7RW NAME ROB QTH HEERHUGOWAARD HW? BK "],
            ["F7 AGN?", "AGN? "],
            ["F8 73 SK", "{CALL} DE PA7RW TNX FER QSO 73 SK "],
            ["F9 leeg", "empty "],
            ["F10 leeg", "empty "],
            ["F11 leeg", "empty "],
            ["F12 leeg", "empty "],
        ]
             
        macro_path = os.path.join(get_base_dir(), "cw_macros.json")
        if os.path.exists(macro_path):
            try:
                with open(macro_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return default_macros

    def save_macros(self):
        macro_path = os.path.join(get_base_dir(), "cw_macros.json")
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
            self.win.bind(f"<F{idx+1}>", lambda e, i=idx, t=text: self.trigger_macro(i, t))
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

    def trigger_macro(self, idx, template_text):
        dx_call = self.dx_call_var.get().strip().upper()

        # Dynamische F1: als er een tegenstation is ingevuld -> "Him de Me"
        if idx == 0 and dx_call:
            self.send_cw_text(f"{dx_call} DE PA7RW K ")
            return

        final_text = template_text.replace("{CALL}", dx_call)

        if self.contest_mode_var.get():
            nr_str = f"{int(self.serial_nr_var.get()):03d}"
            final_text = final_text.replace("{NR}", nr_str)
            # Bij F3 (TU / QSO afronden) automatisch het contestnummer +1 ophogen en call wissen
            if idx == 2:
                self.serial_nr_var.set(int(self.serial_nr_var.get()) + 1)
                self.clear_callsign()
        else:
            # Buiten contestmodus wordt {NR} weggelaten (bijv. "{CALL} 599 {NR} BK" -> "{CALL} 599 BK")
            final_text = final_text.replace("{NR}", "")

        clean_send = " ".join(final_text.split()) + " "
        self.send_cw_text(clean_send)

    def edit_macros_dialog(self):
        dlg = tk.Toplevel(self.win)
        dlg.title("CW Macro's Bewerken (F1 - F8)")
        dlg.geometry("640x390")
        entries = []

        ttk.Label(
            dlg,
            text="Gebruik {CALL} voor tegenstation en {NR} voor contest-volgnummer (alleen actief als Contest-vinkje aanstaat).",
            font=("Arial", 9, "italic")
        ).pack(pady=6)

        grid_frame = ttk.Frame(dlg, padding=10)
        grid_frame.pack(fill="both", expand=True)

        for i, (lbl, txt) in enumerate(self.macros):
            e_lbl = ttk.Entry(grid_frame, width=15)
            e_lbl.insert(0, lbl)
            e_lbl.grid(row=i, column=0, padx=4, pady=3)

            e_txt = ttk.Entry(grid_frame, width=58)
            e_txt.insert(0, txt)
            e_txt.grid(row=i, column=1, padx=4, pady=3, sticky="ew")
            grid_frame.columnconfigure(1, weight=1)
            entries.append((e_lbl, e_txt))

        def save_and_close():
            self.macros = [[e_l.get().strip() or f"F{i+1}", e_t.get()] for i, (e_l, e_t) in enumerate(entries)]
            self.save_macros()
            self.rebuild_macro_buttons()
            dlg.destroy()

        ttk.Button(dlg, text="Opslaan & Sluiten", command=save_and_close).pack(pady=8)

    # --- Audio & Transceiver Communicatie ---
    def refresh_audio_devices(self):
        devices = sd.query_devices()
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

    def read_radio_cw_settings(self):
        if not self.app.is_connected:
            return
        res_pitch = self.app.send_cmd(b'\x14\x09')
        if res_pitch:
            idx = res_pitch.find(b'\xFE\xFE\xE0\xB6\x14\x09')
            if idx != -1 and len(res_pitch) >= idx + 9:
                raw = int(res_pitch[idx+6:idx+8].hex())
                self.target_pitch = round(300 + (raw / 255.0) * 600)

        res_spd = self.app.send_cmd(b'\x14\x0C')
        if res_spd:
            idx = res_spd.find(b'\xFE\xFE\xE0\xB6\x14\x0C')
            if idx != -1 and len(res_spd) >= idx + 9:
                raw = int(res_spd[idx+6:idx+8].hex())
                wpm = round(6 + (raw / 255.0) * 42)
                self.wpm_var.set(wpm)

    def set_tx_wpm(self):
        if not self.app.is_connected:
            return
        wpm = max(6, min(48, int(self.wpm_var.get())))
        raw = round(((wpm - 6) / 42.0) * 255)
        bcd = f"{raw:04d}"
        self.app.send_cmd(b'\x14\x0C', bytes([int(bcd[0:2], 16), int(bcd[2:4], 16)]))

    def send_from_entry(self):
        txt = self.tx_entry.get().strip()
        if txt:
            self.send_cw_text(txt + " ")
            self.tx_entry.delete(0, tk.END)

    def send_cw_text(self, text):
        if not self.app.is_connected:
            messagebox.showwarning("Niet verbonden", "Maak eerst verbinding op het hoofdvenster.")
            return
        clean_txt = text.upper()
        self.append_tx_text(clean_txt)
        for i in range(0, len(clean_txt), 30):
            chunk = clean_txt[i:i+30].encode("ascii", errors="ignore")
            self.app.send_cmd(b'\x17', chunk)
            time.sleep(0.05)

    def stop_cw_tx(self):
        if self.app.is_connected:
            self.app.send_cmd(b'\x17', b'\xFF')

    def draw_tune_bar(self, offset_hz):
        self.tune_canvas.delete("all")
        w = self.tune_canvas.winfo_width() or 680
        mid = w // 2
        self.tune_canvas.create_line(mid, 0, mid, 24, fill="#00ff66", width=2)
        clamped = max(-250, min(250, offset_hz))
        pos_x = mid + int((clamped / 250.0) * (mid - 20))
        kleur = "#00ff66" if abs(offset_hz) <= 15 else ("orange" if abs(offset_hz) <= 60 else "#ff4444")
        self.tune_canvas.create_oval(pos_x - 8, 4, pos_x + 8, 20, fill=kleur, outline="white")

    def apply_spot_on(self):
        if not self.app.is_connected:
            return
        huidige_modus = self.app.lbl_current_mode.cget("text")
        if "CW-R" in huidige_modus:
            nieuwe_freq = self.app.current_freq_hz - self.detected_offset
        else:
            nieuwe_freq = self.app.current_freq_hz + self.detected_offset
        self.app.freq_entry.delete(0, tk.END)
        self.app.freq_entry.insert(0, str(int(nieuwe_freq)))
        self.app.set_frequency()
        self.detected_offset = 0
        self.draw_tune_bar(0)

    def toggle_decoder(self):
        if not self.is_decoding:
            if not self.audio_combo.get():
                messagebox.showerror("Audiofout", "Geen geluidsingang gevonden.")
                return
            dev_id = int(next(iter(self.audio_combo.get().split(":"))))
            self.is_decoding = True
            self.btn_decode.config(text="Stop Decoder", bg="#dc3545")
            threading.Thread(target=self.decode_worker, args=(dev_id,), daemon=True).start()
        else:
            self.is_decoding = False
            self.btn_decode.config(text="Start Decoder", bg="#28a745")

    def decode_worker(self, dev_id):
        block_size = 256  # ~5.3 ms bij 48 kHz
        tone_on = False
        state_dur = 0.0
        morse_buf = ""
        space_added = True
        noise_env = 0.002
        sig_env = 0.03

        try:
            fs = float(self.sample_rate)
            block_dur = block_size / fs
            window = np.hanning(block_size).astype(np.float32)
            t_vec = np.arange(block_size, dtype=np.float32) / fs

            # Voor FFT pitch-indicatie (UI-balk)
            freqs = np.fft.rfftfreq(1024, d=1.0 / fs)
            pitch_mask = (freqs >= self.target_pitch - 140) & (freqs <= self.target_pitch + 140)
            pitch_freqs = freqs[pitch_mask]
            ring_buf = np.zeros(1024, dtype=np.float32)

            with sd.InputStream(device=dev_id, channels=1, samplerate=self.sample_rate, blocksize=block_size) as stream:
                while self.is_decoding:
                    data, _ = stream.read(block_size)
                    samples = data[:, 0]
                    win_samples = samples * window

                    # 1. Razendsnelle NumPy-toonfilter (±60 Hz rond target_pitch)
                    target = float(self.target_pitch)
                    mag = 0.0
                    for off in (-60.0, 0.0, 60.0):
                        w = 2.0 * np.pi * (target + off) * t_vec
                        re = float(np.dot(win_samples, np.cos(w)))
                        im = float(np.dot(win_samples, np.sin(w)))
                        m = np.hypot(re, im) / (block_size * 0.25)
                        if m > mag:
                            mag = m

                    # 2. Snelle envelope-volger (volgt CW-sleuteling zonder vertraging)
                    if mag > sig_env:
                        sig_env = 0.5 * sig_env + 0.5 * mag
                    else:
                        sig_env = max(noise_env * 2.5, 0.993 * sig_env + 0.007 * mag)

                    if mag < sig_env * 0.35:
                        noise_env = 0.94 * noise_env + 0.06 * mag

                    snr_ok = sig_env > max(0.015, noise_env * 3.2)
                    # Hysterese: aanslaan op 50%, afvallen op 35% van de signaalsterkte
                    thresh_on = noise_env + 0.50 * (sig_env - noise_env)
                    thresh_off = noise_env + 0.35 * (sig_env - noise_env)
                    is_tone = snr_ok and (mag > (thresh_off if tone_on else thresh_on))

                    # 3. Update pitch-balkje in de UI
                    ring_buf = np.roll(ring_buf, -block_size)
                    ring_buf[-block_size:] = samples
                    if is_tone:
                        fft = np.abs(np.fft.rfft(ring_buf * np.hanning(1024)))
                        b_fft = fft[pitch_mask]
                        p_idx = int(np.argmax(b_fft))
                        p_freq = int(round(pitch_freqs[p_idx]))
                        self.detected_pitch = p_freq
                        self.detected_offset = p_freq - self.target_pitch
                        self.win.after(0, self.update_pitch_ui, p_freq, self.detected_offset)

                    # 4. Exacte sample-gebaseerde morse-decodering
                    state_dur += block_dur

                    if is_tone != tone_on:
                        if tone_on:
                            # Negeer ruispiekjes korter dan 20 ms
                            if state_dur >= 0.020:
                                if state_dur < self.dot_len * 2.0:
                                    morse_buf += "."
                                    new_dot = 0.70 * self.dot_len + 0.30 * state_dur
                                else:
                                    morse_buf += "-"
                                    new_dot = 0.70 * self.dot_len + 0.30 * (state_dur / 3.0)
                                self.dot_len = max(0.028, min(0.12, new_dot))
                        else:
                            # Letterscheiding (normaal 3 dots; grens op 2.0 dots)
                            if morse_buf and state_dur >= self.dot_len * 2.0:
                                ch = MORSE_TO_CHAR.get(morse_buf, "?")
                                self.win.after(0, self.append_rx_text, ch)
                                morse_buf = ""
                                space_added = False

                        tone_on = is_tone
                        state_dur = 0.0

                    elif not is_tone:
                        if morse_buf and state_dur >= self.dot_len * 2.2:
                            ch = MORSE_TO_CHAR.get(morse_buf, "?")
                            self.win.after(0, self.append_rx_text, ch)
                            morse_buf = ""
                            space_added = False
                        elif not morse_buf and not space_added and state_dur >= self.dot_len * 4.5:
                            self.win.after(0, self.append_rx_text, " ")
                            space_added = True

        except Exception as e:
            self.is_decoding = False
            self.win.after(0, lambda: messagebox.showerror("Audiofout", str(e)))

    def update_pitch_ui(self, peak_freq, offset):
        if not self.win.winfo_exists():
            return
        self.lbl_pitch.config(text=f"Radio Pitch: {self.target_pitch} Hz | Signaal: {peak_freq} Hz")
        self.btn_spot_on.config(text=f"SPOT ON! ({offset:+d} Hz)")
        self.draw_tune_bar(offset)
        rx_wpm = max(5, min(50, round(1.2 / max(0.024, self.dot_len))))
        self.lbl_rx_wpm.config(text=f"RX Snelheid: ~{rx_wpm} WPM")

    def append_rx_text(self, text):
        if self.win.winfo_exists():
            self.txt_rx.insert(tk.END, text, "rx_text")
            self.txt_rx.see(tk.END)

    def append_tx_text(self, text):
        if self.win.winfo_exists():
            huidige_tekst = self.txt_rx.get("1.0", tk.END)
            prefix = "\n" if huidige_tekst.strip() and not huidige_tekst.endswith("\n") else ""
            weergave_tekst = text.strip()
            if weergave_tekst:
                self.txt_rx.insert(tk.END, f"{prefix}{weergave_tekst}\n", "tx_text")
                self.txt_rx.see(tk.END)

    def on_close(self):
        self.is_decoding = False
        self.win.destroy()

