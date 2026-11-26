let state = {
    activeScanId: null,
    sessions: {},
    activeSessionId: null,
    connectedDevices: {},
    openPorts: [],
    isScanning: false,
    scanIp: null,
    topologyNames: {},
    endpointNames: {},
    completionDevices: [],
    completionLinks: [],
    eveDevices: null,
    deviceSource: 'topology'
};
let topologyFormat = null;
let eveMappingGraph = null;
let eveCaptureInterfaces = [];
let currentTopologyRevision = null;
let trafficRunning = false;
let trafficBusy = false;
let trafficError = '';
let packetData = [];
let packetLinks = {};
let traceEdges = {};
let traceSelection = null;
let traceAnimation = null;
let graphFit = null;
let graphBox = null;
let graphSignature = null;
let chatHistory = [];
let chatPending = null;
let chatBusy = false;

function initTheme() {
    var button = document.getElementById('themeToggle');
    var theme = 'dark';
    try { theme = localStorage.getItem('ensp_theme') === 'light' ? 'light' : 'dark'; } catch (_) {}
    function apply(value) {
        document.documentElement.dataset.theme = value;
        button.textContent = value === 'dark' ? '浅色主题' : '深色主题';
        button.setAttribute('aria-label', value === 'dark' ? '切换到浅色主题' : '切换到深色主题');
    }
    apply(theme);
    button.addEventListener('click', function() {
        theme = theme === 'dark' ? 'light' : 'dark';
        apply(theme);
        try { localStorage.setItem('ensp_theme', theme); } catch (_) {}
    });
}

function initChatResize() {
    var handle = document.getElementById('chatResize');
    var panel = document.getElementById('chatView');
    var width = 360;
    try { var saved = Number(localStorage.getItem('ensp_chat_width')); if (Number.isFinite(saved) && saved >= 280 && saved <= 900) width = saved; } catch (_) {}
    function apply(value) {
        width = Math.max(280, Math.min(900, Math.round(value)));
        panel.style.setProperty('--chat-width', width + 'px');
        handle.setAttribute('aria-valuenow', String(width));
        handle.setAttribute('aria-valuetext', width + ' 像素');
    }
    function save() { try { localStorage.setItem('ensp_chat_width', String(width)); } catch (_) {} }
    apply(width);
    var drag = null;
    handle.addEventListener('pointerdown', function(event) {
        if (event.button !== 0) return;
        drag = { id: event.pointerId, startX: event.clientX, startWidth: width };
        handle.setPointerCapture(event.pointerId);
        document.body.classList.add('resizing-chat');
        event.preventDefault();
    });
    handle.addEventListener('pointermove', function(event) {
        if (drag && event.pointerId === drag.id) apply(drag.startWidth + drag.startX - event.clientX);
    });
    function finish(event) {
        if (!drag || event.pointerId !== drag.id) return;
        drag = null;
        document.body.classList.remove('resizing-chat');
        save();
    }
    handle.addEventListener('pointerup', finish);
    handle.addEventListener('pointercancel', finish);
    handle.addEventListener('lostpointercapture', finish);
    handle.addEventListener('keydown', function(event) {
        if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
        apply(width + (event.key === 'ArrowLeft' ? 20 : -20));
        save();
        event.preventDefault();
    });
}

var chatSettingsCurrentBase = '';
var chatSettingsHasKey = false;
var DEEPSEEK_BASE = 'https://api.deepseek.com';

function applyChatTemplate() {
    var model = document.getElementById('aiTemplate').value;
    if (model !== 'deepseek-flash' && model !== 'deepseek-v4-pro') return;
    document.getElementById('aiBaseUrl').value = DEEPSEEK_BASE;
    document.getElementById('aiModel').value = model;
    document.getElementById('aiSettingsStatus').textContent = '已填入 DeepSeek 模板，请检查 API 密钥并保存';
}

function toggleChatSettings() {
    var form = document.getElementById('chatSettings');
    form.hidden = !form.hidden;
    document.getElementById('chatSettingsToggle').setAttribute('aria-expanded', String(!form.hidden));
    if (!form.hidden) loadChatSettings();
}

async function loadChatSettings() {
    var status = document.getElementById('aiSettingsStatus');
    try {
        var response = await fetch('/api/chat/config', {cache: 'no-store'});
        var data = await response.json();
        if (!response.ok) throw new Error(data.error || '读取失败');
        document.getElementById('aiBaseUrl').value = data.base_url;
        document.getElementById('aiModel').value = data.model;
        document.getElementById('aiProxyMode').value = data.proxy_mode || 'system';
        chatSettingsCurrentBase = data.base_url.replace(/\/$/, '');
        chatSettingsHasKey = data.has_api_key;
        document.getElementById('aiTemplate').value = (chatSettingsCurrentBase === DEEPSEEK_BASE || chatSettingsCurrentBase === DEEPSEEK_BASE + '/v1') && ['deepseek-flash', 'deepseek-v4-pro'].includes(data.model) ? data.model : 'custom';
        document.getElementById('aiApiKey').value = '';
        document.getElementById('aiClearKey').checked = false;
        status.textContent = data.has_api_key ? '已设置密钥' : '未设置密钥';
    } catch (error) { status.textContent = error.message; }
}

async function saveChatSettings(event) {
    event.preventDefault();
    var button = document.getElementById('aiSave'), status = document.getElementById('aiSettingsStatus');
    var nextBase = document.getElementById('aiBaseUrl').value.trim().replace(/\/$/, '');
    if (['deepseek-flash', 'deepseek-v4-pro'].includes(document.getElementById('aiTemplate').value) && nextBase === DEEPSEEK_BASE &&
        chatSettingsHasKey && ![DEEPSEEK_BASE, DEEPSEEK_BASE + '/v1'].includes(chatSettingsCurrentBase) &&
        !document.getElementById('aiApiKey').value.trim() && !document.getElementById('aiClearKey').checked) {
        status.textContent = '切换到 DeepSeek 请填写 DeepSeek 密钥，或勾选清除旧密钥';
        return;
    }
    button.disabled = true;
    try {
        var response = await fetch('/api/chat/config', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
            base_url: document.getElementById('aiBaseUrl').value,
            model: document.getElementById('aiModel').value,
            proxy_mode: document.getElementById('aiProxyMode').value,
            api_key: document.getElementById('aiApiKey').value,
            clear_api_key: document.getElementById('aiClearKey').checked
        })});
        var data = await response.json();
        if (!response.ok) throw new Error(data.error || '保存失败');
        document.getElementById('aiApiKey').value = '';
        document.getElementById('aiClearKey').checked = false;
        chatSettingsCurrentBase = data.base_url.replace(/\/$/, '');
        chatSettingsHasKey = data.has_api_key;
        if (chatPending) {
            chatPending = null;
            document.getElementById('chatApproval').hidden = true;
            document.getElementById('chatSend').disabled = false;
            appendChat('assistant', 'AI 设置已更改，待确认的工具调用已取消，请重新发送请求。');
            chatHistory.push({role: 'assistant', content: 'AI 设置已更改，待确认的工具调用已取消。'});
        }
        status.textContent = data.has_api_key ? '已保存，密钥已设置' : '已保存，未设置密钥';
    } catch (error) { status.textContent = error.message; }
    finally { button.disabled = false; }
}

async function testChatConnection() {
    var button = document.getElementById('aiTest'), status = document.getElementById('aiSettingsStatus');
    button.disabled = true;
    status.textContent = '正在测试已保存的设置，不调用设备工具…';
    try {
        var response = await fetch('/api/chat/test', {method: 'POST'});
        var data = await response.json();
        if (!response.ok) throw new Error(data.error || '连接测试失败');
        status.textContent = data.message;
    } catch (error) { status.textContent = error.message; }
    finally { button.disabled = false; }
}

function appendChat(role, text) {
    var node = document.createElement('div');
    node.className = 'chat-message ' + role;
    node.textContent = (role === 'user' ? '你：' : role === 'tool' ? 'MCP：' : 'AI：') + text;
    var container = document.getElementById('chatMessages');
    container.appendChild(node);
    container.scrollTop = container.scrollHeight;
}

var CHAT_STORAGE_KEY = 'learn_to_ensp_saved_chat_v1';

function exportChatConversation() {
    var status = document.getElementById('chatSaveStatus');
    if (chatBusy || chatPending) { status.textContent = '请先完成当前请求或处理待确认操作'; return; }
    var messages = Array.from(document.querySelectorAll('#chatMessages .chat-message'));
    if (!messages.length) { status.textContent = '当前没有对话可导出'; return; }
    try {
        var content = 'Learn to DataNetworking AI 对话\n导出时间：' + new Date().toLocaleString() + '\n\n' +
            messages.map(function(node) { return node.textContent; }).join('\n\n');
        var url = URL.createObjectURL(new Blob(['\ufeff', content], {type: 'text/plain;charset=utf-8'}));
        var link = document.createElement('a'); link.href = url; link.download = 'Learn-to-DataNetworking-AI对话.txt';
        document.body.appendChild(link); link.click(); link.remove();
        setTimeout(function() { URL.revokeObjectURL(url); }, 1000);
        status.textContent = '对话已导出，文件可能包含设备信息';
    } catch (error) { status.textContent = '导出失败，请检查浏览器下载设置'; }
}

function saveChatConversation() {
    var status = document.getElementById('chatSaveStatus');
    if (chatBusy || chatPending) { status.textContent = '请先完成当前请求或处理待确认操作'; return; }
    var nodes = Array.from(document.querySelectorAll('#chatMessages .chat-message'));
    if (!nodes.length) { status.textContent = '当前没有对话可保存'; return; }
    var entries = nodes.map(function(node) {
        return {role: node.classList.contains('user') ? 'user' : node.classList.contains('tool') ? 'tool' : 'assistant', text: node.textContent};
    });
    try {
        localStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify({version: 1, messages: entries, history: chatHistory, saved_at: new Date().toISOString()}));
        status.textContent = '已保存到当前浏览器';
    } catch (error) { status.textContent = '保存失败：浏览器存储不可用或空间不足'; }
}

function restoreChatConversation() {
    var status = document.getElementById('chatSaveStatus');
    if (chatBusy || chatPending) { status.textContent = '请先完成当前请求或处理待确认操作'; return; }
    try {
        var raw = localStorage.getItem(CHAT_STORAGE_KEY);
        if (!raw) { status.textContent = '没有已保存的对话'; return; }
        var saved = JSON.parse(raw);
        if (!saved || saved.version !== 1 || !Array.isArray(saved.messages) || !Array.isArray(saved.history) ||
            !saved.messages.every(function(m) { return m && ['user', 'assistant', 'tool'].includes(m.role) && typeof m.text === 'string'; }) ||
            saved.history.length > 30 || !saved.history.every(function(m) { return m && ['user', 'assistant'].includes(m.role) && typeof m.content === 'string'; })) {
            throw new Error('format');
        }
        if (document.getElementById('chatMessages').children.length && !confirm('恢复已保存的对话会替换当前页面中的对话，是否继续？')) return;
        var container = document.getElementById('chatMessages');
        container.replaceChildren();
        saved.messages.forEach(function(message) {
            var node = document.createElement('div'); node.className = 'chat-message ' + message.role;
            node.textContent = message.text; container.appendChild(node);
        });
        chatHistory = saved.history.map(function(message) { return {role: message.role, content: message.content}; });
        container.scrollTop = container.scrollHeight;
        status.textContent = '已恢复对话；设备会话和待确认操作不会恢复';
    } catch (error) { status.textContent = '无法恢复对话：保存内容无效或浏览器存储不可用'; }
}

function deleteSavedChatConversation() {
    var status = document.getElementById('chatSaveStatus');
    try {
        if (!localStorage.getItem(CHAT_STORAGE_KEY)) { status.textContent = '没有已保存的对话'; return; }
        if (!confirm('确定删除浏览器中保存的 AI 对话吗？')) return;
        localStorage.removeItem(CHAT_STORAGE_KEY);
        status.textContent = '已删除保存的对话';
    } catch (error) { status.textContent = '删除失败：浏览器存储不可用'; }
}

async function chatRequest(url, body) {
    chatBusy = true;
    document.getElementById('chatSend').disabled = true;
    try {
        var response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
        var data = await response.json();
        if (!response.ok) throw new Error(data.error || '请求失败');
        (data.steps || []).forEach(function(step) { appendChat('tool', step.name + ' ' + JSON.stringify(step.arguments) + '\n' + step.result); });
        if (data.pending) {
            chatPending = data.pending;
            document.getElementById('chatApprovalText').textContent = '确认执行 MCP 工具：' + data.pending.name + '\n' + JSON.stringify(data.pending.arguments, null, 2);
            document.getElementById('chatApproval').hidden = false;
        } else {
            chatPending = null;
            document.getElementById('chatApproval').hidden = true;
            appendChat('assistant', data.reply || '无回复');
            chatHistory.push({role: 'assistant', content: data.reply || '无回复'});
        }
    } catch (error) {
        appendChat('assistant', '请求失败：' + error.message + '。如果刚才执行过工具，请先检查设备状态再重试。');
        chatPending = null;
        document.getElementById('chatApproval').hidden = true;
        chatHistory.push({role: 'assistant', content: '请求未完成，请检查设备状态。'});
    } finally {
        chatBusy = false;
        document.getElementById('chatSend').disabled = !!chatPending;
    }
}

function confirmChatTool(allow) {
    if (chatBusy || !chatPending) return;
    var token = chatPending.token;
    chatPending = null;
    document.getElementById('chatApproval').hidden = true;
    chatRequest('/api/chat/confirm', {token: token, allow: allow});
}

function submitChat(event) {
    event.preventDefault();
    if (chatBusy || chatPending) return;
    var input = document.getElementById('chatInput'), message = input.value.trim();
    if (!message) return;
    input.value = '';
    appendChat('user', message);
    chatHistory.push({role: 'user', content: message});
    chatHistory = chatHistory.slice(-29);
    chatRequest('/api/chat', {messages: chatHistory, auto_execute: document.getElementById('chatAutoExecute').checked});
}

function switchSidebarTab(name, focusTab) {
    if (!['scan', 'device', 'topo'].includes(name)) return;
    ['scan', 'device', 'topo'].forEach(function(key) {
        var active = key === name, tab = document.getElementById(key + 'Tab');
        tab.classList.toggle('active', active);
        tab.setAttribute('aria-selected', String(active));
        tab.tabIndex = active ? 0 : -1;
        document.getElementById(key + 'Panel').hidden = !active;
    });
    if (focusTab) document.getElementById(name + 'Tab').focus();
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
    state.deviceSource = 'scan';
    document.getElementById('deviceSource').value = 'scan';
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
    if (state.eveDevices && state.deviceSource === 'topology') {
        container.replaceChildren();
        count.textContent = state.eveDevices.length;
        state.eveDevices.forEach(function(device) {
            var item = document.createElement('div'); item.className = 'device-item';
            var label = document.createElement('span'); label.className = 'device-port';
            var port = Number(device.com_port), host = device.console_host;
            label.textContent = device.name + (port ? '（' + port + '）' : '（控制台不可用）');
            label.title = host ? host + ':' + port : device.console_error || '请接入服务器并检查控制台类型';
            var sessionId = Object.keys(state.sessions).find(function(id) { var s = state.sessions[id]; return s.ip === host && Number(s.port) === port; });
            var button = document.createElement('button'); button.className = 'device-btn ' + (sessionId ? 'disconnect' : 'connect');
            button.textContent = sessionId ? '断开' : '连接'; button.disabled = !host || !port;
            button.onclick = function() { if (sessionId) disconnectSession(sessionId, port); else connectToDevice(port, host); };
            item.append(label, button); container.appendChild(item);
        });
        return;
    }

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

function connectToDevice(port, host) {
    var ip = host || document.getElementById("scanIp").value.trim();
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
        if (!host) state.connectedDevices[port] = data.session_id;
        renderDeviceList();
        switchToSession(data.session_id);
    })
    .catch(function(e) { appendToSession(null, "[错误] " + e.message, "error"); });
}

function sessionLabel(ip, port) {
    if (state.endpointNames[ip + ':' + port]) return state.endpointNames[ip + ':' + port] + '（' + port + '）';
    var local = ['127.0.0.1', 'localhost', '::1'].includes(String(ip).toLowerCase());
    var name = local ? state.topologyNames[Number(port)] : null;
    return name ? name + '（' + port + '）' : ip + ':' + port;
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
    prompt.textContent = sessionLabel(ip, port) + ' >';
    inputBar.appendChild(prompt);

    var input = document.createElement("input");
    input.type = "text";
    input.className = "terminal-cmd";
    input.placeholder = "输入命令 · Tab 补全 · ↑↓候选/历史 · 回车发送";
    input.setAttribute("data-session", sessionId);
    inputBar.appendChild(input);
    panel.appendChild(inputBar);

    document.getElementById("terminalContent").appendChild(panel);

    state.sessions[sessionId] = {
        ip: ip,
        port: port,
        outputEl: output,
        inputEl: input,
        promptEl: prompt,
        panelEl: panel
    };
    state.sessions[sessionId].completion = CommandCompletion.attach(input, function(history) {
        return terminalCommandCatalog(ip, port, history);
    }, function() { handleTerminalKey(sessionId); }, sessionId);

    if (initialOutput) {
        appendToSession(sessionId, initialOutput.trim(), "output");
    }
    appendToSession(sessionId, "[连接] 已连接到 " + sessionLabel(ip, port) + " (会话: " + sessionId + ")", "info");

    startPolling(sessionId);
    renderTabs();
}

function disconnectDevice(port) {
    var sessionId = state.connectedDevices[port];
    if (!sessionId) return;
    disconnectSession(sessionId, port);
}

function disconnectSession(sessionId, port) {
    fetch("/api/telnet/" + sessionId + "/disconnect", { method: "POST" })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        stopPolling(sessionId);
        if (state.connectedDevices[port] === sessionId) delete state.connectedDevices[port];

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
    tabs.replaceChildren();
    Object.keys(state.sessions).forEach(function(sid) {
        var s = state.sessions[sid];
        var label = sessionLabel(s.ip, s.port);
        var tab = document.createElement('div');
        tab.className = 'terminal-tab' + (sid === state.activeSessionId ? ' active' : '');
        tab.title = s.ip + ':' + s.port;
        tab.onclick = function() { switchToSession(sid); };
        var indicator = document.createElement('span'); indicator.className = 'tab-indicator';
        var name = document.createElement('span'); name.textContent = label;
        var close = document.createElement('span'); close.className = 'tab-close'; close.textContent = '×';
        close.onclick = function(event) { event.stopPropagation(); closeTab(sid, s.port); };
        tab.append(indicator, name, close); tabs.appendChild(tab);
        if (s.promptEl) s.promptEl.textContent = label + ' >';
    });
}

function closeTab(sessionId, port) {
    disconnectSession(sessionId, parseInt(port));
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

function terminalCommandCatalog(ip, port, history) {
    var matches = state.completionDevices.filter(function(device) {
        var host = device.console_host || '127.0.0.1';
        var local = ['127.0.0.1', 'localhost', '::1'];
        return Number(device.com_port) === Number(port) && (host === ip || local.includes(host) && local.includes(ip));
    });
    var device = matches.length === 1 ? matches[0] : null, interfaces = [];
    if (device) state.completionLinks.forEach(function(link) {
        var side = link.source_id === device.id ? 'source' : link.target_id === device.id ? 'target' : null;
        if (!side) return;
        link.interface_pairs.forEach(function(pair) {
            ['manual', 'verified', 'restored', 'declared'].forEach(function(kind) {
                var name = (pair[kind] || {})[side + '_interface'];
                if (name && !interfaces.includes(name)) interfaces.push(name);
            });
        });
    });
    return CommandCompletion.catalog(device && device.model, interfaces, history);
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

function updateEveKind() {
    var pnet = document.getElementById('eveKind').value === 'pnet';
    ['eveLabGroup', 'eveUserGroup', 'evePasswordGroup'].forEach(function(id) { document.getElementById(id).hidden = pnet; });
    document.getElementById('eveCookieGroup').hidden = !pnet;
}

async function saveEveSettings() {
    var status = document.getElementById('eveStatus');
    status.textContent = '正在认证并读取服务器…';
    try {
        var response = await fetch('/api/eve/config', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
            base_url: document.getElementById('eveBase').value, kind: document.getElementById('eveKind').value,
            lab_path: document.getElementById('eveLab').value, username: document.getElementById('eveUser').value,
            password: document.getElementById('evePassword').value, cookie: document.getElementById('eveCookie').value
        })});
        var data = await response.json();
        if (!response.ok) throw new Error(data.error || '认证失败');
        document.getElementById('evePassword').value = ''; document.getElementById('eveCookie').value = '';
        status.textContent = '服务器认证成功';
        await loadTopologyGraph();
    } catch (error) { status.textContent = error.message; }
}

async function saveEveCaptureSettings() {
    var status = document.getElementById('eveCaptureStatus');
    status.textContent = '正在连接 SSH 并读取网卡…';
    try {
        var host = document.getElementById('eveSshHost').value.trim() || new URL(document.getElementById('eveBase').value).hostname;
        var response = await fetch('/api/eve/capture/config', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
            host: host, port: Number(document.getElementById('eveSshPort').value), username: document.getElementById('eveSshUser').value,
            password: document.getElementById('eveSshPassword').value, fingerprint: document.getElementById('eveSshFingerprint').value
        })});
        var data = await response.json();
        if (!response.ok) throw new Error(data.error || 'SSH 连接失败');
        document.getElementById('eveSshPassword').value = '';
        eveCaptureInterfaces = data.interfaces || [];
        renderEveCaptureMappings();
        status.textContent = 'SSH 已连接，读取到 ' + eveCaptureInterfaces.length + ' 个以太网接口；请在拓扑图下配置链路映射。';
    } catch (error) { status.textContent = error.message; }
}

async function loadEveSettings() {
    try {
        var response = await fetch('/api/eve/config', {cache: 'no-store'});
        if (!response.ok) return;
        var data = await response.json();
        if (data.base_url) document.getElementById('eveBase').value = data.base_url;
        if (data.authenticated) document.getElementById('eveKind').value = data.kind;
        document.getElementById('eveLab').value = data.lab_path || '';
        document.getElementById('eveUser').value = data.username || '';
        updateEveKind();
    } catch (_) { /* Graph loading reports missing runtime configuration. */ }
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
    document.getElementById("terminalView").hidden = view !== "terminal";
    document.getElementById("topologyView").hidden = !isTopology;
    ["terminal", "topology"].forEach(function(name) {
        var tab = document.getElementById(name + "ViewTab");
        var active = name === view;
        tab.classList.toggle("active", active);
        tab.setAttribute("aria-selected", String(active));
    });
    if (isTopology) loadTopologyGraph();
}

function loadTopologyGraph(throwOnError) {
    return fetch("/api/topo/graph", { cache: "no-store" })
    .then(function(r) { if (!r.ok) throw new Error("无法获取拓扑"); return r.json(); })
    .then(function(data) {
        renderTopologyGraph(data);
        if (!data.loaded) { resetTopologyDisplay(); if (throwOnError) throw new Error('拓扑已删除，请重新上传'); return; }
        currentTopologyRevision = data.revision;
        state.completionDevices = data.devices || [];
        state.completionLinks = data.connections || [];
        var formatChanged = topologyFormat !== data.format;
        topologyFormat = data.format;
        eveMappingGraph = data.format === 'eve-ng' ? data : null;
        document.getElementById('eveCaptureMappings').hidden = data.format !== 'eve-ng';
        document.getElementById('trafficInterface').hidden = data.format === 'eve-ng';
        if (formatChanged) {
            state.deviceSource = 'topology';
            document.getElementById('deviceSource').value = 'topology';
            eveCaptureInterfaces = [];
            loadTrafficInterfaces();
        }
        renderEveCaptureMappings();
        state.eveDevices = data.format === 'eve-ng' ? data.devices.filter(function(d) { return d.category !== '网络'; }) : null;
        state.endpointNames = {};
        (data.devices || []).forEach(function(d) { if (d.console_host && Number(d.com_port)) state.endpointNames[d.console_host + ':' + d.com_port] = d.name; });
        if (data.format === 'eve-ng') document.getElementById('eveStatus').textContent = data.runtime_error || '已从服务器读取控制台地址';
        state.topologyNames = {};
        var duplicatePorts = new Set();
        (data.devices || []).forEach(function(device) {
            if (data.format === 'eve-ng' || !/^\d+$/.test(String(device.com_port)) || !device.name) return;
            var port = Number(device.com_port);
            if (port < 1 || port > 65535 || duplicatePorts.has(port)) return;
            if (Object.prototype.hasOwnProperty.call(state.topologyNames, port)) {
                delete state.topologyNames[port]; duplicatePorts.add(port);
            } else state.topologyNames[port] = device.name;
        });
        renderDeviceList();
        renderTabs();
        document.getElementById("topoFilename").textContent = data.filename;
        document.getElementById("topoStats").textContent = "设备: " + data.summary.total_devices + (data.summary.total_networks ? ' | 网络: ' + data.summary.total_networks : '') + " | 链路: " + data.summary.total_connections + " | VLAN: " + data.summary.vlan_count + " | 子网: " + data.summary.subnet_count;
        document.getElementById("topoStatus").textContent = "已加载";
        document.getElementById("topoStatus").classList.add("loaded");
        document.getElementById("topoInfo").style.display = "block";
        document.getElementById("topoActions").style.display = "block";
        document.getElementById("clearTopologyButton").hidden = false;
    })
    .catch(function(error) { document.getElementById("graphEmpty").textContent = "无法加载拓扑，请刷新后重试"; document.getElementById("graphEmpty").hidden = false; if (throwOnError) throw error; });
}

function resetTopologyDisplay() {
    currentTopologyRevision = null;
    topologyFormat = null;
    eveMappingGraph = null;
    document.getElementById('eveCaptureMappings').hidden = true;
    document.getElementById('trafficInterface').hidden = false;
    loadTrafficInterfaces();
    document.getElementById('interfaceArchiveStatus').textContent = '';
    stopTrace();
    traceSelection = null;
    showPacketAnalysis(null);
    packetData = [];
    packetLinks = {};
    traceEdges = {};
    renderPackets();
    state.topologyNames = {};
    state.completionDevices = [];
    state.completionLinks = [];
    state.eveDevices = null;
    state.endpointNames = {};
    renderDeviceList();
    renderTabs();
    document.getElementById('topoFilename').textContent = '';
    document.getElementById('topoStats').textContent = '';
    document.getElementById('topoConnectStatus').textContent = '';
    document.getElementById('topoConnectLog').hidden = true;
    document.getElementById('topoVerifyStatus').textContent = '';
    document.getElementById('topoVerifyLogContent').textContent = '';
    document.getElementById('topoVerifyLog').hidden = true;
    document.getElementById('topoVerifyProgress').hidden = true;
    document.getElementById('topoStatus').textContent = '未加载';
    document.getElementById('topoStatus').classList.remove('loaded');
    document.getElementById('topoInfo').style.display = 'none';
    document.getElementById('topoActions').style.display = 'none';
    document.getElementById('topoFileInput').value = '';
    document.getElementById('clearTopologyButton').hidden = true;
    renderTopologyGraph({loaded: false});
}

var TOPOLOGY_ARCHIVE_PREFIX = 'learn_to_ensp_interfaces_v1:';

async function readTopologyArchive() {
    var response = await fetch('/api/topo/interfaces-archive', {cache: 'no-store'});
    var data = await response.json();
    if (!response.ok) throw new Error(data.error || '读取互联信息失败');
    return data;
}

async function saveTopologyInterfaces() {
    var status = document.getElementById('interfaceArchiveStatus');
    try {
        var archive = await readTopologyArchive();
        if (!archive.links.length) throw new Error('没有可保存的链路接口信息，请先验证或手动填写');
        localStorage.setItem(TOPOLOGY_ARCHIVE_PREFIX + archive.fingerprint, JSON.stringify(archive));
        status.textContent = '已保存 ' + archive.links.length + ' 条链路到当前浏览器；下次上传相同拓扑后可恢复。';
    } catch (error) { status.textContent = error.name === 'QuotaExceededError' ? '保存失败：浏览器存储空间不足' : error.message; }
}

async function restoreTopologyInterfaces() {
    var status = document.getElementById('interfaceArchiveStatus');
    try {
        var revision = currentTopologyRevision;
        var current = await readTopologyArchive();
        var saved = localStorage.getItem(TOPOLOGY_ARCHIVE_PREFIX + current.fingerprint);
        if (!saved) throw new Error('当前拓扑没有已保存的链路信息');
        var archive = JSON.parse(saved);
        var response = await fetch('/api/topo/interfaces-archive', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({revision: revision, archive: archive})});
        var data = await response.json();
        if (!response.ok) throw new Error(data.error || '恢复失败');
        await loadTopologyGraph(true);
        status.textContent = '已恢复 ' + data.restored_count + ' 条历史验证记录和 ' + data.manual_count + ' 条手动记录；历史记录未重新验证。';
    } catch (error) { status.textContent = error instanceof SyntaxError ? '保存的链路数据无效' : error.message; }
}

async function exportTopologyInterfaces() {
    var status = document.getElementById('interfaceArchiveStatus');
    try {
        var data = await readTopologyArchive();
        var url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'}));
        var link = document.createElement('a'); link.href = url; link.download = 'Learn-to-DataNetworking-互联信息.json';
        document.body.appendChild(link); link.click(); link.remove();
        setTimeout(function() { URL.revokeObjectURL(url); }, 1000);
        status.textContent = '已导出 ' + data.links.length + ' 条链路记录，请妥善保存文件。';
    } catch (error) { status.textContent = error.message; }
}

async function importTopologyInterfaces(event) {
    var input = event.target, file = input.files[0];
    var status = document.getElementById('interfaceArchiveStatus');
    if (!file) return;
    try {
        if (file.size > 2 * 1024 * 1024) throw new Error('文件不能超过 2 MB');
        var revision = currentTopologyRevision;
        var archive = JSON.parse(await file.text());
        var response = await fetch('/api/topo/interfaces-archive', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({revision: revision, archive: archive})});
        var data = await response.json();
        if (!response.ok) throw new Error(data.error || '导入失败');
        await loadTopologyGraph(true);
        status.textContent = '已导入 ' + data.restored_count + ' 条历史验证记录和 ' + data.manual_count + ' 条手动记录。历史记录未重新验证。';
    } catch (error) { status.textContent = error instanceof SyntaxError ? '文件不是有效的 JSON' : error.message; }
    finally { input.value = ''; }
}

async function connectTopologyDevices() {
    state.deviceSource = 'topology';
    document.getElementById('deviceSource').value = 'topology';
    var button = document.getElementById('topoConnectButton');
    var status = document.getElementById('topoConnectStatus');
    var log = document.getElementById('topoConnectLog');
    log.hidden = false;
    log.open = true;
    button.disabled = true;
    status.textContent = '正在连接拓扑设备…';
    try {
        var response = await fetch('/api/topo/connect', {method: 'POST'});
        var data = await response.json();
        if (!response.ok) throw new Error(data.error || '检测失败');
        var localList = !state.scanIp || ['127.0.0.1', 'localhost', '::1'].includes(state.scanIp.toLowerCase());
        if (localList && !state.scanIp && ['127.0.0.1', 'localhost', '::1'].includes(document.getElementById('scanIp').value.trim().toLowerCase())) state.scanIp = '127.0.0.1';
        data.devices.forEach(function(device) {
            if (!device.session_id) return;
            if (!state.sessions[device.session_id]) createSessionTab(device.session_id, device.ip || '127.0.0.1', device.port, '');
            if (!state.eveDevices && localList && state.scanIp && (!state.connectedDevices[device.port] || state.connectedDevices[device.port] === device.session_id)) {
                state.connectedDevices[device.port] = device.session_id;
                if (!state.openPorts.includes(device.port)) state.openPorts.push(device.port);
            }
        });
        state.openPorts.sort(function(a, b) { return a - b; });
        renderDeviceList();
        status.textContent = data.devices.map(function(device) {
            return device.name + '（' + device.port + '）：' + device.status;
        }).join('\n');
        loadTopologyGraph();
    } catch (error) { status.textContent = error.message + '；如已建立会话，可重新执行以复用会话。'; }
    finally { button.disabled = false; }
}

async function verifyTopologyInterfaces() {
    var button = document.getElementById('topoVerifyButton');
    var status = document.getElementById('topoVerifyStatus');
    var progress = document.getElementById('topoVerifyProgress');
    var log = document.getElementById('topoVerifyLog');
    var logContent = document.getElementById('topoVerifyLogContent');
    button.disabled = true;
    progress.hidden = false;
    progress.value = 0;
    log.hidden = true;
    logContent.textContent = '';
    status.textContent = '正在启动验证…';
    var lastRevision = 0;
    try {
        var response = await fetch('/api/topo/verify', {method: 'POST'});
        var data = await response.json();
        if (!response.ok) throw new Error(data.error || '无法启动验证');
        await loadTopologyGraph();
        while (true) {
            response = await fetch('/api/topo/verify/progress', {cache: 'no-store'});
            data = await response.json();
            if (!response.ok || data.state === 'error') throw new Error(data.error || data.message || '验证失败');
            progress.value = data.percent || 0;
            status.textContent = data.message + '（' + progress.value + '%）';
            if (data.state === 'running' && data.revision > lastRevision) {
                await loadTopologyGraph();
                lastRevision = data.revision;
            }
            if (data.state === 'done') {
                logContent.textContent = data.devices.map(function(device) {
                    return device.name + '（' + device.port + '）：' + device.status;
                }).join('\n') + '\n未确认的连线显示待验证；请检查端口 LLDP 和设备名称。配置未自动保存。';
                log.hidden = false;
                log.open = false;
                var summary = '已验证 ' + data.verified_count + ' 条连线';
                status.textContent = summary + '，正在更新互联信息…';
                await loadTopologyGraph(true);
                status.textContent = summary + '，互联信息已更新。';
                break;
            }
            await new Promise(function(resolve) { setTimeout(resolve, 1000); });
        }
    } catch (error) { status.textContent = error.message; }
    finally { button.disabled = false; }
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
    stopTrace();
    var svg = document.getElementById("topologyGraph"), empty = document.getElementById("graphEmpty");
    var stats = document.getElementById("graphStats");
    svg.replaceChildren();
    var devices = Array.isArray(data.devices) ? data.devices : [];
    var links = Array.isArray(data.connections) ? data.connections : [];
    renderConnections(devices, links, data.revision);
    packetLinks = {};
    traceEdges = {};
    var names = new Map(devices.map(function(device) { return [device.id, device.name]; }));
    links.forEach(function(link, index) {
        for (var i = 0; i < (Number(link.link_count) || 1); i++) {
            packetLinks[index + ':' + i] = (names.get(link.source_id) || '未知设备') + ' ↔ ' + (names.get(link.target_id) || '未知设备') + ((link.link_count > 1) ? ' #' + (i + 1) : '');
        }
    });
    renderPackets();
    if (!data.loaded || !devices.length) {
        graphFit = null; graphBox = null; graphSignature = null;
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
    var signature = JSON.stringify([data.filename, devices, links.map(function(link) { return [link.source_id, link.target_id, link.link_count]; })]);
    if (!graphBox || graphSignature !== signature) {
        graphFit = {x: minX, y: minY, width: maxX - minX, height: maxY - minY};
        graphBox = Object.assign({}, graphFit);
    }
    graphSignature = signature;
    applyTopologyView();
    var count = 0;
    links.forEach(function(link, linkIndex) {
        var source = positions.get(link.source_id), target = positions.get(link.target_id);
        if (!source || !target) return;
        var n = Math.max(1, Number(link.link_count) || 1), dx = target.x - source.x, dy = target.y - source.y, length = Math.hypot(dx, dy) || 1;
        for (var i = 0; i < n; i++) {
            var offset = (i - (n - 1) / 2) * 7, ox = -dy / length * offset, oy = dx / length * offset;
            var linkId = linkIndex + ":" + i;
            traceEdges[linkId] = {source: link.source_id, target: link.target_id, x1: source.x + ox, y1: source.y + oy, x2: target.x + ox, y2: target.y + oy};
            svg.appendChild(make("line", { x1: source.x + ox, y1: source.y + oy, x2: target.x + ox, y2: target.y + oy, class: "graph-link", "data-link-id": linkId }));
            var label = make("text", { x: (source.x + target.x) / 2 + ox, y: (source.y + target.y) / 2 + oy - 5, class: "graph-traffic", "data-link-id": linkId });
            svg.appendChild(label);
            count++;
        }
    });
    devices.forEach(function(device) {
        var p = positions.get(device.id), group = make("g", { class: "graph-node", transform: "translate(" + p.x + " " + p.y + ")" });
        var title = make("title"); title.textContent = (device.name || "设备") + (device.model ? " · " + device.model : ""); group.appendChild(title);
        var modelName = String(device.model || '').toUpperCase();
        var category = String(device.category || '');
        var router = /^AR\d/.test(modelName) || category.indexOf('路由器') !== -1;
        var isSwitch = /^S\d/.test(modelName) || category.indexOf('交换机') !== -1;
        if (router) {
            group.appendChild(make('circle', {cx: 0, cy: 0, r: 22, class: 'device-outline'}));
            group.appendChild(make('path', {d: 'M -14 -7 H 10 M 6 -11 L 10 -7 L 6 -3 M 14 7 H -10 M -6 3 L -10 7 L -6 11', class: 'device-mark'}));
        } else if (isSwitch) {
            group.appendChild(make('rect', {x: -26, y: -16, width: 52, height: 32, rx: 4, class: 'device-outline switch-outline'}));
            group.appendChild(make('path', {d: 'M -17 -5 H 13 M 9 -9 L 13 -5 L 9 -1 M 17 6 H -13 M -9 2 L -13 6 L -9 10', class: 'device-mark switch-mark'}));
        } else if (category === '网络') {
            group.appendChild(make('ellipse', {cx: 0, cy: 0, rx: 28, ry: 18, class: 'device-outline'}));
            var networkLabel = make('text', {x: 0, y: 5, class: 'graph-model'}); networkLabel.textContent = '网段'; group.appendChild(networkLabel);
        } else {
            group.appendChild(make('rect', {x: -22, y: -19, width: 44, height: 38, rx: 5, class: 'device-outline'}));
            var icon = make('text', {x: 0, y: 5, class: 'graph-model'}); icon.textContent = '设备'; group.appendChild(icon);
        }
        var name = make("text", { x: 0, y: 37 }); name.textContent = device.name || "设备"; group.appendChild(name);
        var model = make("text", { x: 0, y: 51, class: "graph-model" }); model.textContent = device.model || ""; group.appendChild(model);
        svg.appendChild(group);
    });
    var networkCount = devices.filter(function(device) { return device.category === '网络'; }).length;
    stats.textContent = (devices.length - networkCount) + ' 台设备' + (networkCount ? ' · ' + networkCount + ' 个网络' : '') + ' · ' + count + ' 条连线';
}

function renderConnections(devices, links, revision) {
    var container = document.getElementById('connectionList');
    var count = document.getElementById('connectionCount');
    var names = new Map(devices.map(function(device) { return [device.id, device.name || device.id]; }));
    container.replaceChildren();
    var total = 0;
    var verifiedItems = [], manualItems = [], candidateItems = [], pendingItems = [];
    links.forEach(function(link, linkIndex) {
        var pairs = Array.isArray(link.interface_pairs) ? link.interface_pairs : [];
        var editable = pairs.length > 0;
        if (!pairs.length) pairs = [{}];
        pairs.forEach(function(pair, pairIndex) {
            total++;
            var item = document.createElement('div'); item.className = 'connection-item';
            var heading = document.createElement('strong');
            heading.textContent = (names.get(link.source_id) || link.source_id || '未知设备') + ' ↔ ' + (names.get(link.target_id) || link.target_id || '未知设备');
            var detail = document.createElement('div');
            if (pair.manual) {
                detail.textContent = (pair.manual.source_interface || '未填写') + ' ↔ ' + (pair.manual.target_interface || '未填写') + '（手动填写，未经验证）';
                manualItems.push(item);
            } else if (pair.verified && pair.verified.source_interface && pair.verified.target_interface) {
                detail.textContent = pair.verified.source_interface + ' ↔ ' + pair.verified.target_interface + '（双向 LLDP 已验证）' + (pair.line_type ? ' · ' + pair.line_type : '');
                verifiedItems.push(item);
            } else if (pair.restored && pair.restored.source_interface && pair.restored.target_interface) {
                detail.textContent = pair.restored.source_interface + ' ↔ ' + pair.restored.target_interface + '（历史验证记录，当前未重新验证）';
                manualItems.push(item);
            } else if (pair.candidate && pair.candidate.source_interface && pair.candidate.target_interface) {
                detail.textContent = pair.candidate.source_interface + ' ↔ ' + pair.candidate.target_interface + '（双向 LLDP 候选；其他设备身份未确认）' + (pair.line_type ? ' · ' + pair.line_type : '');
                candidateItems.push(item);
            } else if (pair.declared && (pair.declared.source_interface || pair.declared.target_interface)) {
                detail.textContent = (pair.declared.source_interface || '网络') + ' ↔ ' + (pair.declared.target_interface || '网络') + '（拓扑文件声明，未经验证）';
                pendingItems.push(item);
            } else {
                detail.textContent = '待验证';
                pendingItems.push(item);
            }
            item.append(heading, detail);
            if (pair.manual && pair.verified) {
                var auto = document.createElement('div');
                auto.textContent = 'LLDP：' + pair.verified.source_interface + ' ↔ ' + pair.verified.target_interface + '（已验证）';
                item.appendChild(auto);
            }
            if (editable) {
                var editor = document.createElement('details'); editor.className = 'connection-editor';
                var summary = document.createElement('summary'); summary.textContent = pair.manual ? '编辑手动接口' : '手动填写接口';
                var form = document.createElement('form');
                function field(labelText, value) {
                    var label = document.createElement('label'); label.textContent = labelText;
                    var input = document.createElement('input'); input.type = 'text'; input.maxLength = 64;
                    input.placeholder = '例如 GE0/0/0'; input.value = value || '';
                    label.appendChild(input); form.appendChild(label); return input;
                }
                var source = field((names.get(link.source_id) || '起点') + ' 接口', pair.manual && pair.manual.source_interface);
                var target = field((names.get(link.target_id) || '终点') + ' 接口', pair.manual && pair.manual.target_interface);
                var actions = document.createElement('div'); actions.className = 'connection-editor-actions';
                var save = document.createElement('button'); save.type = 'submit'; save.textContent = '保存';
                var error = document.createElement('div'); error.className = 'connection-editor-error'; error.setAttribute('aria-live', 'polite');
                actions.appendChild(save);
                var payload = {revision: revision, link_index: linkIndex, pair_index: pairIndex, source_id: link.source_id, target_id: link.target_id};
                async function submitManual(method) {
                    save.disabled = true;
                    try {
                        var body = Object.assign({}, payload);
                        if (method === 'POST') { body.source_interface = source.value; body.target_interface = target.value; }
                        var response = await fetch('/api/topo/manual-interfaces', {method: method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
                        var data = await response.json();
                        if (!response.ok) throw new Error(data.error || '保存失败');
                        await loadTopologyGraph(true);
                    } catch (err) { error.textContent = err.message; save.disabled = false; }
                }
                form.addEventListener('submit', function(event) { event.preventDefault(); submitManual('POST'); });
                if (pair.manual) {
                    var clear = document.createElement('button'); clear.type = 'button'; clear.textContent = '清除手动填写';
                    clear.addEventListener('click', function() { submitManual('DELETE'); });
                    actions.appendChild(clear);
                }
                form.append(actions, error); editor.append(summary, form); item.appendChild(editor);
            }
        });
    });
    verifiedItems.concat(manualItems, candidateItems, pendingItems).forEach(function(item) { container.appendChild(item); });
    count.textContent = total ? '· ' + total + ' 条' : '';
    if (!total) {
        var empty = document.createElement('div'); empty.className = 'connections-empty';
        empty.textContent = devices.length ? '拓扑中没有连线' : '上传拓扑后显示连线';
        container.appendChild(empty);
    }
}

function applyTopologyView() {
    if (graphBox) document.getElementById('topologyGraph').setAttribute('viewBox', [graphBox.x, graphBox.y, graphBox.width, graphBox.height].join(' '));
}

function zoomTopology(factor, anchor) {
    if (!graphBox || !graphFit) return;
    var width = Math.max(graphFit.width / 8, Math.min(graphFit.width * 3, graphBox.width / factor));
    var scale = width / graphBox.width;
    var x = anchor ? anchor.x : graphBox.x + graphBox.width / 2;
    var y = anchor ? anchor.y : graphBox.y + graphBox.height / 2;
    graphBox = {x: x - (x - graphBox.x) * scale, y: y - (y - graphBox.y) * scale,
                width: width, height: graphBox.height * scale};
    applyTopologyView();
}

function resetTopologyZoom() {
    if (graphFit) { graphBox = Object.assign({}, graphFit); applyTopologyView(); }
}

function initTopologyInteraction() {
    var svg = document.getElementById('topologyGraph'), drag = null;
    function graphPoint(x, y) {
        var matrix = svg.getScreenCTM();
        if (!matrix) return null;
        var point = svg.createSVGPoint(); point.x = x; point.y = y;
        return point.matrixTransform(matrix.inverse());
    }
    svg.addEventListener('wheel', function(event) {
        if (!graphBox) return;
        var point = graphPoint(event.clientX, event.clientY);
        if (!point) return;
        event.preventDefault();
        zoomTopology(event.deltaY < 0 ? 1.15 : 1 / 1.15, point);
    }, {passive: false});
    svg.addEventListener('pointerdown', function(event) {
        if (!graphBox || event.button !== 0) return;
        drag = {id: event.pointerId, x: event.clientX, y: event.clientY};
        svg.setPointerCapture(event.pointerId); svg.classList.add('dragging'); event.preventDefault();
    });
    svg.addEventListener('pointermove', function(event) {
        if (!drag || drag.id !== event.pointerId || !graphBox) return;
        var from = graphPoint(drag.x, drag.y), to = graphPoint(event.clientX, event.clientY);
        if (!from || !to) return;
        graphBox.x -= to.x - from.x; graphBox.y -= to.y - from.y;
        drag.x = event.clientX; drag.y = event.clientY; applyTopologyView();
    });
    function endDrag(event) { if (drag && drag.id === event.pointerId) { drag = null; svg.classList.remove('dragging'); } }
    svg.addEventListener('pointerup', endDrag);
    svg.addEventListener('pointercancel', endDrag);
    svg.addEventListener('lostpointercapture', endDrag);
}

function renderEveCaptureMappings() {
    var container = document.getElementById('eveMappingRows');
    var old = {};
    if (container.dataset.revision === String(currentTopologyRevision)) container.querySelectorAll('select').forEach(function(s) { old[s.dataset.linkId] = s.value; });
    container.dataset.revision = String(currentTopologyRevision);
    container.replaceChildren();
    if (!eveMappingGraph) return;
    var devices = {};
    eveMappingGraph.devices.forEach(function(d) { devices[d.id] = d.name; });
    eveMappingGraph.connections.forEach(function(link, i) {
        link.interface_pairs.forEach(function(pair, j) {
            var key = i + ':' + j, row = document.createElement('label');
            row.className = 'eve-mapping-row';
            var name = document.createElement('span');
            var ports = pair.manual || pair.verified || pair.restored || pair.declared || {};
            name.textContent = devices[link.source_id] + ' ' + (ports.source_interface || '') + ' ↔ ' + devices[link.target_id] + ' ' + (ports.target_interface || '');
            var select = document.createElement('select'); select.dataset.linkId = key;
            select.setAttribute('aria-label', name.textContent + ' 抓包网卡');
            select.add(new Option('不监控此链路', ''));
            eveCaptureInterfaces.forEach(function(item) { select.add(new Option(item.name, item.id)); });
            if (old[key]) select.value = old[key];
            row.append(name, select); container.appendChild(row);
        });
    });
}

async function loadTrafficInterfaces() {
    try {
        var response = await fetch('/api/traffic/interfaces', { cache: 'no-store' });
        var data = await response.json();
        if (!response.ok) throw new Error(data.error || '无法获取网卡');
        if (data.remote) {
            eveCaptureInterfaces = data.interfaces;
            renderEveCaptureMappings();
            document.getElementById('eveCaptureStatus').textContent = '已读取服务器网卡，请选择链路映射。';
            return;
        }
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
    trafficError = '';
    var button = document.getElementById('trafficButton');
    button.disabled = true;
    try {
        var response = await fetch(trafficRunning ? '/api/traffic/stop' : '/api/traffic/start', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({interface: document.getElementById('trafficInterface').value, revision: currentTopologyRevision,
                mappings: Array.from(document.querySelectorAll('#eveMappingRows select')).filter(function(s) { return s.value; }).map(function(s) { return {link_id: s.dataset.linkId, interface: s.value}; })})
        });
        var data = await response.json();
        if (!response.ok) throw new Error(data.error || '操作失败');
        if (!trafficRunning) {
            if (topologyFormat === 'eve-ng') document.getElementById('eveCaptureMappings').open = false;
            traceSelection = null;
            packetData = [];
            stopTrace();
            showPacketAnalysis(null);
            renderPackets();
        }
        await refreshTrafficStatus();
    } catch (error) { trafficError = error.message; document.getElementById('trafficStatus').textContent = error.message; }
    finally { trafficBusy = false; button.disabled = false; }
}

async function refreshTrafficStatus() {
    try {
        var response = await fetch('/api/traffic/status', {cache: 'no-store'});
        if (!response.ok) throw new Error('监控状态不可用');
        var data = await response.json();
        trafficRunning = data.running;
        document.getElementById('trafficButton').textContent = trafficRunning ? '停止监控' : '开始监控';
        document.getElementById('trafficStatus').textContent = !trafficRunning && trafficError ? trafficError : data.message;
        document.querySelectorAll('.graph-traffic').forEach(function(label) {
            var rate = data.rates[label.getAttribute('data-link-id')];
            label.textContent = rate ? (rate.endpoints === 1 ? '单端 ' : '') + rate.pps.toFixed(1) + ' 包/秒 · ' + (rate.bps / 1000).toFixed(1) + ' kbit/s' : (trafficRunning ? '未覆盖' : '');
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
        if (traceSelection && packet.id === traceSelection.id) row.classList.add('trace-selected');
        row.onclick = function() { traceSelection = packet; showPacketAnalysis(packet); renderPackets(); replayTrace(); };
        var date = new Date(Number(packet.timestamp) * 1000);
        var values = [Number.isFinite(date.getTime()) ? date.toLocaleTimeString() : '—', packetLinks[packet.link_id] || packet.link_id,
                      packet.protocol, packet.source || packet.source_mac || '—', packet.destination || packet.destination_mac || '—',
                      packet.frame_length + ' B', packet.detail || '—'];
        values.forEach(function(value) { var cell = document.createElement('td'); cell.textContent = value; row.appendChild(cell); });
        row.title = (packet.capture_source === 'eve-ssh' ? '服务器抓包网卡：' + packet.capture_interface : '外层 UDP: ' + packet.outer_source + ':' + packet.outer_source_port + ' → ' + packet.outer_destination + ':' + packet.outer_destination_port) + (packet.vlan !== undefined ? ' | VLAN ' + packet.vlan : '') + (packet.ttl !== undefined ? ' | TTL ' + packet.ttl : '');
        rows.appendChild(row);
    });
}

function showPacketAnalysis(packet) {
    var panel = document.getElementById('packetAnalysis');
    if (!packet) {
        panel.textContent = '选择数据包查看自动分析结果；分析基于单个捕获报文，不代表完整连接状态。';
        return;
    }
    var lines = ['自动分析 · ' + packet.protocol + ' · ' + (packetLinks[packet.link_id] || packet.link_id),
        (packet.capture_source === 'eve-ssh' ? '以太网帧 ' + packet.frame_length + ' B；服务器网卡 ' + packet.capture_interface : '内层帧 ' + (packet.inner_length != null ? packet.inner_length + ' B' : '未知') + '；外层捕获帧 ' + packet.frame_length + ' B')];
    if (packet.source_mac || packet.destination_mac) lines.push('MAC：' + (packet.source_mac || '—') + ' → ' + (packet.destination_mac || '—'));
    (packet.analysis || []).forEach(function(item) { lines.push('• ' + item); });
    (packet.warnings || []).forEach(function(item) { lines.push('注意：' + item); });
    if (!(packet.analysis || []).length && !(packet.warnings || []).length) lines.push('无可用的详细协议分析');
    panel.textContent = lines.join('\n');
}

function stopTrace() {
    if (traceAnimation !== null) cancelAnimationFrame(traceAnimation);
    traceAnimation = null;
    document.querySelectorAll('.trace-marker').forEach(function(el) { el.remove(); });
    document.querySelectorAll('.graph-link.trace-active').forEach(function(el) { el.classList.remove('trace-active'); });
}

function replayTrace() {
    stopTrace();
    var status = document.getElementById('traceStatus');
    if (!traceSelection || !traceEdges[traceSelection.link_id] || !traceSelection.sender_id) {
        status.textContent = traceSelection && traceSelection.capture_source === 'eve-ssh' ? '已捕获服务器报文；无法确认发送端，暂不播放方向动画' : '请选择本次监控捕获的数据包';
        return;
    }
    var selected = traceSelection;
    var candidates = selected.trace_key ? packetData.filter(function(p) {
        return p.trace_key === selected.trace_key && Math.abs(Number(p.timestamp) - Number(selected.timestamp)) <= 3 && traceEdges[p.link_id] && p.sender_id;
    }).sort(function(a, b) { return Number(a.timestamp) - Number(b.timestamp) || a.id - b.id; }) : [selected];
    var start = candidates.findIndex(function(p) { return p.id === selected.id; });
    var hops = [selected], last = selected, seen = new Set([selected.link_id]);
    for (var previous = start - 1; previous >= 0; previous--) {
        var prior = candidates[previous], priorEdge = traceEdges[prior.link_id];
        var priorDestination = prior.sender_id === priorEdge.source ? priorEdge.target : priorEdge.source;
        if (priorDestination === last.sender_id && !seen.has(prior.link_id)) {
            hops.unshift(prior); seen.add(prior.link_id); last = prior;
        }
    }
    last = selected;
    for (var i = start + 1; i < candidates.length; i++) {
        var edge = traceEdges[last.link_id];
        var destination = last.sender_id === edge.source ? edge.target : edge.source;
        var next = candidates[i];
        if (next.sender_id === destination && !seen.has(next.link_id)) {
            hops.push(next); seen.add(next.link_id); last = next;
        }
    }
    status.textContent = '捕获到 ' + hops.length + ' 跳；同指纹报文按时间和相邻设备推测关联，可能存在重传或缺失';
    var svg = document.getElementById('topologyGraph'), duration = Number(document.getElementById('traceSpeed').value) || 4000;
    var marker = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    marker.setAttribute('r', '6'); marker.setAttribute('class', 'trace-marker'); svg.appendChild(marker);
    var started = null;
    function frame(now) {
        if (started === null) started = now;
        var step = Math.floor((now - started) / duration);
        if (step >= hops.length) { stopTrace(); return; }
        var hop = hops[step], edge = traceEdges[hop.link_id];
        if (!edge) { stopTrace(); return; }
        var forward = hop.sender_id === edge.source, progress = ((now - started) % duration) / duration;
        var x1 = forward ? edge.x1 : edge.x2, y1 = forward ? edge.y1 : edge.y2;
        var x2 = forward ? edge.x2 : edge.x1, y2 = forward ? edge.y2 : edge.y1;
        marker.setAttribute('cx', x1 + (x2 - x1) * progress); marker.setAttribute('cy', y1 + (y2 - y1) * progress);
        document.querySelectorAll('.graph-link').forEach(function(line) { line.classList.toggle('trace-active', line.getAttribute('data-link-id') === hop.link_id); });
        traceAnimation = requestAnimationFrame(frame);
    }
    traceAnimation = requestAnimationFrame(frame);
}

async function clearPackets() {
    try {
        var response = await fetch('/api/traffic/packets', {method: 'DELETE'});
        if (!response.ok) throw new Error('清空失败');
        packetData = [];
        traceSelection = null;
        stopTrace();
        showPacketAnalysis(null);
        renderPackets();
    } catch (error) { alert('清空数据包失败：' + error.message); }
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
    initTopologyInteraction();
    initTheme();
    initChatResize();
    document.getElementById('chatForm').addEventListener('submit', submitChat);
    document.getElementById('chatInput').addEventListener('keydown', function(event) {
        if (event.key !== 'Enter' || event.shiftKey || event.ctrlKey || event.altKey || event.metaKey || event.isComposing || event.keyCode === 229) return;
        event.preventDefault();
        if (!event.repeat && !chatBusy && !chatPending) document.getElementById('chatForm').requestSubmit();
    });
    document.getElementById('chatSettings').addEventListener('submit', saveChatSettings);
    document.getElementById('aiTemplate').addEventListener('change', applyChatTemplate);
    document.querySelector('.sidebar-tabs').addEventListener('keydown', function(event) {
        var tabs = ['scan', 'device', 'topo'];
        var current = tabs.findIndex(function(name) { return document.activeElement.id === name + 'Tab'; });
        if (current < 0) return;
        var next = event.key === 'ArrowRight' ? (current + 1) % tabs.length : event.key === 'ArrowLeft' ? (current + tabs.length - 1) % tabs.length : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : -1;
        if (next >= 0) { event.preventDefault(); switchSidebarTab(tabs[next], true); }
    });
    checkMcpStatus();
    loadEveSettings();
    loadTopologyGraph();
    pollTrafficStatus();
});
