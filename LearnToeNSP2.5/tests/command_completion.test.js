const test = require('node:test');
const assert = require('node:assert/strict');
const completion = require('../static/command_completion.js');

test('matches abbreviated tokens case insensitively', () => {
    const items = completion.catalog('AR2240', [], []);
    const result = completion.candidates('DIS ip int b', 12, items);
    assert.equal(result[0].text, 'display ip interface brief');
});

test('uses vendor-specific commands and actual topology interfaces', () => {
    const huawei = completion.catalog('huaweice12800', ['GE1/0/0'], []);
    assert.equal(completion.candidates('int g', 5, huawei)[0].text, 'interface GE1/0/0');
    assert.ok(huawei.some(item => item.text === 'display version'));
    assert.ok(!huawei.some(item => item.text === 'show version'));
    const cisco = completion.catalog('vios', ['GigabitEthernet0/1'], []);
    assert.ok(cisco.some(item => item.text === 'show interfaces GigabitEthernet0/1'));
    assert.ok(!cisco.some(item => item.text === 'display version'));
    assert.equal(completion.family('vpcs'), 'vpcs');
    assert.equal(completion.family('ubuntu'), 'linux');
});

test('cursor completion preserves following arguments', () => {
    const result = completion.candidates('int GE1/0/0', 3, completion.catalog('AR2240', ['GE1/0/0'], []));
    assert.equal(result[0].text, 'interface GE1/0/0');
    assert.equal(result[0].cursor, 9);
    assert.equal(result.length, 1);
});

test('empty input requires explicit Tab and candidates are bounded', () => {
    const items = completion.catalog('', [], []);
    assert.deepEqual(completion.candidates('', 0, items), []);
    assert.equal(completion.candidates('', 0, items, true).length, 12);
    assert.deepEqual(completion.candidates('not-a-command', 13, items), []);
});

test('history is bounded, deduplicated and excludes credential commands/login values', () => {
    let history = [];
    for (let i = 0; i < 110; i++) history = completion.remember(history, 'ping 10.0.0.' + i);
    assert.equal(history.length, 100);
    history = completion.remember(history, 'ping 10.0.0.100');
    assert.equal(history.length, 100);
    assert.equal(history.at(-1), 'ping 10.0.0.100');
    for (const value of ['local-user admin password cipher abc', 'snmp-agent community read abc', 'password abc', 'MyLoginPass123!', 'admin']) {
        assert.deepEqual(completion.remember(history, value), history);
    }
});

test('history takes priority without duplicate suggestions or multiline entries', () => {
    const items = completion.catalog('AR2240', ['GE0/0/0', 'bad\nname'], ['display version', 'display version\nquit']);
    assert.equal(items.filter(i => i.text === 'display version').length, 1);
    assert.equal(items[0].source, '本次会话历史');
    assert.ok(!items.some(i => i.text.includes('\n')));
});

// Minimal DOM harness exercises keyboard behavior without a real device or network.
class Element {
    constructor() { this.children = []; this.attrs = {}; this.listeners = {}; this.value = ''; this.selectionStart = this.selectionEnd = 0; }
    append(...children) { children.forEach(child => { child.parentElement = this; this.children.push(child); }); }
    appendChild(child) { this.append(child); }
    replaceChildren() { this.children = []; }
    setAttribute(key, value) { this.attrs[key] = value; }
    removeAttribute(key) { delete this.attrs[key]; }
    addEventListener(event, fn) { this.listeners[event] = fn; }
    setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }
    scrollIntoView() {}
    focus() {}
}

function terminal() {
    global.document = {createElement: () => new Element()};
    const parent = new Element(), input = new Element();
    parent.append(input);
    const sent = [];
    completion.attach(input, history => completion.catalog('AR2240', ['GE1/0/0'], history), () => { sent.push(input.value); input.value = ''; }, 'test');
    const type = value => { input.value = value; input.setSelectionRange(value.length, value.length); input.listeners.input({}); };
    const key = (key, extra = {}) => {
        const event = {key, preventDefault() { this.prevented = true; }, ...extra};
        input.listeners.keydown(event); return event;
    };
    return {input, parent, sent, type, key, popup: parent.children[1]};
}

test('Tab fills suggestion without sending; next Enter sends', () => {
    const t = terminal();
    t.type('dis ver');
    assert.equal(t.input.attrs['aria-expanded'], 'true');
    t.key('Tab');
    assert.equal(t.input.value, 'display version');
    assert.deepEqual(t.sent, []);
    assert.equal(t.input.attrs['aria-expanded'], 'false');
    t.key('Enter');
    assert.deepEqual(t.sent, ['display version']);
});

test('Enter selects suggestion, repeat does not send, Escape closes', () => {
    const t = terminal();
    t.type('dis ver'); t.key('Enter'); t.key('Enter', {repeat: true});
    assert.equal(t.input.value, 'display version');
    assert.deepEqual(t.sent, []);
    t.type('dis'); t.key('Escape');
    assert.equal(t.popup.hidden, true);
});

test('complete command Enter sends its exact text instead of extending it', () => {
    const t = terminal();
    t.type('display interface');
    assert.equal(t.popup.hidden, false);
    t.key('Enter');
    assert.deepEqual(t.sent, ['display interface']);
});

test('arrows choose popup options; history restores draft when popup closed', () => {
    const t = terminal();
    t.type('dis'); t.key('ArrowDown');
    const selected = t.popup.children[0].children[1].children[0].textContent;
    t.key('Tab'); assert.equal(t.input.value, selected);
    t.key('Enter');
    t.type('draft');
    t.key('ArrowUp'); assert.equal(t.input.value, selected);
    t.key('ArrowDown'); assert.equal(t.input.value, 'draft');
});

test('composition, Shift+Tab and modified keys preserve normal browser input', () => {
    const t = terminal();
    t.type('dis');
    assert.equal(t.key('Enter', {isComposing: true}).prevented, undefined);
    assert.equal(t.key('Tab', {shiftKey: true}).prevented, undefined);
    assert.equal(t.popup.hidden, true);
    assert.deepEqual(t.sent, []);
});
