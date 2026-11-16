import telnetlib
import re
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


def send_command(session_id, command, wait_seconds=0.6):
    session = session_manager.get_telnet_session(session_id)
    if not session:
        raise RuntimeError(f"会话不存在: {session_id}")
    if session["status"] != "connected":
        raise RuntimeError(f"会话已断开: {session_id}")

    with session["command_lock"]:
        tn = session["client"]
        cmd_bytes = (command + "\n").encode("utf-8")
        tn.write(cmd_bytes)
        time.sleep(wait_seconds)
        return session_manager.pop_output(session_id)


def send_command_complete(session_id, command, timeout=8):
    """Wait for this command's echo and prompt; never treat an old prompt as its result."""
    session = session_manager.get_telnet_session(session_id)
    if not session or session['status'] != 'connected':
        raise RuntimeError(f'设备会话不可用: {session_id}')
    with session['command_lock']:
        previous = session_manager.pop_output(session_id)
        output = ''
        try:
            session['client'].write((command + '\n').encode('utf-8'))
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                time.sleep(0.1)
                output += session_manager.pop_output(session_id)
                echo = output.lower().find(command.lower())
                if echo < 0:
                    continue
                body = output[echo + len(command):]
                if re.search(r'----\s*More\s*----', body, re.I):
                    return '设备输出被分页，以下内容不完整：\n' + output[echo:]
                if re.search(r'(?m)^\s*(?:<[^\r\n<>]+>|\[[^\r\n\[\]]+\])\s*$', body):
                    return output[echo:]
                if len(output) > 1024 * 1024:
                    break
            return '设备未在等待时间内返回完整命令输出和提示符；结果不完整。\n' + output[-4000:]
        finally:
            if previous:
                session_manager.append_output(session_id, previous)


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
    if not session["command_lock"].acquire(blocking=False):
        return "", session["status"] == "connected"
    try:
        output = session_manager.pop_output(session_id)
        return output, session["status"] == "connected"
    finally:
        session["command_lock"].release()
