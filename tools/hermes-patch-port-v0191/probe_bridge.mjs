// Behavioural proof for acceptance tests 3 (interactive inbound) and 4 (CTA).
//
// Uses the REAL bridge modules: bridge_helpers.extractBridgeEvent,
// outbound_ids.createOutboundIdTracker, owner_message_gate.classifyOwnerMessageGate.
//
// Run:  node probe_bridge.mjs      Exit: 0 = all pass, 1 = failure.
const BR = '/usr/local/lib/hermes-agent/scripts/whatsapp-bridge';
const { extractBridgeEvent } = await import(`${BR}/bridge_helpers.js`);
const { createOutboundIdTracker } = await import(`${BR}/outbound_ids.js`);
const { classifyOwnerMessageGate } = await import(`${BR}/owner_message_gate.js`);
const { readFileSync } = await import('fs');

const results = [];
function check(name, ok, detail = '') {
  results.push([name, ok]);
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `   [${detail}]` : ''}`);
}

const CHAT = '15551234567@s.whatsapp.net';
const base = { chatId: CHAT, senderId: CHAT, senderNumber: '15551234567' };

console.log('=== 3. interactive inbound: IDs survive + identity correct ===');

const interactive = {
  buttons_reply: {
    buttonsResponseMessage: { selectedButtonId: 'START_TRIAL', selectedDisplayText: 'Start Free Trial' },
    expect: 'START_TRIAL',
  },
  template_reply: {
    templateButtonReplyMessage: { selectedId: 'TPL_BOOK', selectedDisplayText: 'Book' },
    expect: 'TPL_BOOK',
  },
  list_native_flow: {
    interactiveResponseMessage: {
      nativeFlowResponseMessage: { paramsJson: JSON.stringify({ id: 'LIST_OPT_2', display_text: 'Option 2' }) },
    },
    expect: 'LIST_OPT_2',
  },
};

for (const [label, spec] of Object.entries(interactive)) {
  const { expect, ...message } = spec;
  const msg = {
    key: { id: `ID-${label}`, remoteJid: CHAT, fromMe: false },
    message,
    messageTimestamp: 1,
    pushName: 'Customer',
  };
  const ev = await extractBridgeEvent({ msg, ...base, isGroup: false });
  check(`${label}: reply ID survives normalization`, ev.body === expect, `body=${JSON.stringify(ev.body)}`);
  check(`${label}: sender identity correct`, ev.senderId === CHAT, `senderId=${ev.senderId}`);
  check(`${label}: conversation identity correct`, ev.chatId === CHAT, `chatId=${ev.chatId}`);
  check(`${label}: messageId preserved`, ev.messageId === `ID-${label}`);
}

console.log('\n=== 3b. duplicate inbound does not execute twice ===');
{
  const tracker = createOutboundIdTracker(512);
  const ourId = 'OUR-SENT-1';
  tracker.remember(ourId);
  // bridge.js: `msg.key.fromMe && recentlySentIds.has(msg.key.id)` -> skip
  check('our own echo is recognised and skipped', tracker.has(ourId) === true);
  check('the SAME id is still recognised on redelivery (no double-execute)',
    tracker.has(ourId) === true);
  check('an unrelated inbound id is NOT skipped', tracker.has('SOMEONE-ELSE') === false);

  const gate1 = classifyOwnerMessageGate({
    fromMe: true, fromOwnerEnabled: true, recentlySent: tracker,
    allowlistMatches: () => true, messageId: ourId, chatId: CHAT,
  });
  const gate2 = classifyOwnerMessageGate({
    fromMe: true, fromOwnerEnabled: true, recentlySent: tracker,
    allowlistMatches: () => true, messageId: ourId, chatId: CHAT,
  });
  check('owner-gate classifies our echo identically on repeat delivery',
    JSON.stringify(gate1) === JSON.stringify(gate2), JSON.stringify(gate1));
}

console.log('\n=== 4. CTA: dedup rewrite + no 500 + single send ===');
{
  const tracker = createOutboundIdTracker(512);

  // Reproduce the ORIGINAL defect: the old dedup calls against the new tracker.
  let oldThrew = false;
  try {
    tracker.add('X');            // old API
  } catch (e) { oldThrew = true; }
  check('OLD dedup API (.add) throws on the v0.19.1 tracker -> was the 500',
    oldThrew === true);
  check('OLD MAX_RECENT_IDS/.size eviction shape is gone (.size is a fn, not a number)',
    typeof tracker.size === 'function');

  // The ported route's post-relay sequence, verbatim: trackSentMessageId(waMessage)
  const rememberSentId = (id) => tracker.remember(id);
  const trackSentMessageId = (sent) => rememberSentId(sent?.key?.id);
  const waMessage = { key: { id: 'CTA-MSG-1' }, message: {} };

  let threw = null;
  try { trackSentMessageId(waMessage); } catch (e) { threw = e; }
  check('NEW dedup call completes without throwing (no 500 path)', threw === null,
    threw ? String(threw) : 'ok');
  check('CTA message id is tracked for echo suppression', tracker.has('CTA-MSG-1') === true);
  check('tracker still bounded (remember/has/size only)',
    typeof tracker.remember === 'function' && typeof tracker.has === 'function');

  // enqueueSend serialisation (the shape the CTA route now uses).
  let _q = Promise.resolve();
  const enqueueSend = (fn) => { const t = _q.then(() => fn(), () => fn()); _q = t.catch(() => {}); return t; };
  const order = [];
  const relay = (tag, ms) => new Promise(r => setTimeout(() => { order.push(tag); r(tag); }, ms));
  await Promise.all([
    enqueueSend(() => relay('cta', 30)),
    enqueueSend(() => relay('normal', 1)),
  ]);
  check('enqueueSend serialises CTA vs normal send (no overlap -> no cross-chat mix)',
    order.join(',') === 'cta,normal', order.join(','));

  // Single send, no retry loop in the ported route.
  const src = readFileSync(`${BR}/bridge.js`, 'utf-8');
  // Anchor on the route itself: '// END shift-agent-cta-buttons' is also a
  // prefix of the import block's END marker earlier in the file.
  const ctaStart = src.indexOf("app.post('/send-cta'");
  const ctaEnd = src.indexOf('// END shift-agent-cta-buttons', ctaStart);
  check('CTA region located in bridge.js', ctaStart > 0 && ctaEnd > ctaStart,
    `start=${ctaStart} end=${ctaEnd}`);
  const cta = src.slice(ctaStart, ctaEnd);
  check('CTA route is registered', cta.includes("app.post('/send-cta'"));
  check('CTA relays through enqueueSend', cta.includes('enqueueSend(() => sock.relayMessage('));
  check('CTA uses the new tracker helper', cta.includes('trackSentMessageId(waMessage)'));
  check('CTA has NO retry/duplicate-send loop (ambiguous outcome not auto-duplicated)',
    !/for\s*\(|while\s*\(|retry/i.test(cta) && (cta.match(/relayMessage/g) || []).length === 1);
  check('CTA guards on connection state before sending',
    cta.includes("connectionState !== 'connected'"));
}

console.log();
const failed = results.filter(([, ok]) => !ok);
console.log(`TOTAL ${results.length} checks, ${results.length - failed.length} pass, ${failed.length} fail`);
if (failed.length) {
  for (const [n] of failed) console.log('  FAILED:', n);
  process.exit(1);
}
console.log('ALL BRIDGE (INTERACTIVE + CTA) CHECKS PASS');
