let state = {
    activeScanId: null,
    sessions: {},
    activeSessionId: null,
    connectedDevices: {},
    openPorts: [],
    isScanning: false,
    scanIp: null,
    topologyNames: {}
};
let trafficRunning = false;
let trafficBusy = false;
let packetData = [];
let packetLinks = {};

function loadHistory() {
    try {
        return JSON.parse(localStorage.getItem("scan_history") || "[]");
    } catch (e) {
        return [];
    }
}

function saveHistory(entry) {
    let history = loadHistory();
    history.unshift(entry);
    if (history.length > 20) history = history.slice(0, 20);
    localStorage.setItem("scan_history", JSON.stringify(history));
}

function renderHistory() {
    const history = loadHistory();
    const container = document.getElementById("historyList");
    if (history.length === 0) {
        container.innerHTML = '<div class="empty-state">暂无历史记录</div>';
        return;
    }
    container.innerHTML = history.map(h => {
        const date = new Date(h.timestamp);
        const time = date.toLocaleString("zh-CN");
        return '<div class="history-item" onclick="restoreHistory(\'' + h.ip + "'," + h.startPort + "," + h.endPort + ')">' +
            '<div class="history-ip">' + h.ip + " : " + h.startPort + "-" + h.endPort + '</div>' +
            '<div class="history-meta">' + time + " | 发现 " + h.openPorts.length + " 个端口</div></div>";
    }).join("");
}

function clearScanHistory() {
    if (!loadHistory().length || !confirm('确定清空本浏览器的扫描历史吗？')) return;
    try {
        localStorage.removeItem('scan_history');
        renderHistory();
    } catch (error) { alert('无法清空扫描历史：' + error.message); }
}

function restoreHistory(ip, startPort, endPort) {
    document.getElementById("scanIp").value = ip;
    document.getElementById("scanStart").value = startPort;
    document.getElementById("scanEnd").value = endPort;
}

function startScan() {
    if (state.isScanning) return;

    var ip = document.getElementById("scanIp").value.trim();
    var startPort = parseInt(document.getElementById("scanStart").value);
    var endPort = parseInt(document.getElementById("scanEnd").value);
    var timeout = parseInt(document.getElementById("scanTimeout").value);

    if (!ip) {
        appendToSession(null, "请输入目标 IP 地址", "error");
        return;
    }

    state.isScanning = true;
    state.scanIp = ip;
    state.openPorts = [];
    var btn = document.getElementById("btnScan");
    btn.classList.add("scanning");
    btn.innerHTML = '<span class="btn-icon">&#9679;</span> 扫描中...';

    document.getElementById("progressBar").style.display = "block";
    document.getElementById("progressFill").style.width = "0%";
    document.getElementById("scanInfo").textContent = "启动扫描...";
    document.getElementById("deviceList").innerHTML = '<div class="empty-state">扫描中...</div>';

    appendToSession(null, "[扫描] 开始扫描 " + ip + ":" + startPort + "-" + endPort, "info");

    fetch("/api/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ip: ip, start_port: startPort, end_port: endPort, timeout: timeout })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.error) {
            scanFailed(data.error);
            return;
        }
        state.activeScanId = data.scan_id;
        document.getElementById("scanInfo").textContent = "共 " + data.total + " 个端口 | 0/" + data.total;
        listenScanProgress(data.scan_id);
    })
    .catch(function(e) { scanFailed(e.message); });
}

function listenScanProgress(scanId) {
    var es = new EventSource("/api/scan/" + scanId + "/status");

    es.addEventListener("start", function(e) {
        var d = JSON.parse(e.data);
        document.getElementById("scanInfo").textContent = "共 " + d.total + " 个端口 | 0/" + d.total;
    });

    es.addEventListener("progress", function(e) {
        var d = JSON.parse(e.data);
        var pct = Math.round((d.scanned / d.total) * 100);
        document.getElementById("progressFill").style.width = pct + "%";
        document.getElementById("scanInfo").textContent = "共 " + d.total + " 个端口 | " + d.scanned + "/" + d.total;
    });

    es.addEventListener("found", function(e) {
        var d = JSON.parse(e.data);
        state.openPorts = d.open_ports;
        appendToSession(null, "[发现] 端口 " + d.port + " 开放", "output");
        renderDeviceList();
    });

    es.addEventListener("complete", function(e) {
        var d = JSON.parse(e.data);
        state.openPorts = d.open_ports;
        state.isScanning = false;
        document.getElementById("btnScan").classList.remove("scanning");
        document.getElementById("btnScan").innerHTML = '<span class="btn-icon">&#9654;</span> 开始扫描';
        es.close();

        var ip = document.getElementById("scanIp").value.trim();
        var startPort = parseInt(document.getElementById("scanStart").value);
        var endPort = parseInt(document.getElementById("scanEnd").value);
        saveHistory({ ip: ip, startPort: startPort, endPort: endPort, openPorts: d.open_ports, timestamp: Date.now(), id: Date.now().toString() });
        renderHistory();

        appendToSession(null, "[完成] 扫描结束，共发现 " + d.open_ports.length + " 个开放端口: " + (d.open_ports.join(", ") || "无"), "info");
        renderDeviceList();
    });

    es.addEventListener("error", function(e) {
        if (e.data) {
            scanFailed(JSON.parse(e.data).message);
        }
        es.close();
    });

    es.onerror = function() { es.close(); };
}

function scanFailed(msg) {
    state.isScanning = false;
    document.getElementById("btnScan").classList.remove("scanning");
    document.getElementById("btnScan").innerHTML = '<span class="btn-icon">&#9654;</span> 开始扫描';
    document.getElementById("scanInfo").textContent = "扫描失败";
    appendToSession(null, "[错误] " + msg, "error");
}

function renderDeviceList() {
    var container = document.getElementById("deviceList");
    var count = document.getElementById("deviceCount");

    if (state.openPorts.length === 0) {
        container.innerHTML = '<div class="empty-state">未发现开放端口</div>';
        count.textContent = "0";
        return;
    }

    count.textContent = state.openPorts.length;

    container.replaceChildren();
    state.openPorts.forEach(function(port) {
        var isConnected = !!state.connectedDevices[port];
        var item = document.createElement('div');
        item.className = 'device-item';
        var indicator = document.createElement('div');
        indicator.className = 'device-indicator ' + (isConnected ? 'connected' : 'open');
        var label = document.createElement('span');
        label.className = 'device-port';
        var local = ['127.0.0.1', 'localhost', '::1'].includes((state.scanIp || '').toLowerCase());
        var name = local ? state.topologyNames[port] : null;
        label.textContent = (name || '未知设备') + '（' + port + '）';
        var actions = document.createElement('div');
        actions.className = 'device-actions';
        var button = document.createElement('button');
        button.className = 'device-btn ' + (isConnected ? 'disconnect' : 'connect');
        button.textContent = isConnected ? '断开' : '连接';
        button.onclick = function() { if (isConnected) disconnectDevice(port); else connectToDevice(port); };
        actions.appendChild(button);
        item.append(indicator, label, actions);
        container.appendChild(item);
    });
}

function connectToDevice(port) {
    var ip = document.getElementById("scanIp").value.trim();
    appendToSession(null, "[连接] 正在连接 " + ip + ":" + port + " ...", "info");

    fetch("/api/telnet/connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ip: ip, port: port })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.error) {
            appendToSession(null, "[错误] " + data.error, "error");
            return;
        }
        createSessionTab(data.session_id, ip, port, data.initial_output);
        state.connectedDevices[port] = data.session_id;
        renderDeviceList();
        switchToSession(data.session_id);
    })
    .catch(function(e) { appendToSession(null, "[错误] " + e.message, "error"); });
}

function createSessionTab(sessionId, ip, port, initialOutput) {
    var panel = document.createElement("div");
    panel.className = "terminal-panel";
    panel.id = "panel-" + sessionId;

    var output = document.createElement("div");
    output.className = "terminal-body";
    output.id = "output-" + sessionId;
    panel.appendChild(output);

    var inputBar = document.createElement("div");
    inputBar.className = "terminal-input-bar";

    var prompt = document.createElement("span");
    prompt.className = "prompt";
    prompt.textContent = ip + ":" + port + " >";
    inputBar.appendChild(prompt);

    var input = document.createElement("input");
    input.type = "text";
    input.className = "terminal-cmd";
    input.placeholder = "输入命令后按回车发送...";
    input.setAttribute("data-session", sessionId);
    input.onkeydown = function(e) {
        if (e.key === "Enter") {
            handleTerminalKey(sessionId);
        }
    };
    inputBar.appendChild(input);
    panel.appendChild(inputBar);

    document.getElementById("terminalContent").appendChild(panel);

    if (initialOutput) {
        appendToSession(sessionId, initialOutput.trim(), "output");
    }
    appendToSession(sessionId, "[连接] 已连接到 " + ip + ":" + port + " (会话: " + sessionId + ")", "info");

    state.sessions[sessionId] = {
        ip: ip,
        port: port,
        outputEl: output,
        inputEl: input,
        panelEl: panel
    };

    startPolling(sessionId);
    renderTabs();
}

function disconnectDevice(port) {
    var sessionId = state.connectedDevices[port];
    if (!sessionId) return;

    fetch("/api/telnet/" + sessionId + "/disconnect", { method: "POST" })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        stopPolling(sessionId);
        delete state.connectedDevices[port];

        var panel = document.getElementById("panel-" + sessionId);
        if (panel) panel.remove();

        if (state.activeSessionId === sessionId) {
            state.activeSessionId = null;
            var remainingSessions = state.sessions;
            delete remainingSessions[sessionId];
            var ids = Object.keys(remainingSessions);
            if (ids.length > 0) {
                switchToSession(ids[0]);
            } else {
                showWelcome();
            }
        } else {
            delete state.sessions[sessionId];
        }

        renderDeviceList();
        renderTabs();
        appendToSession(null, "[断开] " + data.message, "info");
    });
}

function switchToSession(sessionId) {
    state.activeSessionId = sessionId;
    var sess = state.sessions[sessionId];
    if (!sess) return;

    var panels = document.querySelectorAll(".terminal-panel");
    for (var i = 0; i < panels.length; i++) {
        panels[i].style.display = "none";
    }

    var welcome = document.getElementById("terminalWelcome");
    if (welcome) welcome.style.display = "none";

    sess.panelEl.style.display = "flex";
    sess.inputEl.focus();

    document.getElementById("btnClear").style.display = "inline-flex";
    renderTabs();
}

function showWelcome() {
    state.activeSessionId = null;
    var panels = document.querySelectorAll(".terminal-panel");
    for (var i = 0; i < panels.length; i++) {
        panels[i].style.display = "none";
    }
    var welcome = document.getElementById("terminalWelcome");
    if (welcome) welcome.style.display = "block";
    document.getElementById("btnClear").style.display = "none";
    renderTabs();
}

function renderTabs() {
    var tabs = document.getElementById("terminalTabs");
    var html = "";
    var ids = Object.keys(state.sessions);
    for (var i = 0; i < ids.length; i++) {
        var sid = ids[i];
        var s = state.sessions[sid];
        var isActive = sid === state.activeSessionId;
        html += '<div class="terminal-tab' + (isActive ? " active" : "") + '" onclick="switchToSession(\'' + sid + '\')">' +
            '<span class="tab-indicator"></span>' +
            s.ip + ":" + s.port +
            '<span class="tab-close" onclick="event.stopPropagation(); closeTab(\'' + sid + '\',' + s.port + ')">&times;</span>' +
            '</div>';
    }
    tabs.innerHTML = html;
}

function closeTab(sessionId, port) {
    disconnectDevice(parseInt(port));
}

function handleTerminalKey(sessionId) {
    var sess = state.sessions[sessionId];
    if (!sess) return;

    var command = sess.inputEl.value.trim();
    if (!command) return;
    sess.inputEl.value = "";

    appendToSession(sessionId, command, "input");

    fetch("/api/telnet/" + sessionId + "/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command: command })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.error) {
            appendToSession(sessionId, "[错误] " + data.error, "error");
            return;
        }
        if (data.output) {
            appendToSession(sessionId, data.output.trim(), "output");
        }
    })
    .catch(function(e) { appendToSession(sessionId, "[错误] " + e.message, "error"); });
}

function appendToSession(sessionId, text, type) {
    var target;
    if (sessionId && state.sessions[sessionId]) {
        target = state.sessions[sessionId].outputEl;
    } else {
        var active = state.activeSessionId;
        if (active && state.sessions[active]) {
            target = state.sessions[active].outputEl;
        } else {
            return;
        }
    }

    var line = document.createElement("div");
    line.className = "terminal-line " + (type || "output");
    line.textContent = text;
    target.appendChild(line);
    target.scrollTop = target.scrollHeight;
}

function startPolling(sessionId) {
    if (!state.sessions[sessionId]) return;

    state.sessions[sessionId]._pollTimer = setInterval(function() {
        var sess = state.sessions[sessionId];
        if (!sess) { return; }
        fetch("/api/telnet/" + sessionId + "/read")
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.data) {
                appendToSession(sessionId, data.data.trim(), "output");
            }
            if (!data.connected) {
                appendToSession(sessionId, "[系统] 连接已断开", "error");
                stopPolling(sessionId);
            }
        })
        .catch(function() {});
    }, 500);
}

function stopPolling(sessionId) {
    if (state.sessions[sessionId] && state.sessions[sessionId]._pollTimer) {
        clearInterval(state.sessions[sessionId]._pollTimer);
        state.sessions[sessionId]._pollTimer = null;
    }
}

function clearActiveTerminal() {
    var active = state.activeSessionId;
    if (active && state.sessions[active]) {
        state.sessions[active].outputEl.innerHTML = "";
    }
}

function handleTopoUpload(event) {
    var file = event.target.files[0];
    if (!file) return;

    appendToSession(null, "[拓扑] 正在上传并解析: " + file.name, "info");

    var formData = new FormData();
    formData.append("file", file);

    fetch("/api/topo/upload", {
        method: "POST",
        body: formData
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.error) {
            appendToSession(null, "[拓扑错误] " + data.error, "error");
            return;
        }

        var s = data.summary;
        var statsHtml = "设备: <b>" + data.device_count + "</b> | " +
                        "链路: <b>" + data.connection_count + "</b> | " +
                        "VLAN: <b>" + data.vlan_count + "</b> | " +
                        "子网: <b>" + data.subnet_count + "</b>";

        document.getElementById("topoFilename").textContent = data.filename;
        document.getElementById("topoStats").innerHTML = statsHtml;
        document.getElementById("topoStatus").textContent = "已加载";
        document.getElementById("topoStatus").classList.add("loaded");
        document.getElementById("topoInfo").style.display = "block";
        document.getElementById("topoActions").style.display = "block";
        document.getElementById("clearTopologyButton").hidden = false;
        loadTopologyGraph();
        switchWorkspaceView("topology");

        appendToSession(null, "[拓扑] 文件 " + data.filename + " 解析成功 | " +
            data.device_count + " 台设备, " + data.connection_count + " 条链路", "info");
    })
    .catch(function(e) {
        appendToSession(null, "[拓扑错误] " + e.message, "error");
    });
}

function switchWorkspaceView(view) {
    var isTopology = view === "topology";
    document.getElementById("terminalView").hidden = isTopology;
    document.getElementById("topologyView").hidden = !isTopology;
    ["terminal", "topology"].forEach(function(name) {
        var tab = document.getElementById(name + "ViewTab");
        var active = name === view;
        tab.classList.toggle("active", active);
        tab.setAttribute("aria-selected", String(active));
    });
    if (isTopology) loadTopologyGraph();
}

function loadTopologyGraph() {
    fetch("/api/topo/graph", { cache: "no-store" })
    .then(function(r) { if (!r.ok) throw new Error("无法获取拓扑"); return r.json(); })
    .then(function(data) {
        renderTopologyGraph(data);
        if (!data.loaded) { resetTopologyDisplay(); return; }
        state.topologyNames = {};
        (data.devices || []).forEach(function(device) {
            if (/^\d+$/.test(String(device.com_port)) && device.name) state.topologyNames[Number(device.com_port)] = device.name;
        });
        renderDeviceList();
        document.getElementById("topoFilename").textContent = data.filename;
        document.getElementById("topoStats").textContent = "设备: " + data.summary.total_devices + " | 链路: " + data.summary.total_connections + " | VLAN: " + data.summary.vlan_count + " | 子网: " + data.summary.subnet_count;
        document.getElementById("topoStatus").textContent = "已加载";
        document.getElementById("topoStatus").classList.add("loaded");
        document.getElementById("topoInfo").style.display = "block";
        document.getElementById("topoActions").style.display = "block";
        document.getElementById("clearTopologyButton").hidden = false;
    })
    .catch(function() { document.getElementById("graphEmpty").textContent = "无法加载拓扑，请刷新后重试"; document.getElementById("graphEmpty").hidden = false; });
}

function resetTopologyDisplay() {
    packetData = [];
    packetLinks = {};
    renderPackets();
    state.topologyNames = {};
    renderDeviceList();
    document.getElementById('topoFilename').textContent = '';
    document.getElementById('topoStats').textContent = '';
    document.getElementById('topoStatus').textContent = '未加载';
    document.getElementById('topoStatus').classList.remove('loaded');
    document.getElementById('topoInfo').style.display = 'none';
    document.getElementById('topoActions').style.display = 'none';
    document.getElementById('topoOutput').style.display = 'none';
    document.getElementById('topoOutputContent').textContent = '';
    document.getElementById('topoFileInput').value = '';
    document.getElementById('clearTopologyButton').hidden = true;
    renderTopologyGraph({loaded: false});
}

async function clearTopology() {
    if (!confirm('确定删除当前拓扑及其分析报告吗？这也会停止流量监控。')) return;
    var button = document.getElementById('clearTopologyButton');
    button.disabled = true;
    try {
        var response = await fetch('/api/topo', {method: 'DELETE'});
        var data = await response.json();
        if (!response.ok) throw new Error(data.error || '删除失败');
        resetTopologyDisplay();
        await refreshTrafficStatus();
    } catch (error) { alert('删除拓扑失败：' + error.message); }
    finally { button.disabled = false; }
}

function renderTopologyGraph(data) {
    var svg = document.getElementById("topologyGraph"), empty = document.getElementById("graphEmpty");
    var stats = document.getElementById("graphStats");
    svg.replaceChildren();
    var devices = Array.isArray(data.devices) ? data.devices : [];
    var links = Array.isArray(data.connections) ? data.connections : [];
    packetLinks = {};
    var names = new Map(devices.map(function(device) { return [device.id, device.name]; }));
    links.forEach(function(link, index) {
        for (var i = 0; i < (Number(link.link_count) || 1); i++) {
            packetLinks[index + ':' + i] = (names.get(link.source_id) || '未知设备') + ' ↔ ' + (names.get(link.target_id) || '未知设备') + ((link.link_count > 1) ? ' #' + (i + 1) : '');
        }
    });
    renderPackets();
    if (!data.loaded || !devices.length) {
        empty.hidden = false;
        empty.textContent = data.loaded ? "拓扑中没有可显示的设备" : "上传拓扑文件后显示设备与连线";
        stats.textContent = "";
        return;
    }
    empty.hidden = true;
    var ns = "http://www.w3.org/2000/svg";
    function make(tag, attrs) {
        var element = document.createElementNS(ns, tag);
        Object.entries(attrs || {}).forEach(function(pair) { element.setAttribute(pair[0], pair[1]); });
        return element;
    }
    var positions = new Map();
    devices.forEach(function(device, i) {
        var p = device.position || {}, x = Number(p.x), y = Number(p.y);
        if (!Number.isFinite(x) || !Number.isFinite(y)) { x = 250 + 160 * Math.cos(2 * Math.PI * i / devices.length); y = 200 + 140 * Math.sin(2 * Math.PI * i / devices.length); }
        positions.set(device.id, { x: x, y: y });
    });
    var points = Array.from(positions.values());
    var minX = Math.min.apply(null, points.map(p => p.x)) - 85, maxX = Math.max.apply(null, points.map(p => p.x)) + 85;
    var minY = Math.min.apply(null, points.map(p => p.y)) - 70, maxY = Math.max.apply(null, points.map(p => p.y)) + 85;
    svg.setAttribute("viewBox", [minX, minY, maxX - minX, maxY - minY].join(" "));
    var count = 0;
    links.forEach(function(link, linkIndex) {
        var source = positions.get(link.source_id), target = positions.get(link.target_id);
        if (!source || !target) return;
        var n = Math.max(1, Number(link.link_count) || 1), dx = target.x - source.x, dy = target.y - source.y, length = Math.hypot(dx, dy) || 1;
        for (var i = 0; i < n; i++) {
            var offset = (i - (n - 1) / 2) * 7, ox = -dy / length * offset, oy = dx / length * offset;
            var linkId = linkIndex + ":" + i;
            svg.appendChild(make("line", { x1: source.x + ox, y1: source.y + oy, x2: target.x + ox, y2: target.y + oy, class: "graph-link", "data-link-id": linkId }));
            var label = make("text", { x: (source.x + target.x) / 2 + ox, y: (source.y + target.y) / 2 + oy - 5, class: "graph-traffic", "data-link-id": linkId });
            svg.appendChild(label);
            count++;
        }
    });
    devices.forEach(function(device) {
        var p = positions.get(device.id), group = make("g", { class: "graph-node", transform: "translate(" + p.x + " " + p.y + ")" });
        var title = make("title"); title.textContent = (device.name || "设备") + (device.model ? " · " + device.model : ""); group.appendChild(title);
        group.appendChild(make("rect", { x: -24, y: -20, width: 48, height: 40, rx: 8 }));
        var icon = make("text", { x: 0, y: 7, class: "graph-icon" }); icon.textContent = "↔"; group.appendChild(icon);
        var name = make("text", { x: 0, y: 37 }); name.textContent = device.name || "设备"; group.appendChild(name);
        var model = make("text", { x: 0, y: 51, class: "graph-model" }); model.textContent = device.model || ""; group.appendChild(model);
        svg.appendChild(group);
    });
    stats.textContent = devices.length + " 台设备 · " + count + " 条连线";
}

async function loadTrafficInterfaces() {
    try {
        var response = await fetch('/api/traffic/interfaces', { cache: 'no-store' });
        var data = await response.json();
        if (!response.ok) throw new Error(data.error || '无法获取网卡');
        var select = document.getElementById('trafficInterface');
        var previous = select.value;
        select.replaceChildren(new Option('选择抓包网卡', ''));
        data.interfaces.forEach(function(item) {
            select.add(new Option(item.name, item.id));
            if (!previous && /VirtualBox Host-Only/i.test(item.name)) previous = item.id;
        });
        select.value = previous;
    } catch (error) { document.getElementById('trafficStatus').textContent = error.message; }
}

async function toggleTraffic() {
    if (trafficBusy) return;
    trafficBusy = true;
    var button = document.getElementById('trafficButton');
    button.disabled = true;
    try {
        var response = await fetch(trafficRunning ? '/api/traffic/stop' : '/api/traffic/start', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({interface: document.getElementById('trafficInterface').value})
        });
        var data = await response.json();
        if (!response.ok) throw new Error(data.error || '操作失败');
        await refreshTrafficStatus();
    } catch (error) { document.getElementById('trafficStatus').textContent = error.message; }
    finally { trafficBusy = false; button.disabled = false; }
}

async function refreshTrafficStatus() {
    try {
        var response = await fetch('/api/traffic/status', {cache: 'no-store'});
        if (!response.ok) throw new Error('监控状态不可用');
        var data = await response.json();
        trafficRunning = data.running;
        document.getElementById('trafficButton').textContent = trafficRunning ? '停止监控' : '开始监控';
        document.getElementById('trafficStatus').textContent = data.message;
        document.querySelectorAll('.graph-traffic').forEach(function(label) {
            var rate = data.rates[label.getAttribute('data-link-id')];
            label.textContent = rate ? rate.pps.toFixed(1) + ' 包/秒 · ' + (rate.bps / 1000).toFixed(1) + ' kbit/s' : '';
        });
        document.querySelectorAll('.graph-link').forEach(function(link) {
            var rate = data.rates[link.getAttribute('data-link-id')];
            link.classList.toggle('traffic-active', !!rate && rate.pps > 0);
        });
    } catch (error) {
        trafficRunning = false;
        document.getElementById('trafficButton').textContent = '开始监控';
        document.getElementById('trafficStatus').textContent = '监控服务不可用';
    }
}

async function pollTrafficStatus() {
    await refreshTrafficStatus();
    await loadPackets();
    setTimeout(pollTrafficStatus, 1500);
}

async function loadPackets() {
    try {
        var response = await fetch('/api/traffic/packets', {cache: 'no-store'});
        if (!response.ok) throw new Error('无法读取数据包');
        packetData = (await response.json()).packets || [];
        renderPackets();
    } catch (error) { document.getElementById('packetEmpty').textContent = '数据包读取失败'; document.getElementById('packetEmpty').hidden = false; }
}

function renderPackets() {
    var rows = document.getElementById('packetRows'), empty = document.getElementById('packetEmpty');
    var filter = document.getElementById('packetProtocol').value;
    rows.replaceChildren();
    var packets = packetData.filter(function(packet) { return !filter || packet.protocol === filter; }).slice().reverse();
    empty.hidden = packets.length > 0;
    empty.textContent = packetData.length ? '没有符合条件的数据包' : '开始监控后显示数据包';
    packets.forEach(function(packet) {
        var row = document.createElement('tr');
        var date = new Date(Number(packet.timestamp) * 1000);
        var values = [Number.isFinite(date.getTime()) ? date.toLocaleTimeString() : '—', packetLinks[packet.link_id] || packet.link_id,
                      packet.protocol, packet.source || packet.source_mac || '—', packet.destination || packet.destination_mac || '—',
                      packet.frame_length + ' B', packet.detail || '—'];
        values.forEach(function(value) { var cell = document.createElement('td'); cell.textContent = value; row.appendChild(cell); });
        row.title = '外层 UDP: ' + packet.outer_source + ':' + packet.outer_source_port + ' → ' + packet.outer_destination + ':' + packet.outer_destination_port + (packet.vlan !== undefined ? ' | VLAN ' + packet.vlan : '') + (packet.ttl !== undefined ? ' | TTL ' + packet.ttl : '');
        rows.appendChild(row);
    });
}

async function clearPackets() {
    try {
        var response = await fetch('/api/traffic/packets', {method: 'DELETE'});
        if (!response.ok) throw new Error('清空失败');
        packetData = [];
        renderPackets();
    } catch (error) { alert('清空数据包失败：' + error.message); }
}

function analyzeTopo(type) {
    var typeLabel = {
        "markdown": "完整报告",
        "summary": "文本摘要",
        "mermaid": "Mermaid拓扑图"
    };

    var outputArea = document.getElementById("topoOutput");
    var outputContent = document.getElementById("topoOutputContent");
    outputContent.textContent = "正在生成" + (typeLabel[type] || type) + "...";
    outputArea.style.display = "block";
    outputContent.scrollTop = 0;

    fetch("/api/topo/analyze?type=" + type)
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.error) {
            outputContent.textContent = "错误: " + data.error;
            return;
        }
        outputContent.textContent = data.content;
        outputContent.scrollTop = 0;
    })
    .catch(function(e) {
        outputContent.textContent = "请求失败: " + e.message;
    });
}

async function checkMcpStatus() {
    var dot = document.getElementById("mcpStatusDot");
    var label = document.getElementById("mcpStatusText");
    var controller = new AbortController();
    var timeout = setTimeout(function() { controller.abort(); }, 5000);
    try {
        var response = await fetch("/api/mcp/status", { cache: "no-store", signal: controller.signal });
        if (!response.ok) throw new Error("status request failed");
        var data = await response.json();
        dot.classList.toggle("active", data.online === true);
        label.textContent = data.online === true ? "在线" : "离线";
    } catch (e) {
        dot.classList.remove("active");
        label.textContent = "离线";
    } finally {
        clearTimeout(timeout);
        setTimeout(checkMcpStatus, 10000);
    }
}

document.addEventListener("DOMContentLoaded", function() {
    renderHistory();
    checkMcpStatus();
    loadTopologyGraph();
    loadTrafficInterfaces();
    pollTrafficStatus();
});
