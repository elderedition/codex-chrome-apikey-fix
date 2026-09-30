import assert from "node:assert/strict";
import fs from "node:fs";
import { pathToFileURL } from "node:url";

const { factory } = await import(pathToURL(process.argv[2]));
function pathToURL(value) { return pathToFileURL(value).href; }
const control = process.argv[3];
let assertions = 0;
function check(value, expected) { assert.equal(value, expected); assertions += 1; }
const valid = { schema: 1, enabled: true, requireIdentification: true };
function policy(value = valid) { fs.writeFileSync(control, JSON.stringify(value), "utf8"); }
function fixture() {
  return {
    clientInfo: { type: "extension", family: "chrome", agentRequestHeaderEnabled: false,
      metadata: { extensionId: "hehggadaopoacecdllhhajmbjkdcmajg", extensionInstanceId: "instance" } },
  };
}
let context = { session_id: "session", turn_id: "turn" };
let calls = 0;
let receiver;
let forwarded;
const sentinel = {};
function original(...args) { calls += 1; receiver = this; forwarded = args; return sentinel; }
const wrapped = factory(() => context, original);
let client = fixture();
policy();
check(wrapped.call(client), true);
check(calls, 0);
client.clientInfo.agentRequestHeaderEnabled = true;
check(wrapped.call(client), true);
check(calls, 0);

const invalidPolicies = [null, [], true, 1, "true", {},
  { ...valid, schema: true }, { ...valid, schema: "1" }, { ...valid, schema: 2 },
  { ...valid, enabled: false }, { ...valid, enabled: 1 }, { ...valid, enabled: "true" },
  { ...valid, requireIdentification: false }, { ...valid, requireIdentification: 1 },
  { ...valid, requireIdentification: "true" }, { ...valid, unknown: true }];
for (const value of invalidPolicies) { policy(value); check(wrapped.call(client), sentinel); }
for (const value of ["{", "x".repeat(5000), '{"schema":1,"enabled":false,"enabled":true,"requireIdentification":true}']) {
  fs.writeFileSync(control, value, "utf8"); check(wrapped.call(client), sentinel);
}
fs.unlinkSync(control);
check(wrapped.call(client), sentinel);
fs.mkdirSync(control);
check(wrapped.call(client), sentinel);
fs.rmdirSync(control);
policy();
for (const mutate of [
  c => c.clientInfo.type = "native", c => c.clientInfo.family = "firefox",
  c => c.clientInfo.metadata.extensionId = "other",
  c => c.clientInfo.metadata.extensionInstanceId = "", c => c.clientInfo.metadata.extensionInstanceId = 5,
  c => c.clientInfo.agentRequestHeaderEnabled = 1, c => c.clientInfo.agentRequestHeaderEnabled = null,
  c => c.clientInfo.metadata = null, c => c.clientInfo = null,
]) {
  client = fixture(); mutate(client); check(wrapped.call(client), sentinel);
}
client = fixture();
for (const bad of [{ session_id: "", turn_id: "turn" }, { session_id: "session", turn_id: "" },
                   { session_id: 1, turn_id: "turn" }, { session_id: "session", turn_id: false }, null]) {
  context = bad; check(wrapped.call(client), sentinel);
}
context = { session_id: "session", turn_id: "turn" };
policy({ ...valid, enabled: false });
const argument = {};
const previousCalls = calls;
check(wrapped.call(client, argument, "second", 3), sentinel);
check(calls, previousCalls + 1);
check(receiver, client);
check(forwarded[0], argument);
check(forwarded[1], "second");
check(forwarded[2], 3);

let failures = 0;
const failure = new Error("same failure");
const throwing = factory(() => context, function () { failures += 1; throw failure; });
assert.throws(() => throwing.call(client), error => error === failure); assertions += 1;
check(failures, 1);
const rejected = Promise.reject(failure);
const rejecting = factory(() => context, function () { failures += 1; return rejected; });
check(rejecting.call(client), rejected);
await assert.rejects(rejected, error => error === failure); assertions += 1;
check(failures, 2);

policy();
check(factory(() => { throw new Error("local failure"); }, original).call(client), sentinel);
check(factory(() => ({ ...context }), original).call(client), true);
for (const change of [
  () => { context.turn_id = "changed-turn"; },
  () => { context.session_id = "changed-session"; },
  () => { client.clientInfo = { ...client.clientInfo }; },
  () => { client.clientInfo.metadata = { ...client.clientInfo.metadata }; },
  () => { client.clientInfo.metadata.extensionInstanceId = "changed-instance"; },
  () => { client.clientInfo.agentRequestHeaderEnabled = !client.clientInfo.agentRequestHeaderEnabled; },
]) {
  client = fixture(); context = { session_id: "session", turn_id: "turn" };
  let reads = 0;
  const changing = factory(() => { reads += 1; if (reads === 2) change(); return context; }, original);
  check(changing.call(client), sentinel);
}
client = fixture(); context = { session_id: "session", turn_id: "turn" };
policy(); check(wrapped.call(client), true);
policy({ ...valid, enabled: false }); check(wrapped.call(client), sentinel);
policy(); check(wrapped.call(client), true);
console.log(JSON.stringify({ assertions }));
