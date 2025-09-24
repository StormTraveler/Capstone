# signaling_server.py
import socket
import threading
import json

# username -> {"conn": socket, "ip": str, "udp_port": int}
REGISTRY = {}
LOCK = threading.Lock()

def send_json(conn, obj):
    try:
        data = (json.dumps(obj) + "\n").encode()
        conn.sendall(data)
    except Exception:
        pass

def handle_disconnect(username):
    with LOCK:
        if username in REGISTRY:
            try:
                REGISTRY[username]["conn"].close()
            except Exception:
                pass
            del REGISTRY[username]
            print(f"[INFO] {username} removed")

def handle_client(conn, addr):
    """
    Protocol (newline-delimited JSON):
      1) Client sends:
         {"action":"register","username":"alice","udp_port":54321}
      2) Later client sends:
         {"action":"connect","target":"bob"}
      Server replies to BOTH alice and bob with:
         {"action":"peer","peer_username":"<other>","peer_ip":"A.B.C.D","peer_port":NNNN}
    """
    peername = f"{addr[0]}:{addr[1]}"
    print(f"[INFO] TCP connected from {peername}")

    username = None
    buf = b""
    conn.settimeout(1.0)

    try:
        while True:
            try:
                chunk = conn.recv(4096)
                if not chunk:
                    # Client closed
                    break
                buf += chunk
            except socket.timeout:
                chunk = b""

            # Process full lines (newline-delimited JSON)
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line.decode())
                except json.JSONDecodeError:
                    send_json(conn, {"action":"error","error":"bad_json"})
                    continue

                act = msg.get("action")

                if act == "register":
                    # Required fields
                    username = msg.get("username")
                    udp_port = msg.get("udp_port")
                    if not username or not isinstance(udp_port, int):
                        send_json(conn, {"action":"error","error":"missing_fields"})
                        continue

                    with LOCK:
                        # If username already in use, drop previous
                        if username in REGISTRY:
                            try:
                                REGISTRY[username]["conn"].close()
                            except Exception:
                                pass
                        REGISTRY[username] = {
                            "conn": conn,
                            "ip": addr[0],   # public IP seen by server
                            "udp_port": udp_port
                        }
                    print(f"[INFO] registered {username} @ {addr[0]} udp:{udp_port}")
                    send_json(conn, {"action":"registered","username":username})

                elif act == "connect":
                    if not username:
                        send_json(conn, {"action":"error","error":"not_registered"})
                        continue
                    target = msg.get("target")
                    if not target:
                        send_json(conn, {"action":"error","error":"missing_target"})
                        continue

                    with LOCK:
                        me = REGISTRY.get(username)
                        other = REGISTRY.get(target)

                    if not other:
                        send_json(conn, {"action":"error","error":"target_not_online"})
                        continue

                    # Push peer info to both sides
                    my_info = {"ip": me["ip"], "udp_port": me["udp_port"]}
                    other_info = {"ip": other["ip"], "udp_port": other["udp_port"]}

                    send_json(me["conn"], {
                        "action":"peer",
                        "peer_username": target,
                        "peer_ip": other_info["ip"],
                        "peer_port": other_info["udp_port"]
                    })
                    send_json(other["conn"], {
                        "action":"peer",
                        "peer_username": username,
                        "peer_ip": my_info["ip"],
                        "peer_port": my_info["udp_port"]
                    })
                    print(f"[INFO] paired {username} <-> {target}")

                else:
                    send_json(conn, {"action":"error","error":"unknown_action"})
    except Exception as e:
        print(f"[WARN] {peername} error: {e}")
    finally:
        if username:
            handle_disconnect(username)
        else:
            try:
                conn.close()
            except Exception:
                pass
        print(f"[INFO] TCP closed for {peername}")

def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", 5555))
    srv.listen(128)
    print("Signaling server listening on :5555")

    while True:
        conn, addr = srv.accept()
        threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()

if __name__ == "__main__":
    main()

