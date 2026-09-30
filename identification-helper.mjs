import * as __ccpFs_20260930 from "node:fs";

const __ccpControlPath_20260930 = __CCP_CONTROL_PATH_JSON__;
const __ccpPolicyLimit_20260930 = 4096;

function __ccpReadPolicy_20260930() {
  const before = __ccpFs_20260930.lstatSync(__ccpControlPath_20260930);
  if (!before.isFile() || before.isSymbolicLink() || before.size > __ccpPolicyLimit_20260930) return false;
  let descriptor;
  try {
    descriptor = __ccpFs_20260930.openSync(
      __ccpControlPath_20260930,
      __ccpFs_20260930.constants.O_RDONLY | (__ccpFs_20260930.constants.O_NOFOLLOW || 0),
    );
    const opened = __ccpFs_20260930.fstatSync(descriptor);
    if (!opened.isFile() || opened.size > __ccpPolicyLimit_20260930 ||
        opened.dev !== before.dev || opened.ino !== before.ino) return false;
    const bytes = Buffer.alloc(__ccpPolicyLimit_20260930 + 1);
    const count = __ccpFs_20260930.readSync(descriptor, bytes, 0, bytes.length, 0);
    if (count > __ccpPolicyLimit_20260930) return false;
    const raw = new TextDecoder("utf-8", { fatal: true }).decode(bytes.subarray(0, count));
    const policy = JSON.parse(raw);
    if (policy === null || Array.isArray(policy) || typeof policy !== "object") return false;
    // 控制文件只接受这三个字段，同时拒绝重复键造成的歧义。
    const keys = [...raw.matchAll(/"((?:\\.|[^"\\])*)"\s*:/g)].map(match => JSON.parse('"' + match[1] + '"'));
    if (keys.length !== 3 || new Set(keys).size !== 3 ||
        keys.some(key => !["schema", "enabled", "requireIdentification"].includes(key))) return false;
    const after = __ccpFs_20260930.lstatSync(__ccpControlPath_20260930);
    return after.isFile() && !after.isSymbolicLink() &&
      after.dev === opened.dev && after.ino === opened.ino &&
      after.size === opened.size && after.mtimeMs === opened.mtimeMs &&
      policy.schema === 1 && policy.enabled === true && policy.requireIdentification === true;
  } finally {
    if (descriptor !== undefined) __ccpFs_20260930.closeSync(descriptor);
  }
}

function __ccpIdentificationPolicy_20260930(metadataSupplier, originalCallback) {
  return function (...args) {
    let local = false;
    try {
      const info = this?.clientInfo;
      const extension = info?.metadata;
      const context = metadataSupplier();
      const instance = extension?.extensionInstanceId;
      const capability = info?.agentRequestHeaderEnabled;
      const session = context?.session_id;
      const turn = context?.turn_id;
      const nonempty = value => typeof value === "string" && value.trim().length > 0;
      const applicable = info?.type === "extension" && info?.family === "chrome" &&
        extension?.extensionId === "hehggadaopoacecdllhhajmbjkdcmajg" &&
        nonempty(instance) && typeof capability === "boolean" && nonempty(session) && nonempty(turn);
      if (applicable && __ccpReadPolicy_20260930()) {
        // 本地 I/O 全部同步；仍复查对象与值，以拒绝 getter 或读取期间的上下文替换。
        const latest = metadataSupplier();
        local = this.clientInfo === info && info.metadata === extension &&
          info.type === "extension" && info.family === "chrome" &&
          extension.extensionId === "hehggadaopoacecdllhhajmbjkdcmajg" &&
          extension.extensionInstanceId === instance && info.agentRequestHeaderEnabled === capability &&
          latest.session_id === session && latest.turn_id === turn;
      }
    } catch {
      local = false;
    }
    if (local) return true;
    // fallback 位于局部检查的 catch 之外，其异常和返回值不得变成再次调用。
    return Reflect.apply(originalCallback, this, args);
  };
}
