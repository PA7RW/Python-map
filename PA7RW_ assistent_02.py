import sys
import time
import serial
import threading
import tkinter as tk
from tkinter import messagebox, ttk

# --- CI-V PARAMETERS VOOR DE IC-7300mk2 ---
# Standaard CI-V adres voor de IC-7300mk2 is 0xB6. Het programma luistert naar de laptop (0xE0).
IC7300mk2_ADDRESS = 0xB6
CONTROLLER_ADDRESS = 0xE0

# Modus mappen volgens het Icom CI-V protocol
MODES = {
    "LSB": b'\x00\x01',
    "USB": b'\x01\x01',
    "AM":  b'\x02\x01',
    "CW":  b'\x03\x01',
    "RTTY": b'\x04\x01',
    "FM":  b'\x05\x01',
    "LSB-D": b'\x06\x01',
    "USB-D": b'\x07\x01',
}
REVERSE_MODES = {v[0]: k for k, v in MODES.items()}

class IC7300mk2ControlApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Icom IC-7300mk2 Controller")
        self.root.geometry("440x330")
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
        self.port_entry.insert(0, "COM4") # Pas dit aan naar jouw poort (bijv. /dev/ttyUSB0 op Linux)
        self.port_entry.grid(row=0, column=1, padx=5)
        
        ttk.Label(conn_frame, text="Baudrate:").grid(row=0, column=2, sticky="w", padx=5)
        self.baud_combo = ttk.Combobox(conn_frame, values=["4800", "9600", "19200", "38400", "115200"], width=8)
        self.baud_combo.set("115200") # IC-7300mk2 standaard is vaak 'Auto' of 115200
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
        self.freq_entry.insert(0, "7074000") # Standaard FT8 frequentie op 40m als voorbeeld
        
        btn_set_freq = ttk.Button(set_freq_subframe, text="Instellen", command=self.set_frequency)
        btn_set_freq.pack(side="right", padx=5)

        # --- Modus Paneel ---
        mode_frame = ttk.LabelFrame(self.root, text=" Modus ", padding=10)
        mode_frame.pack(fill="x", padx=10, pady=5)
        
        self.lbl_current_mode = ttk.Label(mode_frame, text="Huidige modus: ONBEKEND", font=("Arial", 10, "italic"))
        self.lbl_current_mode.pack(pady=2)
        
        btn_buttons_frame = ttk.Frame(mode_frame)
        btn_buttons_frame.pack(fill="x", pady=5)
        
        for m in ["LSB", "USB", "CW", "RTTY", "LSB-D", "USB-D", "AM", "FM"]:
            btn = ttk.Button(btn_buttons_frame, text=m, command=lambda mode=m: self.set_mode(mode))
            btn.pack(side="left", fill="x", expand=True, padx=2)

    def toggle_connection(self):
        if not self.is_connected:
            port = self.port_entry.get()
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
            print(f"Verzonden: {frame.hex(' ')} | Ontvangen: {response.hex(' ')}")
            return response
        except Exception as e:
            print(f"Seriële fout: {e}")
            return None

    def set_frequency(self):
        try:
            freq_hz = int(self.freq_entry.get())
            # Converteer naar BCD (Packed Binary Coded Decimal) - 5 bytes voor Icom
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

    def query_status(self):
        # Vraag frequentie op (Cmd 03)
        res_freq = self.send_cmd(b'\x03')
        # Vraag modus op (Cmd 04)
        res_mode = self.send_cmd(b'\x04')
        
        self.parse_and_update(res_freq, res_mode)

    def parse_and_update(self, res_freq, res_mode):
        # Verwerk frequentie
        if res_freq and len(res_freq) >= 11:
            # Zoek naar de data in het antwoord (meestal vanaf index 5 tot 10 bij geldige info)
            # Voor de simpelheid scannen we op het juiste formaat binnen de buffer
            idx = res_freq.find(b'\xFE\xFE' + bytes([CONTROLLER_ADDRESS, IC7300mk2_ADDRESS]) + b'\x03')
            if idx != -1 and len(res_freq) >= idx + 11:
                data = res_freq[idx+5:idx+10]
                freq_str = f"{data[4]:02x}{data[3]:02x}{data[2]:02x}{data[1]:02x}{data[0]:02x}"
                try:
                    freq_mhz = int(freq_str) / 1_000_000
                    self.lbl_current_freq.config(text=f"{freq_mhz:.6f} MHz")
                except ValueError:
                    pass

        # Verwerk modus
        if res_mode and len(res_mode) >= 8:
            idx = res_mode.find(b'\xFE\xFE' + bytes([CONTROLLER_ADDRESS, IC7300mk2_ADDRESS]) + b'\x04')
            if idx != -1 and len(res_mode) >= idx + 7:
                mode_byte = res_mode[idx+5]
                mode_name = REVERSE_MODES.get(mode_byte, f"Onbekend ({hex(mode_byte)})")
                self.lbl_current_mode.config(text=f"Huidige modus: {mode_name}")

    def update_loop(self):
        while self.is_connected:
            self.query_status()
            time.sleep(1) # Update elke seconde

if __name__ == "__main__":
    root = tk.Tk()
    app = IC7300mk2ControlApp(root)
    root.mainloop()