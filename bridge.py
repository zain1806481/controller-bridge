import os, sys, time, json, socket, struct, threading, ctypes, glob
from typing import Optional

# ---- constants (no edits) ----
DISCOVERY_PORT = 49010            # presence broadcast
SCREEN_PORT    = 49011            # active-screen broadcast
CLIENT_PORT    = 49002            # client virtual pad
HOST_PORT      = 49001            # host send
POLL_HZ        = 120
DEADZONE       = 2500
MAGIC = b"GP"; VERSION = 1
PACK_FMT = "<2sB H B B h h h h"
FALLBACK_HOTKEY = "f12"

# ---- tiny utils ----
def hostname(): return os.environ.get("COMPUTERNAME") or socket.gethostname()
def clamp(v, lo, hi): return lo if v<lo else hi if v>hi else v

def find_barrier_log():
    cands = []
    local = os.environ.get("LOCALAPPDATA","")
    roaming = os.environ.get("APPDATA","")
    if local:   cands += glob.glob(os.path.join(local, "input-leap", "*.log"))
    if roaming: cands += glob.glob(os.path.join(roaming, "Barrier", "*.log"))
    if not cands: return None
    cands.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return cands[0]

def parse_screen_from_line(line: str):
    l = line.lower()
    if "switch to screen" in l:
        parts = line.split('"')
        if len(parts) >= 2: return parts[1]
    return None

# ---- discovery (no IP typing) ----
class Discovery:
    def __init__(self, screen_guess: str):
        self.screen_name = screen_guess
        self.peers = {}  # screen_name -> (ip, CLIENT_PORT)
        self.stop = False
        self.rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.rx.bind(("", DISCOVERY_PORT))
        self.rx.settimeout(1.0)
        self.tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.tx.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    def start(self):
        threading.Thread(target=self._rx_loop, daemon=True).start()
        threading.Thread(target=self._tx_loop, daemon=True).start()

    def _rx_loop(self):
        while not self.stop:
            try:
                data, addr = self.rx.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                msg = json.loads(data.decode("utf-8","ignore"))
            except Exception:
                continue
            if msg.get("t") != "announce": 
                continue
            sname = msg.get("screen") or msg.get("host")
            if not sname or sname == self.screen_name:
                continue
            self.peers[sname] = (addr[0], CLIENT_PORT)

    def _tx_loop(self):
        raw = json.dumps({"t":"announce","screen":self.screen_name}).encode()
        while not self.stop:
            try: self.tx.sendto(raw, ("255.255.255.255", DISCOVERY_PORT))
            except Exception: pass
            time.sleep(1.5)

    def resolve(self, screen: str):
        return self.peers.get(screen)

# ---- screen events ----
class ScreenBroadcast:
    """If a local Barrier/InputLeap log exists, broadcast active screen changes."""
    def __init__(self):
        self.log = find_barrier_log()
        self.tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.tx.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.last = None

    def start(self):
        if not self.log:
            print("[screen] No local Barrier/InputLeap log found on this PC (that's okay).")
            return
        print(f"[screen] Broadcasting active screen from: {self.log}")
        threading.Thread(target=self._tail_loop, daemon=True).start()

    def _tail_loop(self):
        pos = 0
        try:
            with open(self.log, "rb") as f:
                f.seek(0, os.SEEK_END); pos = f.tell()
        except Exception:
            return
        while True:
            try:
                with open(self.log, "rb") as f:
                    f.seek(pos)
                    data = f.read()
                    pos = f.tell()
                if data:
                    for line in data.decode(errors="ignore").splitlines():
                        scr = parse_screen_from_line(line)
                        if scr and scr != self.last:
                            self.last = scr
                            msg = {"t":"screen","screen":scr,"host":hostname()}
                            self.tx.sendto(json.dumps(msg).encode(), ("255.255.255.255", SCREEN_PORT))
                            # also keep presence fresh
                            self.tx.sendto(json.dumps({"t":"announce","screen":hostname()}).encode(), ("255.255.255.255", DISCOVERY_PORT))
                            print(f"[screen] Active screen -> {scr}")
            except Exception:
                pass
            time.sleep(0.15)

class ScreenListener:
    """Listens for broadcasts of active screen (from whichever PC runs the Barrier server)."""
    def __init__(self):
        self.current = hostname()
        self.lock = threading.Lock()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("", SCREEN_PORT))
        self.sock.settimeout(1.0)

    def start(self):
        threading.Thread(target=self._rx, daemon=True).start()

    def _rx(self):
        while True:
            try:
                data, _ = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            try:
                msg = json.loads(data.decode("utf-8","ignore"))
            except Exception:
                continue
            if msg.get("t") == "screen":
                scr = msg.get("screen")
                if scr:
                    with self.lock:
                        self.current = scr
                    print(f"[screen] Heard active screen -> {scr}")

    def get(self):
        with self.lock:
            return self.current

# ---- hotkey fallback ----
try:
    import keyboard
except Exception:
    keyboard = None

# ---- XInput (controller detect/read) ----
XINPUT_DLLS = [b"xinput1_4.dll", b"xinput1_3.dll", b"xinput9_1_0.dll", b"xinput1_2.dll", b"xinput1_1.dll"]

class XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [
        ("wButtons", ctypes.c_ushort),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]

class XINPUT_STATE(ctypes.Structure):
    _fields_ = [("dwPacketNumber", ctypes.c_uint), ("Gamepad", XINPUT_GAMEPAD)]

def load_xinput():
    for name in XINPUT_DLLS:
        try: return ctypes.windll.LoadLibrary(name)
        except Exception: pass
    return None

def read_gamepad_state(xinput, idx=0):
    if not xinput: return None, 0
    st = XINPUT_STATE()
    res = xinput.XInputGetState(idx, ctypes.byref(st))
    if res != 0: return None, 0
    return st.Gamepad, st.dwPacketNumber

def dz(v): return 0 if abs(v) < DEADZONE else v

def pack_state(gp):
    return struct.pack(PACK_FMT, MAGIC, VERSION,
        gp.wButtons, gp.bLeftTrigger, gp.bRightTrigger,
        dz(gp.sThumbLX), dz(gp.sThumbLY), dz(gp.sThumbRX), dz(gp.sThumbRY)
    )

# ---- client (virtual pad) ----
def run_client():
    try:
        from pyvgamepad import VX360Gamepad
    except Exception:
        print("[client] pyvgamepad missing or ViGEmBus not installed. Install ViGEmBus and re-run.")
        return
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", CLIENT_PORT))
    sock.settimeout(1.0)
    pad = VX360Gamepad()
    print(f"[client] Listening on UDP :{CLIENT_PORT}")
    while True:
        try:
            data, _ = sock.recvfrom(128)
        except socket.timeout:
            continue
        except KeyboardInterrupt:
            break
        try:
            magic, ver, buttons, lt, rt, lx, ly, rx, ry = struct.unpack(PACK_FMT, data)
        except struct.error:
            continue
        if magic != MAGIC or ver != VERSION:
            continue
        # Update virtual pad
        pad.left_trigger(value=clamp(lt,0,255))
        pad.right_trigger(value=clamp(rt,0,255))
        pad.left_joystick(x_value=clamp(lx,-32768,32767), y_value=clamp(ly,-32768,32767))
        pad.right_joystick(x_value=clamp(rx,-32768,32767), y_value=clamp(ry,-32768,32767))
        from pyvgamepad import VX360Gamepad as G
        B = buttons
        def setb(btn, bit):
            (pad.press_button if (B & bit) else pad.release_button)(button=btn)
        MAP = [
            (G.BUTTON_A, 0x1000),(G.BUTTON_B,0x2000),(G.BUTTON_X,0x4000),(G.BUTTON_Y,0x8000),
            (G.BUTTON_SHOULDER_LEFT,0x0100),(G.BUTTON_SHOULDER_RIGHT,0x0200),
            (G.BUTTON_THUMB_LEFT,0x0040),(G.BUTTON_THUMB_RIGHT,0x0080),
            (G.BUTTON_START,0x0010),(G.BUTTON_BACK,0x0020),
            (G.BUTTON_DPAD_UP,0x0001),(G.BUTTON_DPAD_DOWN,0x0002),
            (G.BUTTON_DPAD_LEFT,0x0004),(G.BUTTON_DPAD_RIGHT,0x0008),
        ]
        for btn, bit in MAP: setb(btn, bit)
        pad.update()

# ---- host (reads controller + sends based on active screen) ----
def run_host():
    me = hostname()
    disc = Discovery(me); disc.start()
    screen_tx = ScreenBroadcast(); screen_tx.start()      # will only broadcast if log exists
    screen_rx = ScreenListener();  screen_rx.start()

    xinput = load_xinput()
    if not xinput:
        print("[host] XInput not available; this PC won’t act as host (controller reader).")
        return

    # Quick probe: do we have a controller now?
    gp0, _ = read_gamepad_state(xinput, 0)
    if not gp0:
        print("[host] No controller detected right now. Will keep checking; meanwhile this PC still acts as client.")
    else:
        print("[host] Controller detected. This PC will act as HOST when needed.")

    XIGet = xinput.XInputGetState
    XIGet.argtypes = [ctypes.c_uint, ctypes.POINTER(XINPUT_STATE)]
    XIGet.restype = ctypes.c_uint

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", HOST_PORT))
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    state = {"manual": False}

    if keyboard:
        keyboard.add_hotkey(FALLBACK_HOTKEY, lambda: toggle_manual())

    def toggle_manual():
        state["manual"] = not state["manual"]
        print(f"[host] Manual toggle: {'REMOTE' if state['manual'] else 'LOCAL'}")

    last_target = None
    while True:
        gp, _ = read_gamepad_state(xinput, 0)
        if not gp:
            time.sleep(0.25)
            continue

        # Determine active screen
        active = screen_rx.get()
        # Resolve target
        if state["manual"]:
            # send to first known peer if any
            target = None
            if disc.peers:
                sname = next(iter(disc.peers.keys()))
                target = disc.resolve(sname)
        else:
            target = None if active == me else disc.resolve(active)

        if target != last_target:
            print(f"[host] {'Forwarding -> ' + str(target) if target else 'Local control'}")
            last_target = target

        if target:
            sock.sendto(pack_state(gp), target)
        time.sleep(1.0 / POLL_HZ)

# ---- entry: --auto runs everything needed on each PC ----
if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--auto":
        # Always run client listener in a thread
        threading.Thread(target=run_client, daemon=True).start()
        # Also attempt to run host (only effective if a controller is present)
        try:
            run_host()
        except KeyboardInterrupt:
            pass
        sys.exit(0)

    print("Usage: python bridge.py --auto")
    sys.exit(1)
