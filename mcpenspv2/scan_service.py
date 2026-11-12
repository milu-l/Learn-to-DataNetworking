import socket
from concurrent.futures import ThreadPoolExecutor, as_completed

from session_manager import session_manager


def _check_port(ip, port, timeout):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        result = sock.connect_ex((ip, port))
        return port, result == 0
    except Exception:
        return port, False
    finally:
        sock.close()


def scan_ports_sync(ip, start_port, end_port, timeout=2):
    open_ports = []
    total = end_port - start_port + 1

    with ThreadPoolExecutor(max_workers=100) as executor:
        futures = {}
        for port in range(start_port, end_port + 1):
            future = executor.submit(_check_port, ip, port, timeout)
            futures[future] = port

        for future in as_completed(futures):
            port, is_open = future.result()
            if is_open:
                open_ports.append(port)

    return sorted(open_ports)


def start_scan_async(ip, start_port, end_port, timeout=2):
    scan = session_manager.create_scan(ip, start_port, end_port, timeout)

    def _scan_worker():
        open_ports = []
        total = end_port - start_port + 1

        with ThreadPoolExecutor(max_workers=100) as executor:
            futures = {}
            for port in range(start_port, end_port + 1):
                future = executor.submit(_check_port, ip, port, timeout)
                futures[future] = port

            for future in as_completed(futures):
                port, is_open = future.result()
                if is_open:
                    open_ports.append(port)
                    session_manager.add_open_port(scan["id"], port)
                else:
                    session_manager.progress_scan(scan["id"], port)

        scan["open_ports"] = sorted(open_ports)
        session_manager.complete_scan(scan["id"])

    import threading
    t = threading.Thread(target=_scan_worker, daemon=True)
    t.start()

    return scan
