import tkinter as tk
from tkinter import ttk, messagebox
import serial
import time
import threading

# CI-V instellingen voor de Icom IC-7300MK2
IC7300mk2_ADDRESS = 0xB6
CONTROLLER_ADDRESS = 0xE0

# Modus mappen volgens het Icom CI-V protocol
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
        self.root.title("Icom IC-7300mk2 Controller - PA7RW")
        self.root.geometry("430x340")
        self.root.resizable(False, False)

        self.ser = None
        self.is_connected = False

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

        # --- Modus Paneel (2 rijen van 4 knoppen) ---
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

    def send_cmd(self, cmd, data=b''):
        if not self.is_connected or not self.ser:
            return None
        frame = b'\xFE\xFE' + bytes([IC7300mk2_ADDRESS, CONTROLLER_ADDRESS]) + cmd + data + b'\xFD'
        try:
            self.ser.reset_input_buffer()
            self.ser.write(frame)
            time.sleep(0.08)
            response = b''
            while self.ser.in_waiting > 0:
                response += self.ser.read(self.ser.in_waiting)
                time.sleep(0.01)
            return response
        except Exception:
            return None

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
            time.sleep(0.08)
            if mode_name.endswith("-D"):
                self.send_cmd(b'\x1A\x06', b'\x01\x01')
            else:
                self.send_cmd(b'\x1A\x06', b'\x00\x00')

    def query_status(self):
        res_freq = self.send_cmd(b'\x03')
        res_mode = self.send_cmd(b'\x04')
        res_data = self.send_cmd(b'\x1A\x06')
        self.parse_and_update(res_freq, res_mode, res_data)

    def parse_and_update(self, res_freq, res_mode, res_data=None):
        if res_freq and len(res_freq) >= 11:
            idx = res_freq.find(b'\xFE\xFE' + bytes([CONTROLLER_ADDRESS, IC7300mk2_ADDRESS]) + b'\x03')
            if idx != -1 and len(res_freq) >= idx + 11:
                data = res_freq[idx+5:idx+10]
                freq_str = f"{data[4]:02x}{data[3]:02x}{data[2]:02x}{data[1]:02x}{data[0]:02x}"
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
            self.query_status()
            time.sleep(1)


if __name__ == "__main__":
    root = tk.Tk()
    app = IC7300mk2ControlApp(root)
    root.mainloop()
