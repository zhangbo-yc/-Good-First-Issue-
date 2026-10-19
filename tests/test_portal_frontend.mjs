import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import { setTimeout as tick } from 'node:timers/promises';

// Run the actual browser application against a minimal DOM and mocked SSE.
// No server, credentials, task descriptions or model calls are involved.
const elements = new Map();
function element() {
  return {
    value: '', textContent: '', innerHTML: '', hidden: false, disabled: false,
    scrollHeight: 0, scrollTop: 0, clientHeight: 0,
    classList: { toggle() {} }, querySelectorAll() { return []; },
    addEventListener() {}, close() {}, showModal() {}, focus() {},
  };
}
const context = vm.createContext({
  document: {
    querySelector(selector) {
      if (!elements.has(selector)) elements.set(selector, element());
      return elements.get(selector);
    },
    querySelectorAll() { return []; },
  },
  URL, URLSearchParams, AbortController, DOMException, TextDecoder,
  setTimeout, clearTimeout,
});
let source = readFileSync(new URL('../web/app.js', import.meta.url), 'utf8');
const startup = source.lastIndexOf("window.addEventListener('hashchange'");
assert.ok(startup > 0, 'Application startup boundary must be present');
source = source.slice(0, startup);
vm.runInContext(source, context, { filename: 'web/app.js' });
const evaluate = code => vm.runInContext(code, context);
const encoder = new TextEncoder();
function frame(name, data) {
  return `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;
}
function response(events, close = true) {
  let sink;
  const body = new ReadableStream({
    start(controller) {
      sink = controller;
      for (const event of events) controller.enqueue(encoder.encode(event));
      if (close) controller.close();
    },
  });
  return { response: { ok: true, body }, sink };
}
function setTask(id = 'example/repo#1') {
  const number = Number(id.split('#')[1]);
  evaluate(`state.detail = {task_id: ${JSON.stringify(id)}, repo: 'example/repo', number: ${number}}; state.chatTask = ${JSON.stringify(id)};`);
}
function lastMessage(task = 'example/repo#1') {
  return evaluate(`historyFor(${JSON.stringify(task)}).at(-1)`);
}
const requests = [];
let nextResponse;
context.fetch = async (url, options) => {
  assert.equal(url, '/api/chat', 'All requests must stay within the mock');
  requests.push(JSON.parse(options.body));
  assert.ok(nextResponse, 'A mocked response must be configured');
  return nextResponse.response;
};

setTask();
nextResponse = response([
  frame('meta', { task_id: 'example/repo#1' }),
  frame('token', { text: 'partial-error' }),
  frame('error', { message: 'mock provider error' }),
]);
await evaluate("sendChat('error case')");
assert.equal(lastMessage().status, 'failed');
assert.equal(lastMessage().text, '');
assert.match(elements.get('#chat-log').innerHTML, /mock provider error/);
assert.match(elements.get('#chat-log').innerHTML, /data-retry-message/);
assert.equal(evaluate("chatHistory(historyFor(state.chatTask)).some(message => message.role === 'assistant')"), false);
console.log('PASS token → error discards text and offers retry');

nextResponse = response([frame('token', { text: 'partial-eof' })]);
await evaluate("sendChat('disconnect case')");
assert.equal(lastMessage().status, 'failed');
assert.equal(lastMessage().text, '');
assert.match(lastMessage().error, /中断/);
assert.equal(requests.at(-1).history.some(message => message.text === 'partial-error'), false);
console.log('PASS token → EOF marks the reply incomplete');

nextResponse = response([frame('token', { text: 'partial-abort' })], false);
const oldResponse = nextResponse;
const oldRequest = evaluate("sendChat('abort case')");
await tick(0);
assert.equal(lastMessage().text, 'partial-abort');
evaluate('cancelChat()');
assert.equal(lastMessage().status, 'failed');
assert.equal(lastMessage().text, '');
assert.equal(evaluate('state.streaming'), false);
assert.match(elements.get('#chat-log').innerHTML, /回答已停止/);
console.log('PASS abort immediately clears the pending reply');

setTask('example/repo#2');
nextResponse = response([
  frame('meta', { task_id: 'example/repo#2' }),
  frame('token', { text: 'complete reply' }),
  frame('done', { task_id: 'example/repo#2' }),
]);
await evaluate("sendChat('new task case')");
assert.equal(lastMessage('example/repo#2').status, 'complete');
assert.equal(lastMessage('example/repo#2').text, 'complete reply');
assert.equal(requests.at(-1).history.length, 0, 'A different task must not inherit history');
console.log('PASS successful done preserves a completed reply');

const newTaskMarkup = elements.get('#chat-log').innerHTML;
oldResponse.sink.enqueue(encoder.encode(frame('token', { text: 'late-old-task-token' })));
oldResponse.sink.close();
await oldRequest;
assert.equal(lastMessage().text, '');
assert.equal(lastMessage('example/repo#2').text, 'complete reply');
assert.equal(elements.get('#chat-log').innerHTML, newTaskMarkup);
assert.equal(evaluate('state.streaming'), false);
console.log('PASS late old-task stream cannot mutate the new task or UI');

setTask();
nextResponse = response([
  frame('token', { text: 'completed after failures' }),
  frame('done', { task_id: 'example/repo#1' }),
]);
await evaluate("sendChat('follow-up case')");
assert.equal(requests.at(-1).history.some(message => message.role === 'assistant'), false,
  'Failed, disconnected and aborted assistant turns must all be excluded');
assert.equal(requests.at(-1).history.some(message => /partial-|late-old-task/.test(message.text)), false);
assert.equal(lastMessage().status, 'complete');

nextResponse = response([
  frame('token', { text: 'next completed reply' }),
  frame('done', { task_id: 'example/repo#1' }),
]);
await evaluate("sendChat('completed history case')");
assert.ok(requests.at(-1).history.some(message => message.role === 'assistant' && message.text === 'completed after failures'),
  'Completed assistant replies should remain usable as context');
assert.equal(requests.at(-1).history.some(message => /partial-|late-old-task/.test(message.text)), false);
console.log('PASS subsequent prompts exclude incomplete replies and retain completed history');
console.log('All frontend SSE regression checks passed; no network or model calls were made.');
