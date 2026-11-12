import telnetlib
import threading
import time

from session_manager import session_manager


def connect_device(ip, port, timeout=10):
    try:
        tn = telnetlib.Telnet(ip, port, timeout=timeout)
    except Exception as e:
        raise RuntimeError(f"连接失败 {ip}:{port} - {str(e)}")

    session = session_manager.create_telnet_session(tn, ip, port)

    def _read_loop():
        try:
            while True:
                try:
                    data = tn.read_very_eager()
                except EOFError:
                    break
                except Exception:
                    data = tn.read_some()
                if data:
                    text = data.decode("utf-8", errors="replace")
                    session_manager.append_output(session["session_id"], text)
                else:
                    time.sleep(0.1)
        except Exception:
            pass
        finally:
            try:
                session_manager.remove_telnet_session(session["session_id"])
            except Exception:
                pass

    t = threading.Thread(target=_read_loop, daemon=True)
    t.start()
    time.sleep(0.5)

    initial = session_manager.pop_output(session["session_id"])
    return session["session_id"], initial


def send_command(session_id, command):
    session = session_manager.get_telnet_session(session_id)
    if not session:
        raise RuntimeError(f"会话不存在: {session_id}")
    if session["status"] != "connected":
        raise RuntimeError(f"会话已断开: {session_id}")

    tn = session["client"]
    cmd_bytes = (command + "\n").encode("utf-8")
    tn.write(cmd_bytes)

    time.sleep(0.6)
    output = session_manager.pop_output(session_id)
    return output


def disconnect_device(session_id):
    session = session_manager.get_telnet_session(session_id)
    if not session:
        return False, "会话不存在"

    try:
        session["client"].close()
    except Exception:
        pass

    session_manager.remove_telnet_session(session_id)
    return True, "已断开连接"


def read_output(session_id):
    session = session_manager.get_telnet_session(session_id)
    if not session:
        return "", False
    output = session_manager.pop_output(session_id)
    return output, session["status"] == "connected"
