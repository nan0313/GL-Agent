"use strict";

// Dependency-free Chrome DevTools Protocol runner. It launches the installed
// Chrome directly, so npm/playwright is not required.

const fs = require("fs");
const os = require("os");
const path = require("path");
const crypto = require("crypto");
const net = require("net");
const { EventEmitter } = require("events");
const { spawn } = require("child_process");

const PROJECT_ROOT = path.resolve(__dirname, "..");
const DEFAULT_DATASET = path.join(PROJECT_ROOT, "evaluation", "datasets", "gis_e2e_v1.jsonl");
const DEFAULT_OUTPUT = path.join(PROJECT_ROOT, "evaluation", "reports", "gis_e2e_eval_v1.json");
const DEFAULT_ARTIFACT_DIR = path.join(PROJECT_ROOT, "evaluation", "reports", "gis_artifacts");
const TERMINAL_STATES = ["completed", "partial_success", "failed", "canceled", "awaiting_clarification"];
const NON_MUTATING_EVENTS = new Set(["ask_clarification", "show_message", "safety_block"]);

function usage() {
  console.log(`Usage: node evaluation/run_gis_e2e.js [options]

Options:
  --dataset PATH                 JSONL dataset (default: evaluation/datasets/gis_e2e_v1.jsonl)
  --output PATH                  JSON report path
  --split all|dev|test           Dataset split (default: all)
  --case-id ID                   Run one case; may be repeated
  --max-cases N                  Run only the first N selected cases
  --url URL                      Agent WebGL page
  --agent-base-url URL           Agent API base URL (default: http://127.0.0.1:8009)
  --chrome PATH                  Chrome/Edge executable
  --runtime-selection-name NAME  Runtime object used for selected-object cases (default: 黑龙江省)
  --transport direct|ui          direct=browser fetch + real queue/bridge; ui=debug panel (default: direct)
  --probe-runtime                Print runtime objects/capabilities, then exit without running cases
  --headed                       Show the browser window
  --require-human-locked         Refuse seed cases that are not human_locked
  --help                         Show this help
`);
}

function parseArgs(argv) {
  const args = {
    dataset: DEFAULT_DATASET,
    output: DEFAULT_OUTPUT,
    split: "all",
    caseIds: [],
    maxCases: null,
    url: "http://127.0.0.1:8090/ApplicationVue/dist/index_agent.html#/index",
    agentBaseUrl: "http://127.0.0.1:8009",
    chrome: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    runtimeSelectionName: "黑龙江省",
    transport: "direct",
    headed: false,
    requireHumanLocked: false,
    probeRuntime: false
  };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    const take = () => {
      if (i + 1 >= argv.length) throw new Error(`Missing value after ${arg}`);
      i += 1;
      return argv[i];
    };
    if (arg === "--dataset") args.dataset = path.resolve(take());
    else if (arg === "--output") args.output = path.resolve(take());
    else if (arg === "--split") args.split = take();
    else if (arg === "--case-id") args.caseIds.push(take());
    else if (arg === "--max-cases") args.maxCases = Number(take());
    else if (arg === "--url") args.url = take();
    else if (arg === "--agent-base-url") args.agentBaseUrl = take();
    else if (arg === "--chrome") args.chrome = take();
    else if (arg === "--runtime-selection-name") args.runtimeSelectionName = take();
    else if (arg === "--transport") args.transport = take();
    else if (arg === "--probe-runtime") args.probeRuntime = true;
    else if (arg === "--headed") args.headed = true;
    else if (arg === "--require-human-locked") args.requireHumanLocked = true;
    else if (arg === "--help" || arg === "-h") args.help = true;
    else throw new Error(`Unknown argument: ${arg}`);
  }
  if (!["all", "dev", "test"].includes(args.split)) throw new Error(`Invalid --split: ${args.split}`);
  if (!["direct", "ui"].includes(args.transport)) throw new Error(`Invalid --transport: ${args.transport}`);
  return args;
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

class RawWebSocket extends EventEmitter {
  constructor(url) {
    super();
    this.url = new URL(url);
    this.buffer = Buffer.alloc(0);
    this.handshakeBuffer = Buffer.alloc(0);
    this.handshaken = false;
    this.fragmentOpcode = null;
    this.fragments = [];
  }

  connect() {
    return new Promise((resolve, reject) => {
      const key = crypto.randomBytes(16).toString("base64");
      const timer = setTimeout(() => reject(new Error("Raw websocket connect timeout")), 10000);
      this.socket = net.createConnection({ host: this.url.hostname, port: Number(this.url.port || 80) });
      this.socket.once("connect", () => {
        const request = [
          `GET ${this.url.pathname}${this.url.search} HTTP/1.1`,
          `Host: ${this.url.host}`,
          "Upgrade: websocket",
          "Connection: Upgrade",
          `Sec-WebSocket-Key: ${key}`,
          "Sec-WebSocket-Version: 13",
          "",
          ""
        ].join("\r\n");
        this.socket.write(request, "utf8");
      });
      this.socket.on("data", (chunk) => {
        if (!this.handshaken) {
          this.handshakeBuffer = Buffer.concat([this.handshakeBuffer, chunk]);
          const marker = this.handshakeBuffer.indexOf("\r\n\r\n");
          if (marker < 0) return;
          const header = this.handshakeBuffer.subarray(0, marker).toString("utf8");
          if (!/^HTTP\/1\.1 101\b/.test(header)) {
            clearTimeout(timer);
            reject(new Error(`Websocket upgrade rejected: ${header.split("\r\n")[0]}`));
            this.socket.destroy();
            return;
          }
          this.handshaken = true;
          clearTimeout(timer);
          const remaining = this.handshakeBuffer.subarray(marker + 4);
          this.handshakeBuffer = Buffer.alloc(0);
          resolve();
          if (remaining.length) this._consume(remaining);
          return;
        }
        this._consume(chunk);
      });
      this.socket.once("error", (error) => {
        clearTimeout(timer);
        if (!this.handshaken) reject(error);
        this.emit("error", error);
      });
      this.socket.once("close", () => this.emit("close"));
    });
  }

  _consume(chunk) {
    this.buffer = Buffer.concat([this.buffer, chunk]);
    while (this.buffer.length >= 2) {
      const first = this.buffer[0];
      const second = this.buffer[1];
      const fin = (first & 0x80) !== 0;
      const opcode = first & 0x0f;
      const masked = (second & 0x80) !== 0;
      let length = second & 0x7f;
      let offset = 2;
      if (length === 126) {
        if (this.buffer.length < 4) return;
        length = this.buffer.readUInt16BE(2);
        offset = 4;
      } else if (length === 127) {
        if (this.buffer.length < 10) return;
        const longLength = this.buffer.readBigUInt64BE(2);
        if (longLength > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error("Websocket frame is too large");
        length = Number(longLength);
        offset = 10;
      }
      let mask = null;
      if (masked) {
        if (this.buffer.length < offset + 4) return;
        mask = this.buffer.subarray(offset, offset + 4);
        offset += 4;
      }
      if (this.buffer.length < offset + length) return;
      let payload = Buffer.from(this.buffer.subarray(offset, offset + length));
      this.buffer = this.buffer.subarray(offset + length);
      if (mask) {
        for (let i = 0; i < payload.length; i += 1) payload[i] ^= mask[i % 4];
      }
      if (opcode === 0x8) {
        const code = payload.length >= 2 ? payload.readUInt16BE(0) : null;
        const reason = payload.length > 2 ? payload.subarray(2).toString("utf8") : "";
        this.closeDetail = { code, reason };
        this.socket.end();
        return;
      }
      if (opcode === 0x9) {
        this._writeFrame(0xA, payload);
        continue;
      }
      if (opcode === 0xA) continue;
      if (opcode === 0x1 || opcode === 0x2) {
        this.fragmentOpcode = opcode;
        this.fragments = [payload];
      } else if (opcode === 0x0 && this.fragmentOpcode !== null) {
        this.fragments.push(payload);
      } else {
        continue;
      }
      if (fin) {
        const complete = Buffer.concat(this.fragments);
        const messageOpcode = this.fragmentOpcode;
        this.fragmentOpcode = null;
        this.fragments = [];
        this.emit("message", messageOpcode === 0x1 ? complete.toString("utf8") : complete);
      }
    }
  }

  _writeFrame(opcode, value) {
    const payload = Buffer.isBuffer(value) ? value : Buffer.from(String(value), "utf8");
    let header;
    if (payload.length < 126) {
      header = Buffer.alloc(2);
      header[1] = 0x80 | payload.length;
    } else if (payload.length <= 0xffff) {
      header = Buffer.alloc(4);
      header[1] = 0x80 | 126;
      header.writeUInt16BE(payload.length, 2);
    } else {
      header = Buffer.alloc(10);
      header[1] = 0x80 | 127;
      header.writeBigUInt64BE(BigInt(payload.length), 2);
    }
    header[0] = 0x80 | opcode;
    const mask = crypto.randomBytes(4);
    const masked = Buffer.alloc(payload.length);
    for (let i = 0; i < payload.length; i += 1) masked[i] = payload[i] ^ mask[i % 4];
    this.socket.write(Buffer.concat([header, mask, masked]));
  }

  send(value) {
    this._writeFrame(0x1, value);
  }

  close() {
    if (this.socket && !this.socket.destroyed) {
      try { this._writeFrame(0x8, Buffer.alloc(0)); } catch (_error) { /* ignore */ }
      this.socket.end();
    }
  }
}

class CDPClient {
  constructor(url) {
    this.url = url;
    this.nextId = 1;
    this.pending = new Map();
    this.stdoutBuffer = "";
    this.stderrBuffer = "";
  }

  async connect() {
    const python = path.join(PROJECT_ROOT, "runtime", "python", "python.exe");
    const proxy = path.join(PROJECT_ROOT, "evaluation", "cdp_proxy.py");
    if (!fs.existsSync(python)) throw new Error(`Bundled Python not found: ${python}`);
    this.proxy = spawn(python, ["-X", "utf8", proxy, this.url], {
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true
    });
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`CDP proxy start timeout: ${this.stderrBuffer}`)), 15000);
      this.proxy.stdout.on("data", (chunk) => {
        this.stdoutBuffer += chunk.toString("utf8");
        let newline;
        while ((newline = this.stdoutBuffer.indexOf("\n")) >= 0) {
          const line = this.stdoutBuffer.slice(0, newline).trim();
          this.stdoutBuffer = this.stdoutBuffer.slice(newline + 1);
          if (!line) continue;
          let message;
          try { message = JSON.parse(line); } catch (_error) { continue; }
          if (message.proxy_status === "ready") {
            clearTimeout(timer);
            resolve();
            continue;
          }
          this._handleMessage(message);
        }
      });
      this.proxy.stderr.on("data", (chunk) => {
        this.stderrBuffer = (this.stderrBuffer + chunk.toString("utf8")).slice(-8000);
      });
      this.proxy.once("error", (error) => {
        clearTimeout(timer);
        reject(error);
      });
      this.proxy.once("exit", (code) => {
        clearTimeout(timer);
        const detail = `CDP proxy exited code=${code}; stderr=${this.stderrBuffer}`;
        reject(new Error(detail));
        for (const [id, pending] of this.pending.entries()) {
          clearTimeout(pending.timer);
          pending.reject(new Error(`${detail}; pending_request=${id}`));
        }
        this.pending.clear();
      });
    });
  }

  _handleMessage(message) {
    try {
        if (!message.id || !this.pending.has(message.id)) return;
        const { resolve, reject, timer } = this.pending.get(message.id);
        this.pending.delete(message.id);
        clearTimeout(timer);
        if (message.error) reject(new Error(`${message.error.code}: ${message.error.message}`));
        else resolve(message.result || {});
      } catch (error) {
        for (const [id, pending] of this.pending.entries()) {
          clearTimeout(pending.timer);
          pending.reject(new Error(`CDP message decode failed for request ${id}: ${error.message}`));
        }
        this.pending.clear();
    }
  }

  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`CDP request timeout: ${method}`));
      }, 180000);
      this.pending.set(id, { resolve, reject, timer });
      try {
        this.proxy.stdin.write(JSON.stringify({ id, method, params }) + "\n", "utf8");
      } catch (error) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(error);
      }
    });
  }

  close() {
    if (this.proxy && this.proxy.exitCode === null) {
      this.proxy.stdin.end();
      this.proxy.kill();
    }
  }
}

async function launchChrome(options) {
  if (!fs.existsSync(options.chrome)) {
    const edge = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
    if (fs.existsSync(edge)) options.chrome = edge;
    else throw new Error(`Chrome executable not found: ${options.chrome}`);
  }
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), "ev-agent-gis-eval-"));
  const chromeArgs = [
    "--remote-debugging-port=0",
    "--remote-allow-origins=*",
    `--user-data-dir=${userDataDir}`,
    "--no-first-run",
    "--no-default-browser-check",
    "--no-sandbox",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-features=Translate",
    "--disable-gpu-sandbox",
    "--enable-webgl",
    "--enable-unsafe-swiftshader",
    "--ignore-gpu-blocklist",
    "--use-angle=swiftshader",
    "--host-resolver-rules=MAP localhost 127.0.0.1",
    "about:blank"
  ];
  if (!options.headed) chromeArgs.unshift("--headless=new");
  const child = spawn(options.chrome, chromeArgs, { stdio: ["ignore", "ignore", "pipe"], windowsHide: !options.headed });
  const stderrLines = [];
  child.stderr.on("data", (chunk) => {
    stderrLines.push(chunk.toString("utf8"));
    if (stderrLines.length > 50) stderrLines.shift();
  });
  const activePortPath = path.join(userDataDir, "DevToolsActivePort");
  let port = null;
  for (let attempt = 0; attempt < 200; attempt += 1) {
    if (child.exitCode !== null) throw new Error(`Chrome exited early with code ${child.exitCode}`);
    if (fs.existsSync(activePortPath)) {
      try {
        const lines = fs.readFileSync(activePortPath, "utf8").trim().split(/\r?\n/);
        port = Number(lines[0]);
        if (port) break;
      } catch (_readError) {
        // Chrome can briefly hold an exclusive lock while writing this file.
      }
    }
    await delay(100);
  }
  if (!port) throw new Error("Chrome DevTools port did not become available");
  let target = null;
  for (let attempt = 0; attempt < 100; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/new?about%3Ablank`, { method: "PUT" });
      if (response.ok) target = await response.json();
      if (target) break;
    } catch (_error) {
      // Chrome may not have opened the first target yet.
    }
    await delay(100);
  }
  if (!target) throw new Error("Chrome page target did not become available");
  const cdp = new CDPClient(target.webSocketDebuggerUrl);
  await cdp.connect();
  try {
    await cdp.send("Page.enable");
    await cdp.send("Runtime.enable");
  } catch (error) {
    const chromeDetail = stderrLines.join("").slice(-6000);
    if (child.exitCode === null) child.kill();
    throw new Error(`${error.message}; chrome_exit=${child.exitCode}; chrome_stderr=${chromeDetail}`);
  }
  return { child, cdp, userDataDir };
}

async function evaluate(cdp, expression) {
  const response = await cdp.send("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true
  });
  if (response.exceptionDetails) {
    const detail = response.exceptionDetails.exception?.description || response.exceptionDetails.text;
    throw new Error(`Browser evaluation failed: ${detail}`);
  }
  return response.result ? response.result.value : undefined;
}

function callExpression(fn, ...args) {
  return `(${fn.toString()})(${args.map((value) => JSON.stringify(value)).join(",")})`;
}

async function waitFor(cdp, expression, timeoutMs, label) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const value = await evaluate(cdp, expression);
      if (value) return value;
    } catch (error) {
      lastError = error;
    }
    await delay(300);
  }
  throw new Error(`${label} timeout after ${timeoutMs} ms${lastError ? `; ${lastError.message}` : ""}`);
}

async function loadAgentPage(cdp, options) {
  await cdp.send("Page.addScriptToEvaluateOnNewDocument", {
    source: `window.EV_AGENT_BASE_URL=${JSON.stringify(options.agentBaseUrl)};window.EV_AGENT_CONFIG=Object.assign({healthTimeoutMs:30000,showPanel:true},window.EV_AGENT_CONFIG||{});`
  });
  await cdp.send("Page.navigate", { url: options.url });
  await waitFor(
    cdp,
    "document.readyState === 'complete' || document.readyState === 'interactive'",
    60000,
    "document ready"
  );
  try {
    await waitFor(
      cdp,
      "!!(document.querySelector('#ev-agent-debug-panel') && window.EVAgentBootstrap && window.WebGLAgentBridge && window.EVWebGLSourceAnchors && typeof window.EVWebGLSourceAnchors.findBusinessObjectsByName === 'function')",
      90000,
      "EV Agent debug page"
    );
  } catch (error) {
    const diagnostic = await evaluate(cdp, callExpression(function () {
      return {
        href: location.href,
        title: document.title,
        ready_state: document.readyState,
        body_text: (document.body && document.body.innerText || "").slice(0, 500),
        app_child_count: document.querySelector("#app") ? document.querySelector("#app").children.length : null,
        canvas_count: document.querySelectorAll("canvas").length,
        viewer: !!window.viewer,
        bootstrap: !!window.EVAgentBootstrap,
        bridge: !!window.WebGLAgentBridge,
        source_anchors: !!window.EVWebGLSourceAnchors,
        scripts: Array.from(document.scripts).map(function (item) { return item.src || "inline"; }),
        resources: performance.getEntriesByType("resource").slice(-20).map(function (item) {
          return { name: item.name, duration: item.duration, transfer_size: item.transferSize };
        })
      };
    }));
    throw new Error(`${error.message}; diagnostic=${JSON.stringify(diagnostic)}`);
  }
}

async function waitForNamedObject(cdp, name, timeoutMs) {
  if (!name) return;
  await waitFor(
    cdp,
    callExpression(function (objectName) {
      const anchors = window.EVWebGLSourceAnchors;
      if (!anchors || typeof anchors.findBusinessObjectsByName !== "function") return false;
      const matches = anchors.findBusinessObjectsByName(objectName, { exact: true }) || [];
      return matches.length > 0;
    }, name),
    timeoutMs,
    `runtime object ${name}`
  );
}

async function prepareCase(cdp, testCase, selectionName) {
  const pre = testCase.preconditions || {};
  if (pre.must_exist && pre.runtime_object_name) {
    await waitForNamedObject(cdp, pre.runtime_object_name, Math.min(testCase.timeout_ms || 60000, 90000));
  }
  if (pre.selected_object || pre.requires_unique_runtime_selection) {
    await waitForNamedObject(cdp, selectionName, Math.min(testCase.timeout_ms || 60000, 90000));
  }
  return evaluate(cdp, callExpression(function (caseData, runtimeSelectionName) {
    const preconditions = caseData.preconditions || {};
    const expectedEvents = (caseData.gold && caseData.gold.ui_events) || [];
    const anchors = window.EVWebGLSourceAnchors;
    const handlers = window.WebGLAgentBridge && window.WebGLAgentBridge.listHandlers
      ? window.WebGLAgentBridge.listHandlers()
      : [];
    const missingHandlers = expectedEvents.filter(function (type) {
      return ["ask_clarification", "show_message", "safety_block"].indexOf(type) < 0
        && handlers.indexOf(type) < 0;
    });
    const checks = {
      debug_panel: !!document.querySelector("#ev-agent-debug-panel"),
      bridge: !!window.WebGLAgentBridge,
      source_anchors: !!anchors,
      expected_handlers: missingHandlers.length === 0
    };
    let runtimeMatches = [];
    if (preconditions.runtime_object_name && anchors && anchors.findBusinessObjectsByName) {
      runtimeMatches = anchors.findBusinessObjectsByName(preconditions.runtime_object_name, { exact: true }) || [];
      if (preconditions.must_exist) checks.runtime_object_exists = runtimeMatches.length > 0;
      if (preconditions.must_be_unique) checks.runtime_object_unique = runtimeMatches.length === 1;
      if (preconditions.must_not_exist) checks.runtime_object_absent = runtimeMatches.length === 0;
    }
    let selected = null;
    if (preconditions.selected_object || preconditions.requires_unique_runtime_selection) {
      const matches = anchors.findBusinessObjectsByName(runtimeSelectionName, { exact: true }) || [];
      checks.runtime_selection_unique = matches.length === 1;
      selected = matches.length === 1 ? matches[0] : null;
      if (selected && window.EVWebGLSelectedObjectBridge) {
        window.EVWebGLSelectedObjectBridge.setSelectedObject(selected, { source: "gis_evaluator" });
      } else if (selected) {
        window.__EV_AGENT_SELECTED_OBJECT__ = selected;
      }
    }
    if (preconditions.viewer_available) checks.viewer_available = !!window.viewer;
    if (preconditions.canvas_available) checks.canvas_available = !!document.querySelector("canvas");
    return {
      pass: Object.keys(checks).every(function (key) { return checks[key] === true; }),
      checks: checks,
      missing_handlers: missingHandlers,
      runtime_match_count: runtimeMatches.length,
      selected_object: selected
    };
  }, testCase, selectionName));
}

async function sendTurn(cdp, query, timeoutMs) {
  const previousTaskId = await evaluate(
    cdp,
    "window.EVAgentBootstrap.state.activeTask && window.EVAgentBootstrap.state.activeTask.task_id || null"
  );
  await evaluate(cdp, callExpression(function (text) {
    const panel = document.querySelector("#ev-agent-debug-panel");
    if (!panel) throw new Error("debug panel missing");
    const input = panel.querySelector("[data-query]");
    const button = panel.querySelector("[data-send]");
    input.value = text;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    button.click();
    return true;
  }, query));
  await waitFor(cdp, callExpression(function (oldTaskId, terminalStates) {
    const state = window.EVAgentBootstrap && window.EVAgentBootstrap.state;
    const task = state && state.activeTask;
    const runtime = state && state.panelRuntime;
    return !!task && task.task_id !== oldTaskId && !!runtime
      && terminalStates.indexOf(runtime.state) >= 0
      && runtime.responseFinished === true;
  }, previousTaskId, TERMINAL_STATES), timeoutMs, `turn '${query}'`);
  return snapshot(cdp);
}

async function sendTurnDirect(cdp, query, timeoutMs, caseId) {
  return evaluate(cdp, callExpression(async function (text, requestTimeoutMs, evaluationCaseId) {
    function safe(value, depth, seen) {
      if (value === null || value === undefined || typeof value === "string" || typeof value === "number" || typeof value === "boolean") return value;
      if (depth > 6) return "[max-depth]";
      if (typeof value === "function") return "[function]";
      if (typeof value !== "object") return String(value);
      if (seen.indexOf(value) >= 0) return "[circular]";
      seen.push(value);
      if (Array.isArray(value)) return value.slice(0, 100).map(function (item) { return safe(item, depth + 1, seen); });
      var output = {};
      Object.keys(value).slice(0, 100).forEach(function (key) {
        try { output[key] = safe(value[key], depth + 1, seen); } catch (_error) { output[key] = "[unreadable]"; }
      });
      seen.pop();
      return output;
    }
    function inner(value) {
      return value && value.result && typeof value.result === "object" ? value.result : value || {};
    }
    var runtime = window.__EV_GIS_EVAL_RUNTIME__ || {
      conversation_id: "gis-eval-" + evaluationCaseId + "-" + Date.now(),
      last_context_object: null,
      last_admin_region: null,
      buffers: [],
      buffer_queries: []
    };
    window.__EV_GIS_EVAL_RUNTIME__ = runtime;
    var selected = window.EVWebGLSourceAnchors && window.EVWebGLSourceAnchors.getSelectedObject
      ? window.EVWebGLSourceAnchors.getSelectedObject()
      : window.__EV_AGENT_SELECTED_OBJECT__ || null;
    var requestId = "gis-eval-request-" + Date.now() + "-" + Math.random().toString(16).slice(2);
    var payload = {
      session_id: runtime.conversation_id,
      conversation_id: runtime.conversation_id,
      request_id: requestId,
      user_id: "gis-evaluator",
      role: "developer",
      query: text,
      message: text,
      gis_context: {
        selected_object: selected,
        last_context_object: runtime.last_context_object,
        last_admin_region: runtime.last_admin_region,
        last_buffer: runtime.buffers.length ? runtime.buffers[runtime.buffers.length - 1] : null,
        last_buffer_query: runtime.buffer_queries.length ? runtime.buffer_queries[runtime.buffer_queries.length - 1] : null,
        buffers: runtime.buffers,
        buffer_queries: runtime.buffer_queries,
        active_layers: window.currentActiveLayers || [],
        camera_state: {},
        current_object_id: selected && selected.object_id || null
      },
      context: {
        selected_object: selected,
        last_context_object: runtime.last_context_object,
        last_admin_region: runtime.last_admin_region,
        current_object_id: selected && selected.object_id || null
      }
    };
    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, requestTimeoutMs);
    var response;
    var data;
    try {
      response = await fetch(String(window.EV_AGENT_BASE_URL || "http://127.0.0.1:8009").replace(/\/$/, "") + "/agent/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: controller.signal
      });
      var responseText = await response.text();
      if (!response.ok) throw new Error("Agent HTTP " + response.status + ": " + responseText.slice(0, 500));
      data = JSON.parse(responseText);
    } finally {
      clearTimeout(timer);
    }
    var uiEvents = Array.isArray(data.ui_events) ? data.ui_events : [];
    var queue = new window.AgentUIEventQueue({
      bridge: window.WebGLAgentBridge,
      sourceAnchors: window.EVWebGLSourceAnchors,
      taskId: "gis-eval-task-" + Date.now(),
      requestId: requestId,
      handlerTimeoutMs: Math.min(requestTimeoutMs, 30000)
    });
    uiEvents.forEach(function (event, index) {
      queue.enqueue({
        type: event.type || event.event_type,
        payload: event.payload || event.params || {},
        sequence: index + 1,
        source_event: event
      });
    });
    var executionDeadline = Date.now() + requestTimeoutMs;
    while (Date.now() < executionDeadline) {
      var pending = queue.items.some(function (item) {
        return item.status === "queued" || item.status === "pending" || item.status === "executing";
      });
      if (!pending && !queue.executing) break;
      await new Promise(function (resolve) { setTimeout(resolve, 50); });
    }
    var items = queue.items;
    items.forEach(function (item) {
      var value = inner(item.result);
      if (item.object_resolution && item.object_resolution.status === "resolved") {
        runtime.last_context_object = item.object_resolution;
      }
      if (["fly_to_object", "gis_highlight", "get_object_properties"].indexOf(item.type) >= 0 && item.payload) {
        runtime.last_context_object = Object.assign({}, runtime.last_context_object || {}, item.payload);
      }
      if (["locate_admin_region", "highlight_admin_boundary", "get_admin_region_properties"].indexOf(item.type) >= 0) {
        runtime.last_admin_region = Object.assign({}, runtime.last_admin_region || {}, value, item.payload || {});
      }
      if (item.type === "create_buffer" && item.status === "success") runtime.buffers.push(value);
      if (item.type === "query_objects_in_buffer" && item.status === "success") runtime.buffer_queries.push(value);
      if (item.type === "clear_buffer" && item.status === "success") runtime.buffers = [];
    });
    var failed = items.filter(function (item) { return item.status === "failed" || item.status === "canceled"; });
    var succeeded = items.filter(function (item) { return item.status === "success"; });
    var onlyClarification = items.length > 0 && items.every(function (item) {
      return ["ask_clarification", "show_message"].indexOf(item.type) >= 0;
    });
    var runtimeState = onlyClarification
      ? "awaiting_clarification"
      : failed.length
        ? (succeeded.length ? "partial_success" : "failed")
        : "completed";
    return {
      runtime_state: runtimeState,
      response_finished: true,
      active_task: { request_id: requestId, trace_id: data.trace_id || null, status: data.status || null },
      current_object: safe(runtime.last_context_object, 0, []),
      current_admin_region: safe(runtime.last_admin_region, 0, []),
      buffer_count: runtime.buffers.length,
      buffer_query_count: runtime.buffer_queries.length,
      answer: data.answer || "",
      route_type: data.route_type || data.metadata && data.metadata.route_type || null,
      ui_events: items.map(function (item) {
        return {
          type: item.type,
          status: item.status,
          error_code: item.error_code || null,
          error: item.error || null,
          payload: safe(item.payload, 0, []),
          result: safe(item.result, 0, [])
        };
      })
    };
  }, query, timeoutMs, caseId));
}

function runTurn(cdp, query, timeoutMs, testCase, transport) {
  return transport === "ui"
    ? sendTurn(cdp, query, timeoutMs)
    : sendTurnDirect(cdp, query, timeoutMs, testCase.case_id);
}

async function snapshot(cdp) {
  return evaluate(cdp, callExpression(function () {
    function safe(value, depth, seen) {
      if (value === null || value === undefined || typeof value === "string" || typeof value === "number" || typeof value === "boolean") return value;
      if (depth > 6) return "[max-depth]";
      if (typeof value === "function") return "[function]";
      if (typeof value !== "object") return String(value);
      if (seen.indexOf(value) >= 0) return "[circular]";
      seen.push(value);
      if (Array.isArray(value)) return value.slice(0, 100).map(function (item) { return safe(item, depth + 1, seen); });
      const result = {};
      Object.keys(value).slice(0, 100).forEach(function (key) {
        try { result[key] = safe(value[key], depth + 1, seen); } catch (_error) { result[key] = "[unreadable]"; }
      });
      seen.pop();
      return result;
    }
    const state = window.EVAgentBootstrap.state;
    const items = state.uiEventQueue && state.uiEventQueue.items || [];
    return {
      runtime_state: state.panelRuntime.state,
      response_finished: state.panelRuntime.responseFinished,
      active_task: safe(state.activeTask, 0, []),
      current_object: safe(state.panelRuntime.currentObject, 0, []),
      current_admin_region: safe(state.panelRuntime.currentAdminRegion, 0, []),
      buffer_count: (state.buffers || []).length,
      buffer_query_count: (state.bufferQueries || []).length,
      ui_events: items.map(function (item) {
        return {
          type: item.type,
          status: item.status,
          error_code: item.error_code || null,
          error: item.error || null,
          payload: safe(item.payload, 0, []),
          result: safe(item.result, 0, [])
        };
      })
    };
  }));
}

async function setupStatefulPreconditions(cdp, testCase, timeoutMs, transport) {
  const pre = testCase.preconditions || {};
  const setup = [];
  if (pre.currently_highlighted) {
    const target = pre.runtime_object_name || "黑龙江省";
    setup.push(await runTurn(cdp, `高亮${target}`, timeoutMs, testCase, transport));
  }
  if (pre.latest_buffer_exists || pre.latest_buffer_query_exists) {
    setup.push(await runTurn(cdp, "给黑龙江省创建500米缓冲区", timeoutMs, testCase, transport));
  }
  if (pre.latest_buffer_query_exists) {
    setup.push(await runTurn(cdp, "查询缓冲区里面的站点", timeoutMs, testCase, transport));
  }
  const pass = setup.every((item) => item.ui_events.length > 0 && item.ui_events.every((event) => event.status === "success"));
  return { required: setup.length > 0, pass, turns: setup };
}

function equalArrays(left, right) {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function receiptPresent(event) {
  return event && event.status === "success" && event.result !== null && event.result !== undefined;
}

function eventForPostcondition(name) {
  const map = {
    search_completed_with_runtime_results: "search_business_objects",
    camera_reached_resolved_object: "fly_to_object",
    target_style_or_selection_visibly_changed: "gis_highlight",
    properties_match_resolved_object: "get_object_properties",
    target_highlight_removed: "clear_highlight",
    camera_reached_resolved_admin_region: "locate_admin_region",
    resolved_admin_boundary_is_visible: "highlight_admin_boundary",
    admin_properties_match_resolved_region: "get_admin_region_properties",
    runtime_layer_tree_returned: "get_layer_tree",
    runtime_layer_list_returned: "get_layer_list",
    actual_layer_visibility_matches_request: "set_layer_visibility",
    camera_state_matches_viewer: "get_camera_state",
    camera_matches_configured_default_view: "reset_view",
    camera_reached_coordinate_within_tolerance: "fly_to_coordinates",
    valid_nonempty_png_or_data_url_returned: "screenshot",
    buffer_geometry_rendered_with_distance_tolerance: "create_buffer",
    returned_objects_satisfy_buffer_relation: "query_objects_in_buffer",
    all_returned_objects_highlighted: "highlight_buffer_query_results",
    camera_reached_latest_buffer: "fly_to_buffer",
    latest_buffer_removed_from_scene: "clear_buffer",
    returned_objects_within_requested_radius: "query_nearby_objects",
    requested_panel_opened_with_matching_payload: "open_panel",
    clarification_names_missing_or_ambiguous_field: "ask_clarification",
    safety_block_is_visible: "safety_block",
    not_found_is_reported_without_false_success: "ask_clarification"
  };
  return map[name] || null;
}

function assessCase(testCase, turnSnapshots, precheck, setupResult) {
  const events = turnSnapshots.flatMap((item) => item.ui_events || []);
  const actualTypes = events.map((item) => item.type);
  const expectedTypes = testCase.gold.ui_events || [];
  const eventSequencePass = equalArrays(actualTypes, expectedTypes);
  const receiptsPass = events.length === expectedTypes.length && events.every(receiptPresent);
  const postconditionResults = (testCase.gold.postconditions || []).map((name) => {
    if (name === "no_business_mutation") {
      const pass = events.every((event) => NON_MUTATING_EVENTS.has(event.type));
      return { name, pass, strategy: "no mutating UI event observed" };
    }
    const expectedEvent = eventForPostcondition(name);
    const event = events.find((item) => item.type === expectedEvent);
    return {
      name,
      pass: receiptPresent(event),
      strategy: `successful WebGL bridge receipt for ${expectedEvent || "unmapped condition"}`
    };
  });
  const postconditionsPass = postconditionResults.every((item) => item.pass);
  const handling = testCase.gold.expected_handling;
  const handlingPass = handling === "execute"
    ? turnSnapshots.every((item) => item.runtime_state === "completed")
    : handling === "reject"
      ? actualTypes.includes("safety_block")
      : handling === "clarify"
        ? actualTypes.includes("ask_clarification")
        : handling === "not_found"
          ? actualTypes.includes("ask_clarification") && events.every((event) => NON_MUTATING_EVENTS.has(event.type))
          : false;
  const setupPass = !setupResult.required || setupResult.pass;
  return {
    success: precheck.pass && setupPass && eventSequencePass && receiptsPass && postconditionsPass && handlingPass,
    preconditions_pass: precheck.pass,
    setup_pass: setupPass,
    event_sequence_pass: eventSequencePass,
    receipt_pass: receiptsPass,
    postconditions_pass: postconditionsPass,
    handling_pass: handlingPass,
    expected_event_types: expectedTypes,
    actual_event_types: actualTypes,
    postcondition_results: postconditionResults,
    events
  };
}

async function captureScreenshot(cdp, targetPath) {
  const response = await cdp.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
  fs.mkdirSync(path.dirname(targetPath), { recursive: true });
  fs.writeFileSync(targetPath, Buffer.from(response.data, "base64"));
}

function rate(numerator, denominator) {
  return denominator ? Number((numerator / denominator).toFixed(6)) : null;
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {
    usage();
    return;
  }
  const lines = fs.readFileSync(options.dataset, "utf8").split(/\r?\n/).filter((line) => line.trim());
  let cases = lines.map((line) => JSON.parse(line));
  if (options.split !== "all") cases = cases.filter((item) => item.split === options.split);
  if (options.caseIds.length) cases = cases.filter((item) => options.caseIds.includes(item.case_id));
  if (options.maxCases !== null) cases = cases.slice(0, Math.max(0, options.maxCases));
  if (!cases.length) throw new Error("No GIS cases selected");
  const unlocked = cases.filter((item) => item.review_status !== "human_locked");
  if (options.requireHumanLocked && unlocked.length) {
    throw new Error(`${unlocked.length} cases are not human_locked; first=${unlocked[0].case_id}`);
  }

  const launched = await launchChrome(options);
  const results = [];
  const startedAt = Date.now();
  try {
    await loadAgentPage(launched.cdp, options);
    if (options.probeRuntime) {
      const probe = await evaluate(launched.cdp, callExpression(function () {
        const anchors = window.EVWebGLSourceAnchors || {};
        let spatial = [];
        try {
          if (typeof anchors.getSpatialObjects === "function") spatial = anchors.getSpatialObjects() || [];
          else if (typeof anchors.debugSpatialObjects === "function") spatial = anchors.debugSpatialObjects() || [];
          else spatial = anchors.debugSpatialObjects || [];
        } catch (error) {
          return { error: error && error.message || String(error), anchor_methods: Object.keys(anchors).sort() };
        }
        function brief(item) {
          item = item || {};
          return {
            object_id: item.object_id || item.id || null,
            name: item.name || item.object_name || null,
            object_type: item.object_type || item.business_type || item.type || null,
            longitude: item.longitude || item.lon || item.lng || null,
            latitude: item.latitude || item.lat || null,
            can_fly_to: item.can_fly_to === true,
            can_highlight: item.can_highlight === true,
            position_valid: item.position_valid === true,
            bounding_sphere_valid: item.bounding_sphere_valid === true
          };
        }
        const normalized = Array.from(spatial || []).map(brief);
        const flyable = normalized.filter(function (item) {
          return item.can_fly_to || item.position_valid || item.bounding_sphere_valid
            || (item.longitude !== null && item.latitude !== null);
        });
        return {
          anchor_methods: Object.keys(anchors).sort(),
          spatial_object_count: normalized.length,
          flyable_object_count: flyable.length,
          flyable_objects: flyable.slice(0, 100),
          sample_objects: normalized.slice(0, 50)
        };
      }));
      console.log(JSON.stringify(probe, null, 2));
      return;
    }
    for (let index = 0; index < cases.length; index += 1) {
      const testCase = cases[index];
      const caseStarted = Date.now();
      let record;
      try {
        if (index > 0) {
          await launched.cdp.send("Page.navigate", { url: "about:blank" });
          await delay(200);
          await launched.cdp.send("Page.navigate", { url: options.url });
          await waitFor(
            launched.cdp,
            "!!(document.querySelector('#ev-agent-debug-panel') && window.EVAgentBootstrap && window.WebGLAgentBridge && window.EVWebGLSourceAnchors)",
            90000,
            "EV Agent page reload"
          );
        }
        const precheck = await prepareCase(launched.cdp, testCase, options.runtimeSelectionName);
        const setupResult = await setupStatefulPreconditions(
          launched.cdp,
          testCase,
          Math.min(Number(testCase.timeout_ms || 60000), 120000),
          options.transport
        );
        const turns = [];
        for (const turn of testCase.turns) {
          turns.push(await runTurn(
            launched.cdp,
            turn.query,
            Math.min(Number(testCase.timeout_ms || 60000), 120000),
            testCase,
            options.transport
          ));
        }
        const assessment = assessCase(testCase, turns, precheck, setupResult);
        record = {
          case_id: testCase.case_id,
          split: testCase.split,
          case_type: testCase.case_type,
          category: testCase.category,
          success: assessment.success,
          precheck,
          setup: setupResult,
          turns,
          assessment,
          latency_ms: Date.now() - caseStarted,
          error: null
        };
      } catch (error) {
        const screenshotPath = path.join(DEFAULT_ARTIFACT_DIR, `${testCase.case_id}.png`);
        try { await captureScreenshot(launched.cdp, screenshotPath); } catch (_captureError) { /* ignore */ }
        record = {
          case_id: testCase.case_id,
          split: testCase.split,
          case_type: testCase.case_type,
          category: testCase.category,
          success: false,
          latency_ms: Date.now() - caseStarted,
          error: `${error.name || "Error"}: ${error.message || String(error)}`,
          screenshot: screenshotPath
        };
      }
      results.push(record);
      console.log(`[${String(index + 1).padStart(3, "0")}/${String(cases.length).padStart(3, "0")}] ${testCase.case_id} ${record.success ? "PASS" : "FAIL"}`);
    }
  } finally {
    launched.cdp.close();
    if (launched.child.exitCode === null) launched.child.kill();
    await delay(300);
    try {
      const resolved = path.resolve(launched.userDataDir);
      if (resolved.startsWith(path.resolve(os.tmpdir())) && path.basename(resolved).startsWith("ev-agent-gis-eval-")) {
        fs.rmSync(resolved, { recursive: true, force: true });
      }
    } catch (_cleanupError) {
      // Chrome can briefly retain profile files on Windows; they are in the OS temp directory.
    }
  }

  const executable = results.filter((item) => item.case_type === "executable");
  const abnormal = results.filter((item) => item.case_type !== "executable");
  const executablePass = executable.filter((item) => item.success).length;
  const abnormalPass = abnormal.filter((item) => item.success).length;
  const safeForResume = unlocked.length === 0 && results.every((item) => !item.error);
  const report = {
    status: "completed",
    generated_at: new Date().toISOString(),
    dataset: options.dataset,
    dataset_version: cases[0].dataset_version,
    split: options.split,
    case_count: cases.length,
    review_gate: {
      required_human_locked: options.requireHumanLocked,
      unlocked_case_count: unlocked.length,
      safe_for_resume_claim: safeForResume
    },
    environment: {
      url: options.url,
      agent_base_url: options.agentBaseUrl,
      chrome: options.chrome,
      headed: options.headed,
      runtime_selection_name: options.runtimeSelectionName,
      transport: options.transport
    },
    metrics: {
      executable_count: executable.length,
      executable_success_count: executablePass,
      e2e_task_success_rate: rate(executablePass, executable.length),
      e2e_task_success_rate_percent: executable.length ? Number((100 * executablePass / executable.length).toFixed(2)) : null,
      abnormal_count: abnormal.length,
      abnormal_correct_count: abnormalPass,
      expected_handling_accuracy: rate(abnormalPass, abnormal.length),
      expected_handling_accuracy_percent: abnormal.length ? Number((100 * abnormalPass / abnormal.length).toFixed(2)) : null
    },
    metric_definition: {
      e2e_task_success_rate: "Strict all-or-nothing pass: runtime preconditions, stateful setup, expected UI-event sequence, successful WebGL bridge receipts, mapped postconditions, and final handling must all pass.",
      verification_level: options.transport === "ui"
        ? "Real debug-panel stream client + UI-event queue + WebGL bridge receipt/runtime state."
        : "Real Chrome browser fetch + production UI-event queue + WebGL bridge receipt/runtime state; debug-panel rendering is bypassed.",
      visual_audit: "Visual/geometric tolerances should be manually audited before locking the dataset."
    },
    elapsed_seconds: Number(((Date.now() - startedAt) / 1000).toFixed(2)),
    results
  };
  fs.mkdirSync(path.dirname(options.output), { recursive: true });
  fs.writeFileSync(options.output, JSON.stringify(report, null, 2) + "\n", "utf8");
  console.log(JSON.stringify({
    status: report.status,
    case_count: report.case_count,
    metrics: report.metrics,
    safe_for_resume_claim: report.review_gate.safe_for_resume_claim,
    output: options.output
  }, null, 2));
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
