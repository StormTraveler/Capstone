# client.py
import socket
import threading
import time
import json
import sys

SIGNALING_SERVER_IP = "198.211.117.27"
SIGNALING_SERVER_PORT = 5555

PUNCH_COUNT = 12
PUNCH_INTERVAL = 0.1

class PeerClient:
    def __init__(self, username, signaling_ip, signaling_port):
        self.username = username
        # UDP socket bound to random local port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("", 0))
        self.sock.settimeout(0.5)
        self.local_udp_port = self.sock.getsockname()[1]

        # TCP signaling (kept open so server can push)
        self.tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tcp_lock = threading.Lock()

        self.signaling_ip = signaling_ip
        self.signaling_port = signaling_port

        self.peer_ip = None
        self.peer_port = None
        self.peer_username = None

        self.stop_evt = threading.Event()

    # ---------- TCP signaling helpers ----------
    def tcp_send_json(self, obj):
        data = (json.dumps(obj) + "\n").encode()
        with self.tcp_lock:
            self.tcp.sendall(data)

    def signaling_reader(self):
        buf = b""
        self.tcp.settimeout(1.0)
        while not self.stop_evt.is_set():
            try:
                chunk = self.tcp.recv(4096)
                if not chunk:
                    print("[SIGNAL] disconnected from server")
                    self.stop_evt.set()
                    break
                buf += chunk
            except socket.timeout:
                continue
            except Exception as e:
                print("[SIGNAL] error:", e)
                self.stop_evt.set()
                break

            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line.decode())
                except json.JSONDecodeError:
                    continue
                self.handle_signal(msg)

    def handle_signal(self, msg):
        act = msg.get("action")
        if act == "registered":
            print(f"[SIGNAL] registered as {msg.get('username')}")
        elif act == "error":
            print("[SIGNAL][error]", msg.get("error"))
        elif act == "peer":
            # Got peer endpoint; store & start hole punching both ways
            self.peer_username = msg.get("peer_username")
            self.peer_ip = msg.get("peer_ip")
            self.peer_port = int(msg.get("peer_port"))
            print(f"[SIGNAL] peer: {self.peer_username} @ {self.peer_ip}:{self.peer_port}")
            self.start_hole_punch()
        else:
            print("[SIGNAL] unknown msg:", msg)

    def connect_signaling(self):
        self.tcp.connect((self.signaling_ip, self.signaling_port))
        # Register username + UDP port
        self.tcp_send_json({
            "action": "register",
            "username": self.username,
            "udp_port": self.local_udp_port
        })
        threading.Thread(target=self.signaling_reader, daemon=True).start()

    def request_connect(self, target_username):
        self.tcp_send_json({"action": "connect", "target": target_username})

    # ---------- UDP messaging ----------
    def start_hole_punch(self):
        if not (self.peer_ip and self.peer_port):
            return
        def punch():
            # Send a few empty/hello packets so NATs open mappings both sides
            for i in range(PUNCH_COUNT):
                payload = json.dumps({
                    "type": "hello",
                    "from": self.username,
                    "seq": i
                }).encode()
                try:
                    self.sock.sendto(payload, (self.peer_ip, self.peer_port))
                except Exception as e:
                    print("[UDP] punch error:", e)
                time.sleep(PUNCH_INTERVAL)
            print("[UDP] hole punch packets sent.")
        threading.Thread(target=punch, daemon=True).start()

    def send_chat(self, text):
        if not (self.peer_ip and self.peer_port):
            print("[UDP] No peer yet. Use /connect <username> first.")
            return
        packet = {
            "type": "chat",
            "from": self.username,
            "to": self.peer_username,
            "ts": time.time(),
            "msg": text
        }
        try:
            self.sock.sendto(json.dumps(packet).encode(), (self.peer_ip, self.peer_port))
        except Exception as e:
            print("[UDP] send error:", e)

    def udp_receiver(self):
        while not self.stop_evt.is_set():
            try:
                data, addr = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except Exception as e:
                print("[UDP] recv error:", e)
                break

            try:
                msg = json.loads(data.decode(errors="ignore"))
            except json.JSONDecodeError:
                print(f"[UDP] non-JSON from {addr}: {data[:60]!r}")
                continue

            mtype = msg.get("type")
            if mtype == "chat":
                f = msg.get("from")
                t = msg.get("to")
                text = msg.get("msg")
                print(f"\n[{f} -> {t}] {text}")
            elif mtype == "hello":
                # Optional: reply to ensure both mappings are alive
                pass
            else:
                print(f"[UDP] {addr} {msg}")

    def close(self):
        self.stop_evt.set()
        try:
            self.tcp.close()
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


def main():
    if len(sys.argv) < 2:
        print("Usage: python client.py <your_username>")
        sys.exit(1)

    me = sys.argv[1]
    cli = PeerClient(me, SIGNALING_SERVER_IP, SIGNALING_SERVER_PORT)
    cli.connect_signaling()

    # Start UDP receive loop
    threading.Thread(target=cli.udp_receiver, daemon=True).start()

    print("Commands:\n"
          "  /connect <username>  - request peer by username\n"
          "  /quit                - exit\n"
          "  (anything else sends a chat message)\n")

    try:
        while True:
            line = input("> ").strip()
            if not line:
                continue
            if line.startswith("/connect "):
                target = line.split(maxsplit=1)[1].strip()
                cli.request_connect(target)
            elif line == "/quit":
                break
            else:
                cli.send_chat(line)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        cli.close()

if __name__ == "__main__":
    main()
