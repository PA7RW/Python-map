"""
Military Calisthenics 10-Min Workout Starter
============================================
Dit Python-script start de workout web-app op je eigen PC en maakt
deze direct bereikbaar voor je Samsung smartphone via je WiFi-thuisnetwerk.
"""

import http.server
import socket
import socketserver
import webbrowser
import os
import sys

PORT = 8080
MAP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workout_app")

def get_lan_ip():
    """Haal het lokale WiFi IP-adres van deze computer op."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Verbind virtueel met een extern adres om de actieve netwerkinterface te vinden
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

class WorkoutHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=MAP_DIR, **kwargs)

    def log_message(self, format, *args):
        # Mute normale HTTP logregels voor een schone terminal
        pass

def main():
    if not os.path.exists(MAP_DIR):
        print(f"Fout: Map {MAP_DIR} niet gevonden!")
        sys.exit(1)

    lan_ip = get_lan_ip()
    pc_url = f"http://localhost:{PORT}"
    mobiel_url = f"http://{lan_ip}:{PORT}"

    print("=" * 64)
    print("💪 MILITARY CALISTHENICS - 10 MIN WORKOUT APPLICATIE")
    print("=" * 64)
    print("\nDe applicatie is succesvol gestart!\n")
    print(f"🖥️  Op je computer:")
    print(f"    {pc_url}\n")
    print(f"📱  Op je Samsung telefoon (via dezelfde WiFi):")
    print(f"    {mobiel_url}\n")
    print("-" * 64)
    print("⭐ TIP OM ER EEN ECHTE APP VAN TE MAKEN OP JE SAMSUNG:")
    print("   1. Open de link hierboven op je telefoon in Chrome of Samsung Internet.")
    print("   2. Tik rechtsboven op het menu (de 3 puntjes ⋮).")
    print("   3. Kies: 'Toevoegen aan startscherm' (Add to Home screen).")
    print("   -> De app krijgt nu een eigen icoontje en opent schermvullend!")
    print("-" * 64)
    print("\nDruk op [Ctrl + C] in deze terminal om de server te stoppen.\n")

    # Open automatisch de browser op de computer
    webbrowser.open(pc_url)

    # Start de server (blijft luisteren op alle netwerkinterfaces 0.0.0.0)
    socketserver.TCPServer.allow_reuse_address = True
    try:
        with socketserver.TCPServer(("", PORT), WorkoutHandler) as httpd:
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nWorkout applicatie netjes afgesloten. Goede training!")

if __name__ == "__main__":
    main()
