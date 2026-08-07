import { extractBridgeEvent } from '/usr/local/lib/hermes-agent/scripts/whatsapp-bridge/bridge_helpers.js';

const base = { chatId: '15551234567@s.whatsapp.net', senderId: '15551234567@s.whatsapp.net', senderNumber: '15551234567' };

const cases = [
  ['buttonsResponseMessage', { buttonsResponseMessage: { selectedButtonId: 'START_TRIAL', selectedDisplayText: 'Start Free Trial' } }],
  ['buttonsResponse displayText-only', { buttonsResponseMessage: { selectedDisplayText: 'Act Now' } }],
  ['templateButtonReplyMessage', { templateButtonReplyMessage: { selectedId: 'TPL_2', selectedDisplayText: 'Book' } }],
  ['interactive nativeFlow paramsJson', { interactiveResponseMessage: { nativeFlowResponseMessage: { paramsJson: '{"id":"CTA_YES","display_text":"Yes"}' } } }],
  ['interactive malformed json -> body fallback', { interactiveResponseMessage: { nativeFlowResponseMessage: { paramsJson: '{not json' }, body: { text: 'fallback body' } } }],
  ['REGRESSION plain conversation', { conversation: 'hello world' }],
  ['REGRESSION extendedText', { extendedTextMessage: { text: 'quoted reply' } }],
];

let fail = 0;
for (const [name, message] of cases) {
  const msg = { key: { id: 'ID1', remoteJid: base.chatId, fromMe: false }, message, messageTimestamp: 1 };
  const ev = await extractBridgeEvent({ msg, ...base });
  const ok = ev.body !== '';
  if (!ok) fail += 1;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name.padEnd(38)} body=${JSON.stringify(ev.body)} nativeType=${ev.nativeType}`);
}
console.log(fail === 0 ? 'ALL JS BRANCH CASES PASS' : `${fail} JS CASE(S) FAILED`);
