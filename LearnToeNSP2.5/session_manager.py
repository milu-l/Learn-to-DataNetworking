import threading
import uuid
import time
from queue import Queue


class SessionManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._scans = {}
        self._telnet_sessions = {}

    def create_scan(self, ip, start_port, end_port, timeout):
        scan_id = str(uuid.uuid4())[:8]
        scan = {
            "id": scan_id,
            "ip": ip,
            "start_port": start_port,
            "end_port": end_port,
            "timeout": timeout,
            "open_ports": [],
            "scanned": 0,
            "total": end_port - start_port + 1,
            "status": "scanning",
            "events": Queue(),
            "created_at": time.time()
        }
        with self._lock:
            self._scans[scan_id] = scan
        return scan

    def get_scan(self, scan_id):
        with self._lock:
            return self._scans.get(scan_id)

    def add_open_port(self, scan_id, port):
        with self._lock:
            if scan_id in self._scans:
                self._scans[scan_id]["open_ports"].append(port)
                self._scans[scan_id]["scanned"] += 1
                self._scans[scan_id]["events"].put({
                    "type": "found",
                    "port": port,
                    "open_ports": list(self._scans[scan_id]["open_ports"])
                })

    def progress_scan(self, scan_id, port):
        with self._lock:
            if scan_id in self._scans:
                self._scans[scan_id]["scanned"] += 1
                self._scans[scan_id]["events"].put({
                    "type": "progress",
                    "port": port,
                    "scanned": self._scans[scan_id]["scanned"],
                    "total": self._scans[scan_id]["total"]
                })

    def complete_scan(self, scan_id):
        with self._lock:
            if scan_id in self._scans:
                self._scans[scan_id]["status"] = "completed"
                self._scans[scan_id]["events"].put({
                    "type": "complete",
                    "open_ports": list(self._scans[scan_id]["open_ports"]),
                    "total": self._scans[scan_id]["total"]
                })

    def error_scan(self, scan_id, message):
        with self._lock:
            if scan_id in self._scans:
                self._scans[scan_id]["status"] = "error"
                self._scans[scan_id]["events"].put({
                    "type": "error",
                    "message": message
                })

    def list_scans(self):
        with self._lock:
            return {k: {
                "id": v["id"], "ip": v["ip"],
                "start_port": v["start_port"], "end_port": v["end_port"],
                "open_ports": list(v["open_ports"]),
                "status": v["status"], "total": v["total"],
                "scanned": v["scanned"]
            } for k, v in self._scans.items()}

    def create_telnet_session(self, tn_client, ip, port):
        session_id = str(uuid.uuid4())[:8]
        session = {
            "session_id": session_id,
            "client": tn_client,
            "ip": ip,
            "port": port,
            "status": "connected",
            "output_buffer": [],
            "created_at": time.time(),
            "last_active": time.time(),
            "command_lock": threading.RLock()
        }
        with self._lock:
            self._telnet_sessions[session_id] = session
        return session

    def remove_telnet_session(self, session_id):
        with self._lock:
            session = self._telnet_sessions.pop(session_id, None)
            if session:
                session["status"] = "disconnected"
            return session

    def get_telnet_session(self, session_id):
        with self._lock:
            session = self._telnet_sessions.get(session_id)
            if session:
                session["last_active"] = time.time()
            return session

    def append_output(self, session_id, data):
        with self._lock:
            if session_id in self._telnet_sessions:
                self._telnet_sessions[session_id]["output_buffer"].append(data)
                self._telnet_sessions[session_id]["last_active"] = time.time()

    def pop_output(self, session_id):
        with self._lock:
            if session_id in self._telnet_sessions:
                buf = self._telnet_sessions[session_id]["output_buffer"]
                result = "".join(buf)
                buf.clear()
                return result
            return ""

    def list_sessions(self):
        with self._lock:
            return [{
                "session_id": s["session_id"],
                "ip": s["ip"],
                "port": s["port"],
                "status": s["status"],
                "created_at": s["created_at"]
            } for s in self._telnet_sessions.values()]

    def get_session_by_port(self, ip, port):
        """根据 IP 和端口查找会话"""
        with self._lock:
            for session in self._telnet_sessions.values():
                if session["ip"] == ip and session["port"] == port:
                    session["last_active"] = time.time()
                    return session
            return None

    def get_session_count(self):
        with self._lock:
            return len(self._telnet_sessions)


session_manager = SessionManager()
