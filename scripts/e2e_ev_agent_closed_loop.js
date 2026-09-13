const assert = require("assert");

let chromium;
try {
  ({ chromium } = require("playwright"));
} catch (primaryError) {
  try {
    ({ chromium } = require("@playwright/test"));
  } catch (secondaryError) {
    console.error("Playwright is not installed or not resolvable from this Node environment.");
    console.error((secondaryError && secondaryError.message) || (primaryError && primaryError.message) || String(secondaryError));
    process.exit(2);
  }
}

const agentUrl = process.env.EV_AGENT_E2E_URL || "http://localhost:8090/ApplicationVue/dist/index_agent.html#/index";
const normalUrl = process.env.EV_AGENT_E2E_INDEX_URL || agentUrl.replace("index_agent.html", "index.html");
const agentBaseUrl = process.env.EV_AGENT_E2E_AGENT_BASE_URL || "http://127.0.0.1:8000";
const targetName = "\u9ed1\u9f99\u6c5f\u7701";
const command = process.env.EV_AGENT_E2E_COMMAND || "\u98de\u5230\u9ed1\u9f99\u6c5f\u7701\u5e76\u9ad8\u4eae";
const chromeExecutable = process.env.EV_AGENT_E2E_CHROME
  || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";

async function main() {
  const launchOptions = {
    headless: process.env.EV_AGENT_E2E_HEADLESS !== "0",
    args: ["--host-resolver-rules=MAP localhost 127.0.0.1"]
  };
  if (chromeExecutable) {
    launchOptions.executablePath = chromeExecutable;
  }
  const browser = await chromium.launch(launchOptions);
  const agentErrors = [];
  const observedConsoleWarnings = [];
  const page = await browser.newPage();
  await page.addInitScript((baseUrl) => {
    window.EV_AGENT_BASE_URL = baseUrl;
  }, agentBaseUrl);
  page.on("console", (message) => {
    const text = message.text();
    if (/Agent .*failed|EV Agent .*failed|BOOTSTRAP_ERROR|Unhandled|AbortError|initialization exception/i.test(text)) {
      agentErrors.push(text);
    } else if (message.type() === "error" || /ERR_NETWORK_ACCESS_DENIED/i.test(text)) {
      observedConsoleWarnings.push(text);
    }
  });
  page.on("pageerror", (error) => {
    const message = error && error.message ? error.message : String(error);
    if (/Agent .*failed|EV Agent .*failed|BOOTSTRAP_ERROR|Unhandled|AbortError|initialization exception/i.test(message)) {
      agentErrors.push(message);
    } else {
      observedConsoleWarnings.push(message);
    }
  });

  await page.goto(agentUrl, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForSelector("#ev-agent-debug-panel", { timeout: 60000 });
  await page.waitForFunction(() => (
    window.WebGLAgentBridge
    && window.EVWebGLSourceAnchors
    && typeof window.EVWebGLSourceAnchors.findBusinessObjectsByName === "function"
  ), null, { timeout: 60000 });
  await page.waitForFunction((name) => {
    const matches = window.EVWebGLSourceAnchors.findBusinessObjectsByName(name, { exact: true }) || [];
    return matches.length === 1 && (matches[0].can_fly_to === true || matches[0].bounding_sphere_valid === true);
  }, targetName, { timeout: 90000 });

  const precheck = await page.evaluate((name) => {
    const matches = window.EVWebGLSourceAnchors.findBusinessObjectsByName(name, { exact: true }) || [];
    return {
      panelCount: document.querySelectorAll("#ev-agent-debug-panel").length,
      launcherCount: document.querySelectorAll("#ev-agent-launcher").length,
      matches,
      handlers: window.WebGLAgentBridge && window.WebGLAgentBridge.listHandlers ? window.WebGLAgentBridge.listHandlers() : []
    };
  }, targetName);
  assert.strictEqual(precheck.panelCount, 1, "index_agent.html must create exactly one Agent panel");
  assert(precheck.handlers.includes("fly_to_object"), "fly_to_object handler must be registered");
  assert(precheck.handlers.includes("gis_highlight"), "gis_highlight handler must be registered");
  assert(precheck.handlers.includes("get_object_properties"), "get_object_properties handler must be registered");
  assert(precheck.handlers.includes("clear_highlight"), "clear_highlight handler must be registered");
  assert.strictEqual(precheck.matches.length, 1, `Expected one real runtime object named ${targetName}, got ${precheck.matches.length}`);

  await page.fill("#ev-agent-debug-panel [data-query]", command);
  await page.click("#ev-agent-debug-panel [data-send]");
  await page.waitForFunction(() => {
    const runtime = window.EVAgentBootstrap && window.EVAgentBootstrap.state && window.EVAgentBootstrap.state.panelRuntime;
    return runtime && ["completed", "partial_success", "failed", "canceled"].includes(runtime.state);
  }, null, { timeout: 90000 });

  const result = await page.evaluate((name) => {
    const state = window.EVAgentBootstrap.state;
    const items = state.uiEventQueue ? state.uiEventQueue.items : [];
    return {
      runtimeState: state.panelRuntime.state,
      currentObject: state.panelRuntime.currentObject,
      task: state.activeTask,
      uiEvents: items.map((item) => ({
        type: item.type,
        status: item.status,
        error_code: item.error_code,
        object_resolution: item.object_resolution,
        result: item.result
      })),
      currentObjectMatchesTarget: !!state.panelRuntime.currentObject
        && String(state.panelRuntime.currentObject.name || state.panelRuntime.currentObject.object_name || "").includes(name),
      contextText: document.querySelector("#ev-agent-debug-panel [data-context]")?.textContent || "",
      taskText: document.querySelector("#ev-agent-debug-panel [data-task]")?.textContent || ""
    };
  }, targetName);

  assert.strictEqual(result.runtimeState, "completed", JSON.stringify(result, null, 2));
  assert(result.currentObjectMatchesTarget, `currentObject must be ${targetName}`);
  assert.deepStrictEqual(result.uiEvents.map((item) => item.type), ["fly_to_object", "gis_highlight", "get_object_properties"]);
  assert(result.uiEvents.every((item) => item.status === "success"), JSON.stringify(result.uiEvents, null, 2));
  assert(result.uiEvents[0].result && result.uiEvents[0].result.success === true, "fly_to_object must succeed");
  assert(result.uiEvents[0].result.result && result.uiEvents[0].result.result.completed === true, "fly_to_object must complete");
  assert(result.uiEvents[1].result && result.uiEvents[1].result.success === true, "gis_highlight must succeed");
  assert(result.uiEvents[2].result && result.uiEvents[2].result.success === true, "get_object_properties must succeed");

  const clearResult = await page.evaluate(() => {
    const objectId = window.EVAgentBootstrap.state.panelRuntime.currentObject.object_id;
    return window.WebGLAgentBridge.executeUIEvents([{ type: "clear_highlight", payload: { object_id: objectId } }]);
  });
  assert.strictEqual(clearResult[0].success, true, JSON.stringify(clearResult, null, 2));
  assert.strictEqual(clearResult[0].result.highlight_cleared, true, JSON.stringify(clearResult, null, 2));

  const normalPage = await browser.newPage();
  await normalPage.goto(normalUrl, { waitUntil: "domcontentloaded", timeout: 60000 });
  await normalPage.waitForTimeout(2500);
  const normalState = await normalPage.evaluate(() => ({
    panelCount: document.querySelectorAll("#ev-agent-debug-panel").length,
    launcherCount: document.querySelectorAll("#ev-agent-launcher").length,
    agentEnabled: !!document.querySelector('meta[name="ev-agent-enabled"]'),
    bridgeLoaded: !!window.WebGLAgentBridge
  }));
  assert.strictEqual(normalState.agentEnabled, false, "index.html must not enable Agent");
  assert.strictEqual(normalState.panelCount, 0, "index.html must not show Agent panel");
  assert.strictEqual(normalState.launcherCount, 0, "index.html must not show Agent launcher");
  assert.strictEqual(normalState.bridgeLoaded, false, "index.html must not load Agent scripts");

  await browser.close();
  console.log(JSON.stringify({
    status: "passed",
    agentUrl,
    normalUrl,
    agentBaseUrl,
    command,
    precheck,
    result,
    clearResult,
    agentErrors,
    observedConsoleWarnings: observedConsoleWarnings.slice(0, 20),
    observedConsoleWarningCount: observedConsoleWarnings.length
  }, null, 2));
  if (agentErrors.length) {
    console.error("Agent console errors were observed:");
    agentErrors.forEach((item) => console.error(item));
    process.exit(1);
  }
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
});

