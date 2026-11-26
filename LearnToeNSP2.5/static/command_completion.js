/* Local terminal suggestions: never sends device commands or network requests. */
(function(root) {
    'use strict';
    var huawei = [
        'display version', 'display current-configuration', 'display saved-configuration',
        'display this', 'display interface', 'display interface brief', 'display ip interface brief',
        'display ip routing-table', 'display arp', 'display mac-address', 'display vlan',
        'display lldp neighbor brief', 'display lldp neighbor', 'display ospf peer brief',
        'display ospf interface', 'display ospf routing', 'display bgp peer',
        'display stp brief', 'display eth-trunk', 'display port vlan', 'display logbuffer',
        'display current-configuration | include', 'system-view', 'return', 'quit',
        'screen-length 0 temporary', 'sysname', 'interface', 'interface LoopBack',
        'ip address', 'undo ip address', 'undo shutdown', 'shutdown', 'lldp enable',
        'undo lldp enable', 'port link-type access', 'port link-type trunk',
        'port default vlan', 'port trunk allow-pass vlan', 'vlan batch',
        'ospf', 'area', 'network', 'ip route-static', 'save', 'ping', 'tracert'
    ];
    var cisco = [
        'show version', 'show running-config', 'show startup-config', 'show interfaces',
        'show ip interface brief', 'show ip route', 'show arp', 'show mac address-table',
        'show vlan brief', 'show lldp neighbors', 'show lldp neighbors detail',
        'show cdp neighbors', 'show ip ospf neighbor', 'show ip bgp summary',
        'show spanning-tree', 'show etherchannel summary', 'show logging',
        'enable', 'configure terminal', 'terminal length 0', 'hostname', 'interface',
        'ip address', 'no shutdown', 'shutdown', 'lldp run', 'router ospf',
        'switchport mode access', 'switchport mode trunk', 'switchport access vlan',
        'switchport trunk allowed vlan', 'ip route', 'end', 'exit',
        'copy running-config startup-config', 'ping', 'traceroute'
    ];
    var linux = ['ip address show', 'ip link show', 'ip route show', 'ip neighbor show',
        'ss -tuln', 'ping', 'traceroute', 'exit'];
    var vpcs = ['show ip', 'show ipv6', 'show arp', 'show version', 'ip', 'ping', 'trace', 'save', 'help'];

    function family(model) {
        model = String(model || '').toLowerCase();
        if (/huawei|^(ar\d|s\d|usg|ac\d)/.test(model)) return 'huawei';
        if (/cisco|vios|csr|nxos|iol|ios|cat9/.test(model)) return 'cisco';
        if (/vpcs/.test(model)) return 'vpcs';
        if (/linux|ubuntu|debian|centos/.test(model)) return 'linux';
        return 'unknown';
    }

    function catalog(model, interfaces, history) {
        var kind = family(model);
        var base = kind === 'huawei' ? huawei : kind === 'cisco' ? cisco : kind === 'linux' ? linux : kind === 'vpcs' ? vpcs : huawei.concat(cisco);
        var items = [];
        function add(text, source) {
            if (typeof text !== 'string' || !text.trim() || /[\x00-\x1f\x7f]/.test(text)) return;
            if (!items.some(function(item) { return item.text.toLowerCase() === text.toLowerCase(); })) items.push({text: text, source: source});
        }
        (history || []).slice().reverse().forEach(function(text) { add(text, '本次会话历史'); });
        base.forEach(function(text) { add(text, kind === 'unknown' ? '常用命令，请核对厂商' : '常用命令'); });
        (interfaces || []).forEach(function(name) {
            if (!/^[A-Za-z][A-Za-z0-9./:_-]*$/.test(name)) return;
            if (kind === 'huawei' || kind === 'cisco' || kind === 'unknown') add('interface ' + name, '拓扑接口，请核对设备');
            if (kind === 'huawei' || kind === 'unknown') add('display interface ' + name, '拓扑接口，请核对设备');
            if (kind === 'cisco' || kind === 'unknown') add('show interfaces ' + name, '拓扑接口，请核对设备');
        });
        return items;
    }

    function candidates(value, cursor, items, showAll) {
        cursor = cursor == null ? value.length : cursor;
        var prefix = value.slice(0, cursor).replace(/^\s+/, '');
        if (!prefix && !showAll) return [];
        var tokens = prefix.toLowerCase().split(/\s+/);
        var suffix = value.slice(cursor), seen = new Set();
        return items.filter(function(item) {
            var words = item.text.toLowerCase().split(/\s+/);
            return tokens.length <= words.length && tokens.every(function(token, i) { return words[i].startsWith(token); });
        }).map(function(item) {
            // Preserve text after the cursor; do not fill later words across existing arguments.
            var text = suffix.trim() ? item.text.split(/\s+/).slice(0, tokens.length).join(' ') : item.text;
            var combined = text + suffix;
            return {text: combined, cursor: text.length, source: item.source, label: text};
        }).filter(function(item) {
            if (item.text === value || seen.has(item.text)) return false;
            seen.add(item.text);
            return true;
        }).sort(function(a, b) {
            var ap = a.label.toLowerCase().startsWith(prefix.toLowerCase()) ? 0 : 1;
            var bp = b.label.toLowerCase().startsWith(prefix.toLowerCase()) ? 0 : 1;
            return ap - bp;
        }).slice(0, 12);
    }

    function remember(history, command) {
        // Keep credentials out of local suggestions, including commands in config view.
        if (/password|secret|community|cipher|authentication|private-key|token|passphrase|local-user|username/i.test(command)) return history.slice();
        if (!/^(display|dis|show|sh|system-view|sys|return|quit|screen-length|sysname|hostname|interface|int|ip|ipv6|undo|no|shutdown|lldp|port|vlan|ospf|area|network|save|ping|tracert|traceroute|trace|enable|configure|terminal|router|switchport|end|exit|copy|ss|help)(?:\s|$)/i.test(command)) return history.slice();
        return history.filter(function(value) { return value !== command; }).concat(command).slice(-100);
    }

    function attach(input, getItems, send, id) {
        var popup = document.createElement('div');
        popup.className = 'command-completion'; popup.hidden = true;
        var list = document.createElement('div'); list.id = 'command-options-' + id;
        list.setAttribute('role', 'listbox'); list.setAttribute('aria-label', '命令补全候选');
        var hint = document.createElement('div'); hint.className = 'command-completion-hint';
        hint.textContent = '本地建议 · Tab/回车填入 · ↑↓选择 · Esc关闭 · 填入后回车发送';
        popup.append(list, hint); input.parentElement.appendChild(popup);
        input.setAttribute('role', 'combobox'); input.setAttribute('aria-autocomplete', 'list');
        input.setAttribute('aria-controls', list.id); input.setAttribute('aria-expanded', 'false');
        input.setAttribute('aria-label', '设备命令，Tab 补全，回车发送');
        input.autocomplete = 'off'; input.spellcheck = false;
        var matches = [], active = 0, snapshot = '', history = [], historyIndex = 0, draft = '', explicitSelection = false;

        function close() {
            popup.hidden = true; matches = []; active = 0;
            input.setAttribute('aria-expanded', 'false'); input.removeAttribute('aria-activedescendant');
        }
        function highlight() {
            Array.from(list.children).forEach(function(row, index) {
                row.setAttribute('aria-selected', String(index === active));
            });
            var row = list.children[active];
            if (row) { input.setAttribute('aria-activedescendant', row.id); row.scrollIntoView({block: 'nearest'}); }
        }
        function accept(index) {
            if (input.value !== snapshot || !matches[index]) { close(); return; }
            var choice = matches[index];
            input.value = choice.text;
            input.setSelectionRange(choice.cursor, choice.cursor);
            close(); input.focus(); historyIndex = history.length;
        }
        function update(showAll) {
            if (input.selectionStart !== input.selectionEnd) { close(); return; }
            matches = candidates(input.value, input.selectionStart, getItems(history), showAll);
            snapshot = input.value; active = 0; explicitSelection = false; list.replaceChildren();
            matches.forEach(function(item, index) {
                var row = document.createElement('div'); row.id = list.id + '-' + index;
                row.setAttribute('role', 'option'); row.className = 'command-completion-option';
                var text = document.createElement('span'); text.textContent = item.label;
                var source = document.createElement('small'); source.textContent = item.source;
                row.append(text, source);
                row.addEventListener('mousedown', function(event) { event.preventDefault(); });
                row.addEventListener('click', function() { accept(index); });
                list.appendChild(row);
            });
            popup.hidden = !matches.length;
            input.setAttribute('aria-expanded', String(!!matches.length)); highlight();
            if (!matches.length) input.removeAttribute('aria-activedescendant');
        }
        input.addEventListener('input', function(event) {
            historyIndex = history.length;
            if (event.isComposing) { close(); return; }
            update(false);
        });
        input.addEventListener('compositionend', function() { update(false); });
        input.addEventListener('blur', close);
        input.addEventListener('click', function() { update(false); });
        input.addEventListener('keydown', function(event) {
            if (event.isComposing || event.keyCode === 229 || event.ctrlKey || event.altKey || event.metaKey) return;
            if (event.key === 'Escape') { close(); return; }
            if (event.key === 'Tab') {
                if (event.shiftKey) { close(); return; }
                if (!matches.length) update(true);
                if (matches.length) { event.preventDefault(); accept(active); }
                return;
            }
            if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
                if (matches.length) {
                    event.preventDefault();
                    active = (active + (event.key === 'ArrowUp' ? -1 : 1) + matches.length) % matches.length;
                    explicitSelection = true;
                    highlight(); return;
                }
                if (!history.length || input.selectionStart !== input.selectionEnd) return;
                event.preventDefault();
                if (historyIndex === history.length) draft = input.value;
                historyIndex = Math.max(0, Math.min(history.length, historyIndex + (event.key === 'ArrowUp' ? -1 : 1)));
                input.value = historyIndex === history.length ? draft : history[historyIndex];
                input.setSelectionRange(input.value.length, input.value.length); return;
            }
            if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                if (event.repeat) return;
                var exact = getItems(history).some(function(item) { return item.text.toLowerCase() === input.value.trim().toLowerCase(); });
                if (matches.length && (explicitSelection || !exact)) { accept(active); return; }
                var command = input.value.trim();
                if (!command) return;
                history = remember(history, command); historyIndex = history.length; draft = '';
                close(); send(); return;
            }
            if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) close();
        });
        return {close: close};
    }

    var api = {family: family, catalog: catalog, candidates: candidates, remember: remember, attach: attach};
    root.CommandCompletion = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
