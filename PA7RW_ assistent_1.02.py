import tkinter as tk
from tkinter import ttk, messagebox
import serial
import time
import threading

IC7300mk2_ADDRESS = 0xB6
CONTROLLER_ADDRESS = 0xE0
MAX_SWR = 2.8

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
        self.root.title("Icom IC-7300mk2 Controller - PA7RW (v1.02)")
        self.root.geometry("460x585")
        self.root.resizable(False, False)

        self.ser = None
        self.is_connected = False
        self.swr_tripped = False
        self.tripped_swr_val = 0.0
        self.last_tx_swr = None
        self.saved_rf_power_bcd = b'\x01\x28'
        self.user_dragging_slider = False

        self.create_widgets()

    def create_widgets(self):
        # --- Verbindingspaneel ---
        conn_frame = ttk.LabelFrame(self.root, text=" Verbinding ", padding=10)
        conn_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(conn_frame, text="COM Poort:").grid(row=0, column=0, sticky="w")
        self.port_entry = ttk.Entry(conn_frame, width=10)
        self.port_entry.insert(0, "COM4")
        self.port_entry.grid(row=0, column=1, padx=5)

        ttk.Label(conn_frame, text="Baudrate:").grid(row=0, column=2, sticky="w", padx=5)
        self.baud_combo = ttk.Combobox(
            conn_frame,
            values=["4800", "9600", "19200", "38400", "115200"],
            width=8,
            state="readonly"
        )
        self.baud_combo.set("115200")
        self.baud_combo.grid(row=0, column=3, padx=5)

        self.btn_connect = ttk.Button(conn_frame, text="Verbinden", command=self.toggle_connection)
        self.btn_connect.grid(row=0, column=4, padx=5)

        # --- Frequentie Paneel ---
        freq_frame = ttk.LabelFrame(self.root, text=" Frequentie (Hz) ", padding=10)
        freq_frame.pack(fill="x", padx=10, pady=5)

        self.lbl_current_freq = ttk.Label(freq_frame, text="00.000.000 Hz", font=("Arial", 16, "bold"))
        self.lbl_current_freq.pack(pady=5)

        set_freq_subframe = ttk.Frame(freq_frame)
        set_freq_subframe.pack(fill="x")

        self.freq_entry = ttk.Entry(set_freq_subframe)
        self.freq_entry.pack(side="left", fill="x", expand=True, padx=5)
        self.freq_entry.insert(0, "7074000")

        btn_set_freq = ttk.Button(set_freq_subframe, text="Instellen", command=self.set_frequency)
        btn_set_freq.pack(side="right", padx=5)

        # --- Modus Paneel ---
        mode_frame = ttk.LabelFrame(self.root, text=" Modus ", padding=10)
        mode_frame.pack(fill="x", padx=10, pady=5)

        self.lbl_current_mode = ttk.Label(mode_frame, text="Huidige modus: ONBEKEND", font=("Arial", 10, "italic"))
        self.lbl_current_mode.pack(pady=2)

        btn_buttons_frame = ttk.Frame(mode_frame)
        btn_buttons_frame.pack(fill="x", pady=5)

        modes_list = ["LSB", "USB", "CW", "RTTY", "LSB-D", "USB-D", "AM", "FM"]
        for i, m in enumerate(modes_list):
            rij = i // 4
            kolom = i % 4
            btn = ttk.Button(btn_buttons_frame, text=m, command=lambda mode=m: self.set_mode(mode))
            btn.grid(row=rij, column=kolom, padx=3, pady=3, sticky="ew")
            btn_buttons_frame.columnconfigure(kolom, weight=1)

        # --- RF Power Schuifregelaar Paneel ---
        pwr_frame = ttk.LabelFrame(self.root, text=" RF Zendvermogen (0% - 100%) ", padding=10)
        pwr_frame.pack(fill="x", padx=10, pady=5)

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
        self.pwr_slider.pack(side="left", fill="x", expand=True, padx=5)
        self.pwr_slider.bind("<ButtonPress-1>", lambda e: setattr(self, "user_dragging_slider", True))
        self.pwr_slider.bind("<ButtonRelease-1>", self.on_slider_release)

        self.lbl_pwr_val = ttk.Label(pwr_row, text="50%", font=("Arial", 11, "bold"), width=5)
        self.lbl_pwr_val.pack(side="right", padx=5)

        # --- Autotuner & SWR Beveiliging Paneel ---
        tuner_frame = ttk.LabelFrame(self.root, text=f" Interne Autotuner & SWR Beveiliging (Max {MAX_SWR}) ", padding=10)
        tuner_frame.pack(fill="x", padx=10, pady=5)

        tuner_btn_row = ttk.Frame(tuner_frame)
        tuner_btn_row.pack(fill="x", pady=3)

        self.btn_tuner_on = tk.Button(tuner_btn_row, text="Tuner AAN", command=self.tuner_on, relief="raised")
        self.btn_tuner_on.pack(side="left", expand=True, fill="x", padx=2)

        self.btn_tuner_off = tk.Button(tuner_btn_row, text="Tuner UIT", command=self.tuner_off, relief="raised")
        self.btn_tuner_off.pack(side="left", expand=True, fill="x", padx=2)

        ttk.Button(tuner_btn_row, text="TUNE starten", command=self.start_tune).pack(side="left", expand=True, fill="x", padx=2)

        self.lbl_tuner_status = ttk.Label(tuner_frame, text="Tuner: Onbekend | RF Power: --%", font=("Arial", 9, "bold"))
        self.lbl_tuner_status.pack(pady=4)

        self.lbl_swr = ttk.Label(tuner_frame, text="SWR: Nog geen TX gemeten", font=("Arial", 11, "bold"), foreground="green")
        self.lbl_swr.pack(pady=3)

        self.btn_reset_swr = ttk.Button(
            tuner_frame,
            text="Reset SWR Beveiliging (Herstel RF Power)",
            command=self.reset_swr_trip,
            state="disabled"
        )
        self.btn_reset_swr.pack(fill="x", pady=4)
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
            port = self.port_entry.get().strip()
            baud = int(self.baud_combo.get())
            try:
                self.ser = serial.Serial(
                    port=port,
                    baudrate=baud,
                    timeout=0.5,
                    rtscts=False,
                    dsrdtr=False
                )
                self.ser.dtr = False
                self.ser.rts = False
                self.is_connected = True
                self.btn_connect.config(text="Ontkoppelen")
                threading.Thread(target=self.update_loop, daemon=True).start()
            except Exception as e:
                messagebox.showerror("Fout", f"Kan geen verbinding maken met {port}:\n{str(e)}")
        else:
            self.is_connected = False
            if self.ser and self.ser.is_open:
                self.ser.close()
            self.btn_connect.config(text="Verbinden")
            self.lbl_current_freq.config(text="00.000.000 Hz")
            self.lbl_current_mode.config(text="Huidige modus: ONBEKEND")
            self.lbl_tuner_status.config(text="Tuner: Niet verbonden | RF Power: --%")
            self.lbl_swr.config(text="SWR: Niet verbonden", foreground="gray")
            self.btn_tuner_on.config(bg="SystemButtonFace", fg="black")
            self.btn_tuner_off.config(bg="SystemButtonFace", fg="black")

    def send_cmd(self, cmd, data=b''):
        if not self.is_connected or not self.ser:
            return None
        frame = b'\xFE\xFE' + bytes([IC7300mk2_ADDRESS, CONTROLLER_ADDRESS]) + cmd + data + b'\xFD'
        try:
            self.ser.reset_input_buffer()
            self.ser.write(frame)
            time.sleep(0.05)
            response = b''
            while self.ser.in_waiting > 0:
                response += self.ser.read(self.ser.in_waiting)
                time.sleep(0.01)
            return response
        except Exception:
            return None

    # --- Tuner & Beveiliging functies ---
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
            text=f"ALARM! SWR {self.tripped_swr_val:.2f} (> {MAX_SWR}) -> OUTPUT 0%!",
            foreground="red"
        )

    def reset_swr_trip(self):
        self.send_cmd(b'\x14\x0A', self.saved_rf_power_bcd)
        self.swr_tripped = False
        self.tripped_swr_val = 0.0
        self.btn_reset_swr.config(state="disabled")
        self.lbl_swr.config(text="SWR: Beveiliging gereset (klaar voor TX)", foreground="green")

    def raw_to_swr(self, raw_val):
        if raw_val <= 48:
            return 1.0 + (raw_val / 48.0) * 0.5
        elif raw_val <= 80:
            return 1.5 + ((raw_val - 48) / 32.0) * 0.5
        elif raw_val <= 120:
            return 2.0 + ((raw_val - 80) / 40.0) * 1.0
        else:
            return 3.0 + ((raw_val - 120) / 30.0) * 1.0

    # --- Frequentie & Modus ---
    def set_frequency(self):
        try:
            freq_hz = int(self.freq_entry.get())
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

    def set_mode(self, mode_name):
        if mode_name in MODES:
            self.send_cmd(b'\x06', MODES[mode_name])
            time.sleep(0.06)
            if mode_name.endswith("-D"):
                self.send_cmd(b'\x1A\x06', b'\x01\x01')
            else:
                self.send_cmd(b'\x1A\x06', b'\x00\x00')

    def query_status(self):
        res_ptt = self.send_cmd(b'\x1C\x00')
        is_tx = False
        if res_ptt:
            p_idx = res_ptt.find(b'\xFE\xFE' + bytes([CONTROLLER_ADDRESS, IC7300mk2_ADDRESS]) + b'\x1C\x00')
            if p_idx != -1 and len(res_ptt) >= p_idx + 8:
                is_tx = (res_ptt[p_idx + 6] == 0x01)

        res_swr = self.send_cmd(b'\x15\x12')
        if res_swr:
            s_idx = res_swr.find(b'\xFE\xFE' + bytes([CONTROLLER_ADDRESS, IC7300mk2_ADDRESS]) + b'\x15\x12')
            if s_idx != -1 and len(res_swr) >= s_idx + 9:
                try:
                    raw_swr = int(res_swr[s_idx+6:s_idx+8].hex())
                    if is_tx or raw_swr > 0:
                        is_tx = True
                        swr_val = self.raw_to_swr(raw_swr)
                        self.last_tx_swr = swr_val
                        if swr_val > MAX_SWR:
                            self.trigger_swr_protection(swr_val)
                        elif not self.swr_tripped:
                            kleur = "orange" if swr_val > 2.0 else "green"
                            self.lbl_swr.config(text=f"LIVE TX SWR: {swr_val:.2f}", foreground=kleur)
                        return True
                except ValueError:
                    pass

        if is_tx:
            return True

        res_pwr = self.send_cmd(b'\x14\x0A')
        pwr_pct = "--"
        if res_pwr:
            w_idx = res_pwr.find(b'\xFE\xFE' + bytes([CONTROLLER_ADDRESS, IC7300mk2_ADDRESS]) + b'\x14\x0A')
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
            t_idx = res_tuner.find(b'\xFE\xFE' + bytes([CONTROLLER_ADDRESS, IC7300mk2_ADDRESS]) + b'\x1C\x01')
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
                text=f"ALARM! SWR {self.tripped_swr_val:.2f} (> {MAX_SWR}) -> OUTPUT 0%!",
                foreground="red"
            )
        elif self.last_tx_swr is not None:
            kleur = "orange" if self.last_tx_swr > 2.0 else "green"
            self.lbl_swr.config(text=f"Laatste TX SWR: {self.last_tx_swr:.2f} (RX)", foreground=kleur)
        else:
            self.lbl_swr.config(text="SWR: Nog geen TX gemeten", foreground="green")

        res_freq = self.send_cmd(b'\x03')
        res_mode = self.send_cmd(b'\x04')
        res_data = self.send_cmd(b'\x1A\x06')
        self.parse_and_update(res_freq, res_mode, res_data)
        return False

    def parse_and_update(self, res_freq, res_mode, res_data=None):
        if res_freq and len(res_freq) >= 11:
            idx = res_freq.find(b'\xFE\xFE' + bytes([CONTROLLER_ADDRESS, IC7300mk2_ADDRESS]) + b'\x03')
            if idx != -1 and len(res_freq) >= idx + 11:
                data = res_freq[idx+5:idx+10]
                freq_str = data[::-1].hex()
                try:
                    freq_mhz = int(freq_str) / 1_000_000
                    self.lbl_current_freq.config(text=f"{freq_mhz:.6f} MHz")
                except ValueError:
                    pass

        if res_mode and len(res_mode) >= 8:
            idx = res_mode.find(b'\xFE\xFE' + bytes([CONTROLLER_ADDRESS, IC7300mk2_ADDRESS]) + b'\x04')
            if idx != -1 and len(res_mode) >= idx + 7:
                mode_byte = res_mode[idx+5]
                mode_name = REVERSE_MODES.get(mode_byte, f"Onbekend ({hex(mode_byte)})")

                if res_data:
                    d_idx = res_data.find(b'\xFE\xFE' + bytes([CONTROLLER_ADDRESS, IC7300mk2_ADDRESS]) + b'\x1A\x06')
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