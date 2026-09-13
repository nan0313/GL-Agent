(function (global) {
  "use strict";

  var BOOTSTRAP_VERSION = "0.3.0";
  var PANEL_STATE_VERSION = 5;
  var DEFAULT_CONFIG = {
    agentBaseUrl: "",
    environment: "development",
    releaseId: "",
    webglArtifact: "",
    devMode: false,
    role: "user",
    debug: false,
    handlerTimeoutMs: 15000,
    showPanel: true,
    healthTimeoutMs: 2000,
    maxRetries: 120,
    retryIntervalMs: 300
  };
  var DEVELOPMENT_AGENT_BASE_URL = "http://127.0.0.1:8009";
  var state = {
    adapter: null,
    panel: null,
    basicPanel: null,
    launcher: null,
    statusNode: null,
    timelineNode: null,
    taskNode: null,
    contextNode: null,
    quickActionsNode: null,
    diagnosticsNode: null,
    errorNode: null,
    messagesNode: null,
    rawEventsNode: null,
    devBottomNode: null,
    devScrollNode: null,
    currentConfig: null,
    currentController: null,
    activeTask: null,
    uiEventQueue: null,
    rawEvents: [],
    messageHistory: [],
    taskHistory: [],
    errors: [],
    statusEvents: [],
    uiEventResults: [],
    uiEventLedger: [],
    buffers: [],
    bufferQueries: [],
    dialogueState: null,
    semanticTraces: [],
    modelParticipation: [],
    qwenInvocations: [],
    ragSummary: null,
    ragIndexStatus: null,
    ragRetrievals: [],
    ragEvidences: [],
    ragCitations: [],
    ragFallbacks: [],
    ragSourceOperations: [],
    ragIndexJobs: [],
    ragBackupOperations: [],
    clarifications: [],
    objectResolutionResults: [],
    objectCardKeys: {},
    processedEventKeys: {},
    transportMode: "idle",
    closed: false,
    minimized: false,
    presentationMode: "expanded",
    expandedSize: { width: "680px", height: "760px" },
    activeDiagnosticTab: "tasks",
    diagnosticScroll: {},
    scrollFollow: { timeline: true, tasks: true, events: true, messages: true, errors: true, state: true },
    testObject: null,
    sessionStartedAt: new Date().toISOString(),
    connectionStatus: "checking",
    activeRequestStatus: "idle",
    lastRequestStatus: "none",
    sessionHealth: "starting",
    qwenHealth: null,
    interaction: null,
    started: false,
    modeResolving: false,
    shortcutBound: false,
    health: null,
    releaseWarning: null,
    panelRuntime: {
      state: "idle",
      activeRequestId: null,
      activeTaskId: null,
      abortController: null,
      eventQueue: null,
      currentObject: null,
      currentAdminRegion: null,
      startedAt: null,
      completedAt: null,
      responseFinished: false,
      streamNode: null,
      currentReader: null
    },
    runtimeState: {
      page_enabled: false,
      frontend_ready: false,
      backend_online: false,
      webgl_ready: false,
      panel_created: false,
      panel_visible: false,
      state: "disabled",
      error: null
    }
  };

  function start() {
    var config = normalizeConfig(merge(DEFAULT_CONFIG, global.EV_AGENT_CONFIG || {}));
    state.currentConfig = config;
    var pageEnabled = isEVAgentEnabledPage();
    traceBootstrapStage("page enabled", { enabled: pageEnabled, path: global.location && global.location.pathname });
    if (!pageEnabled) {
      setRuntimeState("disabled", {
        page_enabled: false,
        frontend_ready: false,
        backend_online: false,
        webgl_ready: false,
        panel_created: false,
        panel_visible: false,
        error: null
      });
      return { state: "disabled" };
    }
    if (config.__serverModeResolved !== true) {
      if (state.modeResolving) return { state: "checking_mode" };
      state.modeResolving = true;
      setRuntimeState("checking_backend", { page_enabled: true, frontend_ready: true });
      checkAgentBackendHealth(config).then(function (health) {
        var serverMode = health && health.raw && health.raw.agent_mode;
        var mode = serverMode === "developer" ? "developer" : "customer";
        state.modeResolving = false;
        state.health = health;
        if (mode === "customer") {
          state.started = true;
          launchCustomerMode(config, health);
          return;
        }
        global.EV_AGENT_CONFIG = merge(global.EV_AGENT_CONFIG || {}, {
          __serverModeResolved: true,
          mode: "developer",
          devMode: true,
          role: "developer",
          debug: true
        });
        start();
      }).catch(function () {
        state.modeResolving = false;
        state.started = true;
        launchCustomerMode(config, { online: false });
      });
      return { state: "checking_mode" };
    }
    if (state.started) {
      if (state.panel) openPanel();
      traceBootstrapStage("panel visible", { visible: !!(state.panel && state.panel.hidden !== true) });
      return { state: state.runtimeState.state };
    }
    try {
      setRuntimeState("booting", { page_enabled: true, frontend_ready: true });
      installPanelStyles();
      ensureLauncher(config);
      if (config.showPanel !== false) {
        ensurePanel(config);
        openPanel();
      }
      state.started = true;
      traceBootstrapStage("panel visible", { visible: !!(state.panel && state.panel.hidden !== true) });
      setLauncherState("checking", "EV Agent checking...");
    } catch (error) {
      if (global.console && console.error) console.error("[EV Agent Bootstrap] initialization exception", error);
      renderBootstrapError(config, error);
      return { state: "failed" };
    }
    checkAgentBackendHealth(config).then(function (health) {
      state.health = health;
      state.releaseWarning = compareReleaseIds(config.releaseId, health.release_id);
      if (state.releaseWarning) appendWarning(state.releaseWarning.message);
      checkQwenHealth(config);
      traceBootstrapStage("backend status resolved", health);
      if (!health.online) {
        setRuntimeState("offline", {
          backend_online: false,
          error: {
            code: "AGENT_BACKEND_OFFLINE",
            message: health.message || "EV Agent service is not connected."
          }
        });
        renderOfflineLauncher(health);
        renderOfflinePanel(health);
        openPanel();
        traceBootstrapStage("panel visible", { visible: !!(state.panel && state.panel.hidden !== true) });
        return;
      }
      setRuntimeState("checking_backend", { backend_online: true, error: null });
      setLauncherState("checking", "EV Agent starting...");
      ensureSelectedObjectBridge(config, function () {
        ensureEVPickPatch(config, function () {
          waitForReady(config, 0);
        });
      });
    }).catch(function (error) {
      var health = {
        online: false,
        error_code: "AGENT_BACKEND_HEALTH_FAILED",
        message: error && error.message ? error.message : String(error)
      };
      state.health = health;
      setRuntimeState("offline", {
        backend_online: false,
        error: { code: health.error_code, message: health.message }
      });
      renderOfflineLauncher(health);
      renderOfflinePanel(health);
      openPanel();
      traceBootstrapStage("backend status resolved", health);
      traceBootstrapStage("panel visible", { visible: !!(state.panel && state.panel.hidden !== true) });
    });
  }

  function checkQwenHealth(config) {
    if (typeof fetch !== "function") return;
    fetch(trimTrailingSlash(config.agentBaseUrl) + "/health/qwen", { method: "GET", cache: "no-store" })
      .then(function (response) { return response.ok ? response.json() : { available: false, error_code: "QWEN_UNAVAILABLE" }; })
      .then(function (data) { state.qwenHealth = sanitizeExportValue(data || {}); renderDiagnostics(); })
      .catch(function () { state.qwenHealth = { available: false, error_code: "QWEN_UNAVAILABLE" }; renderDiagnostics(); });
  }

  function renderBootstrapError(config, error) {
    state.currentConfig = config || state.currentConfig || DEFAULT_CONFIG;
    installPanelStyles();
    ensureLauncher(state.currentConfig);
    ensurePanel(merge(state.currentConfig, { skipStreamScripts: true }));
    renderStatusPanel("AGENT_BOOTSTRAP_ERROR", "EV Agent initialization failed.", {
      message: error && error.message ? error.message : String(error)
    });
    openPanel();
    state.started = false;
    setRuntimeState("failed", {
      page_enabled: true,
      frontend_ready: false,
      backend_online: false,
      panel_created: !!state.panel,
      panel_visible: !!(state.panel && state.panel.hidden !== true),
      error: {
        code: "AGENT_BOOTSTRAP_ERROR",
        message: error && error.message ? error.message : String(error)
      }
    });
    traceBootstrapStage("panel visible", { visible: !!(state.panel && state.panel.hidden !== true) });
  }

  function waitForReady(config, retry) {
    if (!global.WebGLAgentBridge) {
      return retryLater(config, retry, "window.WebGLAgentBridge");
    }
    if (!global.createEVWebGLAgentAdapter) {
      return retryLater(config, retry, "window.createEVWebGLAgentAdapter");
    }
    if (!global.viewer) {
      return retryLater(config, retry, "window.viewer");
    }
    setRuntimeState("frontend_ready", { frontend_ready: true, webgl_ready: true });
    attach(config);
  }

  function retryLater(config, retry, label) {
    setRuntimeState("webgl_waiting", {
      webgl_ready: false,
      error: { code: "WEBGL_NOT_READY", message: "Waiting for " + label }
    });
    setLauncherState("waiting", "EV Agent waiting for WebGL");
    if (retry >= config.maxRetries) {
      var message = "Agent bootstrap timeout waiting for " + label;
      setRuntimeState("webgl_waiting", {
        webgl_ready: false,
        error: { code: "WEBGL_NOT_READY", message: message }
      });
      setLauncherState("waiting", "WebGL not ready");
      warn(message);
      return;
    }
    setTimeout(function () {
      waitForReady(config, retry + 1);
    }, config.retryIntervalMs);
  }

  function attach(config) {
    if (state.adapter) {
      attachSelectedObjectBridge();
      patchEVPickTool();
      registerAdapterHandlers(state.adapter);
      return state.adapter;
    }
    if (config.showPanel !== false) ensurePanel(config);
    restoreOnlinePanelContent();
    attachSelectedObjectBridge();
    patchEVPickTool();
    var adapter = global.createEVWebGLAgentAdapter({
      agentBaseUrl: config.agentBaseUrl,
      handlerTimeoutMs: config.handlerTimeoutMs,
      role: config.role || "developer",
      viewer: global.viewer,
      layerManager: global.LayerManager || global.evLayerManager || global.EV_LayerManager,
      selector: global.selector || global.EV_SelectorTool || global.EV_SelectorEventHandler,
      getSelectedObject: getSelectedObject,
      getActiveLayers: function () {
        return global.currentActiveLayers || [];
      },
      resolveObject: function (objectId) {
        if (global.viewer && global.viewer.entities && global.viewer.entities.getById) {
          return global.viewer.entities.getById(objectId);
        }
        return null;
      },
      openPanel: function (payload) {
        var panelType = payload && (payload.type || payload.event_type || payload.panel_type || payload.target);
        if (panelType === "open_panel") {
          console.log("[Agent open_panel]", payload);
          appendMessage("open_panel: " + stringify(payload));
        } else {
          console.log("[WebGLAgent event result]", payload);
          appendMessage("event result: " + stringify(payload));
        }
        global.dispatchEvent(new CustomEvent("agent:open-panel", { detail: payload }));
        showPanelPayload(payload);
        showBasicOpenPanel(payload);
      },
      showMessage: function (payload) {
        console.log("[Agent message]", payload);
        appendMessage(payload && payload.message ? payload.message : stringify(payload));
      },
      onResponse: function (response) {
        setResponse(response && response.answer ? response.answer : stringify(response));
        setEvents(response && response.ui_events ? response.ui_events : []);
        if (typeof config.onResponse === "function") config.onResponse(response);
      },
      onError: function (error) {
        setError(error && error.message ? error.message : stringify(error));
        if (typeof config.onError === "function") config.onError(error);
      },
      debug: config.debug !== false
    });
    adapter.attach();
    global.EVWebGLAgentAdapter = adapter;
    global.AgentAdapter = adapter;
    registerAdapterHandlers(adapter);
    state.adapter = adapter;
    setRuntimeState("ready", {
      frontend_ready: true,
      backend_online: true,
      webgl_ready: true,
      handler_ready: !!(global.WebGLAgentBridge && global.WebGLAgentBridge.listHandlers && global.WebGLAgentBridge.listHandlers().length),
      business_data_ready: hasBusinessObjects(),
      panel_created: !!state.panel,
      panel_visible: !!state.panel && state.panel.hidden !== true,
      error: null
    });
    setStatus("Agent online");
    setLauncherState("online", "EV Agent online");
    log(config, "attached");
    console.log("[EV Agent Bootstrap] attached");
    return adapter;
  }

  function hasBusinessObjects() {
    try {
      if (global.EVWebGLSourceAnchors && typeof global.EVWebGLSourceAnchors.getSpatialObjects === "function") {
        var payload = global.EVWebGLSourceAnchors.getSpatialObjects();
        return !!(payload && Array.isArray(payload.objects) && payload.objects.length);
      }
    } catch (error) {}
    return false;
  }

  function restoreOnlinePanelContent() {
    if (!state.panel) return;
    state.panel.hidden = false;
    var input = state.panel.querySelector("[data-query]");
    var send = state.panel.querySelector("[data-send]");
    if (input) input.disabled = false;
    if (send) send.disabled = false;
    if (state.taskNode && state.taskNode.textContent === "Offline") {
      state.taskNode.textContent = "空闲";
    }
    if (state.timelineNode && state.runtimeState.state !== "ready") {
      replaceChildren(state.timelineNode);
    }
    renderQuickActions();
    renderContextBar();
    renderDiagnostics();
  }

  function registerAdapterHandlers(adapter) {
    if (adapter && typeof adapter.registerAllHandlers === "function") {
      adapter.registerAllHandlers();
    }
    attachSelectedObjectBridge();
    patchEVPickTool();
    if (global.WebGLAgentBridge && typeof global.WebGLAgentBridge.logRegisteredHandlers === "function") {
      global.WebGLAgentBridge.logRegisteredHandlers();
    }
  }

  function getSelectedObject() {
    if (global.EVWebGLSourceAnchors && typeof global.EVWebGLSourceAnchors.getSelectedObject === "function") {
      return normalizeSelectedObject(global.EVWebGLSourceAnchors.getSelectedObject());
    }
    return normalizeSelectedObject(
      global.__EV_AGENT_SELECTED_OBJECT__ || global.currentSelectedObject || null
    );
  }

  function normalizeSelectedObject(value) {
    if (!value) return null;
    var properties = value.properties || {};
    return {
      object_id: pickFirst(value.object_id, value.objectId, value.id, properties.id, properties.EVID),
      object_type: pickFirst(value.object_type, value.objectType, value.type, properties.type),
      object_name: pickFirst(value.object_name, value.objectName, value.name, properties.name, properties.NAME),
      layer_id: pickFirst(value.layer_id, value.layerId, value.layer, properties.layer_id),
      layer_name: pickFirst(value.layer_name, value.layerName, value.data_source_name, properties.layer_name, properties.layerName),
      data_source_name: pickFirst(value.data_source_name, value.dataSourceName, properties.data_source_name),
      business_id: pickFirst(value.business_id, properties.business_id, properties.OBJECTID),
      business_id_source: pickFirst(value.business_id_source, properties.business_id_source),
      stable_id: value.stable_id === true || properties.stable_id === true,
      longitude: pickFirst(value.longitude, value.lon, value.lng, properties.longitude, properties.lon, properties.lng),
      latitude: pickFirst(value.latitude, value.lat, properties.latitude, properties.lat),
      height: pickFirst(value.height, value.altitude, value.alt, properties.height, properties.altitude, properties.alt),
      source: pickFirst(value.source, properties.source),
      timestamp: pickFirst(value.timestamp, properties.timestamp),
      properties: properties
    };
  }

  function ensureSelectedObjectBridge(config, callback) {
    if (global.EVWebGLSelectedObjectBridge) {
      attachSelectedObjectBridge();
      callback();
      return;
    }
    var existing = document.getElementById("ev-agent-selected-object-bridge");
    if (existing) {
      existing.addEventListener("load", function () {
        attachSelectedObjectBridge();
        callback();
      });
      existing.addEventListener("error", callback);
      setTimeout(callback, 1000);
      return;
    }
    var script = document.createElement("script");
    script.id = "ev-agent-selected-object-bridge";
    script.async = true;
    script.src = trimTrailingSlash(config.agentBaseUrl) + "/webgl_selected_object_bridge.js";
    script.onload = function () {
      attachSelectedObjectBridge();
      callback();
    };
    script.onerror = function () {
      warn("selected object bridge load failed");
      callback();
    };
    document.head.appendChild(script);
  }

  function ensureEVPickPatch(config, callback) {
    if (global.__EV_AGENT_DISABLE_PICK_PATCH__) {
      callback();
      return;
    }
    if (global.EVWebGLEvPickPatch) {
      patchEVPickTool();
      callback();
      return;
    }
    var existing = document.getElementById("ev-agent-pick-patch");
    if (existing) {
      existing.addEventListener("load", function () {
        patchEVPickTool();
        callback();
      });
      existing.addEventListener("error", callback);
      setTimeout(callback, 1000);
      return;
    }
    var script = document.createElement("script");
    script.id = "ev-agent-pick-patch";
    script.async = true;
    script.src = trimTrailingSlash(config.agentBaseUrl) + "/webgl_ev_pick_patch.js";
    script.onload = function () {
      patchEVPickTool();
      callback();
    };
    script.onerror = function () {
      warn("EVPickTool patch load failed");
      callback();
    };
    document.head.appendChild(script);
  }

  function attachSelectedObjectBridge() {
    if (global.EVWebGLSelectedObjectBridge && typeof global.EVWebGLSelectedObjectBridge.attach === "function") {
      global.EVWebGLSelectedObjectBridge.attach();
    }
  }

  function patchEVPickTool() {
    if (global.EVWebGLEvPickPatch && typeof global.EVWebGLEvPickPatch.patch === "function") {
      global.EVWebGLEvPickPatch.patch();
    }
  }

  function trimTrailingSlash(value) {
    return String(value || "").replace(/\/+$/, "");
  }

  function isEVAgentEnabledPage() {
    var marker = document.querySelector && document.querySelector('meta[name="ev-agent-enabled"]');
    return !!(marker && String(marker.getAttribute("content") || "").toLowerCase() === "true");
  }

  function checkAgentBackendHealth(config) {
    if (typeof fetch !== "function") {
      return Promise.resolve({
        online: false,
        error_code: "AGENT_BACKEND_HEALTH_UNSUPPORTED",
        message: "fetch is unavailable."
      });
    }
    var controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    var timeoutMs = clamp(Number(config.healthTimeoutMs || 2000), 1500, 3000);
    var timer = setTimeout(function () {
      if (controller) controller.abort();
    }, timeoutMs);
    return fetch(trimTrailingSlash(config.agentBaseUrl) + "/api/agent/health", {
      method: "GET",
      cache: "no-store",
      signal: controller && controller.signal
    }).then(function (response) {
      clearTimeout(timer);
      if (!response.ok) {
        return {
          online: false,
          status: response.status,
          error_code: "AGENT_BACKEND_HEALTH_FAILED",
          message: "Agent health check returned HTTP " + response.status + "."
        };
      }
      return response.json().catch(function () {
        return { status: "ok", service: "ev-agent", streaming: true };
      }).then(function (data) {
        return {
          online: data && data.status === "ok",
          status: data && data.status || "unknown",
          service: data && data.service || "ev-agent",
          streaming: data && data.streaming === true,
          release_id: data && data.release_id || null,
          agent_commit: data && data.agent_commit || null,
          webgl_artifact_version: data && data.webgl_artifact_version || null,
          runtime_profile: data && data.runtime_profile || null,
          rag: data && data.rag || null,
          qwen: data && data.qwen || null,
          raw: data
        };
      });
    }).catch(function (error) {
      clearTimeout(timer);
      return {
        online: false,
        error_code: error && error.name === "AbortError" ? "AGENT_BACKEND_HEALTH_TIMEOUT" : "AGENT_BACKEND_OFFLINE",
        message: error && error.message ? error.message : String(error)
      };
    });
  }

  function setRuntimeState(name, patch) {
    patch = patch || {};
    state.runtimeState = merge(state.runtimeState, patch);
    state.runtimeState.state = name || state.runtimeState.state;
    state.runtimeState.page_enabled = !!state.runtimeState.page_enabled;
    state.runtimeState.frontend_ready = !!state.runtimeState.frontend_ready;
    state.runtimeState.backend_online = !!state.runtimeState.backend_online;
    state.runtimeState.webgl_ready = !!state.runtimeState.webgl_ready;
    state.runtimeState.panel_created = !!state.panel || !!state.runtimeState.panel_created;
    state.runtimeState.panel_visible = !!(state.panel && state.panel.hidden !== true);
    state.connectionStatus = state.runtimeState.backend_online ? "connected" : name === "booting" || name === "checking_backend" ? "checking" : "disconnected";
    if (!isTaskRunning()) state.sessionHealth = state.runtimeState.backend_online ? "healthy" : "degraded";
    global.__EV_AGENT_RUNTIME_STATE__ = state.runtimeState;
    if (!global.EVWebGLAgentStatus) {
      global.EVWebGLAgentStatus = {
        getStatus: function () {
          return merge({}, state.runtimeState);
        }
      };
    }
    renderDiagnostics();
  }

  function bindGlobalShortcut() {
    if (state.shortcutBound) return;
    document.addEventListener("keydown", handleGlobalShortcut);
    state.shortcutBound = true;
  }

  function ensurePanel(config) {
    if (state.panel || config.showPanel === false) return;
    state.currentConfig = config;
    var panel = document.createElement("div");
    panel.id = "ev-agent-debug-panel";
    panel.className = "ev-agent-panel";
    installPanelStyles();
    applySavedWindowState(panel);
    traceBootstrapStage("panel shell created", { id: panel.id });
    panel.innerHTML =
      '<div class="ev-agent-titlebar" data-drag-handle>' +
      '<div class="ev-agent-title"><span class="ev-agent-brand-mark" aria-hidden="true">E</span><strong>EV Agent</strong><span data-status>未连接</span><span class="ev-agent-knowledge-badge">输变电知识库</span></div>' +
      '<div class="ev-agent-title-actions">' +
      '<button data-devtools title="显示或隐藏开发者诊断">开发者</button>' +
      '<button data-settings title="当前版本暂未启用更多菜单" aria-label="更多菜单，当前版本暂未启用" disabled>…</button>' +
      '<button data-minimize title="最小化" aria-label="最小化">−</button>' +
      '<button data-close title="收起到右下角入口" aria-label="关闭到入口">×</button>' +
      '</div></div>' +
      '<div class="ev-agent-workspace">' +
      '<nav class="ev-agent-rail" aria-label="EV Agent 导航">' +
      '<button data-nav-action="new" title="新会话"><span aria-hidden="true">＋</span><em>新会话</em></button>' +
      '<button data-knowledge-trigger title="知识库"><span aria-hidden="true">▤</span><em>知识库</em></button>' +
      '<button data-nav-action="files" title="上传文件"><span aria-hidden="true">⌁</span><em>文件</em></button>' +
      '<button data-nav-action="history" title="会话记录"><span aria-hidden="true">◷</span><em>记录</em></button>' +
      '<div class="ev-agent-mascot" title="EV Agent" aria-hidden="true">EV</div>' +
      '</nav>' +
      '<div class="ev-agent-body">' +
      '<div class="ev-agent-recommendations" aria-label="推荐问题">' +
      '<button data-recommendation="输变电工程三维设计模型框架包括哪四个层次？">三维设计模型框架包括哪四个层次？</button>' +
      '<button data-recommendation="按照现行规范检查我上传的方案">按照现行规范检查当前方案</button>' +
      '</div>' +
      '<div class="ev-agent-attachment-cards" data-attachment-cards hidden></div>' +
      '<div class="ev-agent-timeline" data-timeline></div>' +
      '<button class="ev-agent-bottom" data-bottom hidden>回到底部</button>' +
      '<div class="ev-agent-task" data-task hidden></div>' +
      '<div class="ev-agent-quick" data-quick-actions hidden></div>' +
      '<div class="ev-agent-document-scope" data-document-scope>基于：输变电知识库</div>' +
      '<div class="ev-agent-composer">' +
      '<button type="button" class="ev-agent-attach-button" data-attach-trigger title="上传会话文件" aria-label="上传会话文件">⌁</button>' +
      '<textarea data-query rows="1" placeholder="向 EV Agent 提问"></textarea>' +
      '<button type="button" class="ev-agent-send-button" data-send data-state="idle" title="发送" aria-label="发送 Agent 请求">' +
      '<span class="send-label">↑</span>' +
      '<span class="stop-indicator" hidden><span class="spinner-ring"></span><span class="stop-square"></span></span>' +
      '</button></div></div>' +
      '<aside class="ev-agent-diagnostics" data-diagnostics hidden aria-label="开发者面板">' +
      '<div class="ev-agent-dev-heading"><strong>开发者</strong><button data-dev-close aria-label="关闭开发者面板">×</button></div>' +
      '<div class="ev-agent-context" data-context>当前对象：未选择</div>' +
      '<div data-conversation-slot></div>' +
      '<div class="ev-agent-tabs"><button data-tab="tasks">任务</button><button data-tab="events">UI Events</button><button data-tab="semantic">语义解析</button><button data-tab="messages">原始消息</button><button data-tab="errors">错误 <span data-error-count></span></button><button data-tab="state">状态</button></div>' +
      '<div class="ev-agent-dev-scroll"><pre data-raw-events></pre><button class="ev-agent-dev-bottom" data-dev-bottom hidden>回到底部</button></div>' +
      '<div class="ev-agent-dev-actions"><button data-clear>清空</button><button data-export>导出</button><button data-mock>设置测试对象</button></div>' +
      '</aside></div>' +
      '<div class="ev-agent-test-object-dialog" data-test-dialog hidden role="dialog" aria-modal="true" aria-label="设置测试对象">' +
      '<div class="ev-agent-test-object-card"><strong>设置测试对象</strong>' +
      '<label>对象名称<input data-test-name maxlength="200"></label>' +
      '<label>对象 ID<input data-test-id maxlength="200"></label>' +
      '<label>对象类型<input data-test-type maxlength="100"></label>' +
      '<label>行政区名称或 adcode<input data-test-admin maxlength="100"></label>' +
      '<label>备注<textarea data-test-note rows="2" maxlength="500"></textarea></label>' +
      '<div class="ev-agent-validation" data-test-error hidden></div>' +
      '<div class="ev-agent-test-actions"><button data-test-save>保存</button><button data-test-clear>清除测试对象</button><button data-test-cancel>取消</button></div>' +
      '</div></div>' +
      '<div class="ev-agent-resize" data-resize></div>';
    traceBootstrapStage("panel content created", {
      titlebar: !!panel.querySelector("[data-drag-handle]"),
      status: !!panel.querySelector("[data-status]"),
      timeline: !!panel.querySelector("[data-timeline]"),
      input: !!panel.querySelector("[data-query]"),
      send: !!panel.querySelector("[data-send]")
    });
    document.body.appendChild(panel);
    applyEnvironmentVisibility(panel, config);
    clampPanelToViewport(panel);
    makeDraggable(panel, panel.querySelector("[data-drag-handle]"));
    makeResizable(panel, panel.querySelector("[data-resize]"));
    preventGlobePointerLeak(panel);
    if (!config.skipStreamScripts) ensureStreamScripts(config);
    state.panel = panel;
    setRuntimeState(state.runtimeState.state, { panel_created: true, panel_visible: !panel.hidden });
    state.statusNode = panel.querySelector("[data-status]");
    state.timelineNode = panel.querySelector("[data-timeline]");
    state.taskNode = panel.querySelector("[data-task]");
    state.contextNode = panel.querySelector("[data-context]");
    state.quickActionsNode = panel.querySelector("[data-quick-actions]");
    state.diagnosticsNode = panel.querySelector("[data-diagnostics]");
    state.errorNode = panel.querySelector("[data-error]");
    state.messagesNode = panel.querySelector("[data-raw-events]");
    state.rawEventsNode = panel.querySelector("[data-raw-events]");
    state.devBottomNode = panel.querySelector("[data-dev-bottom]");
    state.devScrollNode = panel.querySelector(".ev-agent-dev-scroll");
    renderQuickActions();
    renderContextBar();
    panel.querySelector("[data-close]").addEventListener("click", function () {
      closePanel();
    });
    panel.querySelector("[data-minimize]").addEventListener("click", function () {
      toggleMinimized();
    });
    if (panel.querySelector("[data-devtools]")) panel.querySelector("[data-devtools]").addEventListener("click", function () {
      toggleDiagnostics();
    });
    if (panel.querySelector("[data-dev-close]")) panel.querySelector("[data-dev-close]").addEventListener("click", function () {
      toggleDiagnostics(false);
    });
    Array.prototype.forEach.call(panel.querySelectorAll("[data-recommendation]"), function (button) {
      button.addEventListener("click", function () { sendFromPanel(button.dataset.recommendation || button.textContent); });
    });
    Array.prototype.forEach.call(panel.querySelectorAll("[data-nav-action]"), function (button) {
      button.addEventListener("click", function () {
        var ui = global.EVConversationUI;
        if (!ui) return;
        if (button.dataset.navAction === "new" && ui.create) ui.create();
        if (button.dataset.navAction === "files" && ui.openFilePicker) ui.openFilePicker();
        if (button.dataset.navAction === "history") {
          toggleDiagnostics(true);
          if (ui.focusHistory) ui.focusHistory();
        }
      });
    });
    if (panel.querySelector("[data-clear]")) panel.querySelector("[data-clear]").addEventListener("click", function () {
      clearSession();
    });
    if (panel.querySelector("[data-export]")) panel.querySelector("[data-export]").addEventListener("click", function () {
      exportSession();
    });
    panel.querySelector("[data-send]").addEventListener("click", function () {
      var input = panel.querySelector("[data-query]");
      if (isTaskRunning()) {
        cancelCurrentAgentTask();
        return;
      }
      submitPanelInput(input);
    });
    panel.querySelector("[data-query]").addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        if (!isTaskRunning()) submitPanelInput(event.target);
      }
      if (event.key === "Escape" && state.currentController) {
        cancelCurrentAgentTask();
      }
    });
    panel.querySelector("[data-query]").addEventListener("input", function (event) {
      event.target.style.height = "auto";
      event.target.style.height = Math.min(160, Math.max(40, event.target.scrollHeight)) + "px";
    });
    global.addEventListener("agent:attachment-error", function (event) {
      var detail = event.detail || {};
      state.errors.push({ code: detail.code || "ATTACHMENT_REQUEST_FAILED", message: detail.message || "文件操作失败", created_at: new Date().toISOString() });
      if (state.errors.length > 200) state.errors.shift();
      renderDiagnostics();
    });
    if (panel.querySelector("[data-mock]")) panel.querySelector("[data-mock]").addEventListener("click", openTestObjectDialog);
    Array.prototype.forEach.call(typeof panel.querySelectorAll === "function" ? panel.querySelectorAll("[data-tab]") : [], function (button) {
      button.addEventListener("click", function () { setDiagnosticTab(button.dataset.tab); });
    });
    panel.querySelector("[data-test-save]").addEventListener("click", saveTestObjectFromDialog);
    panel.querySelector("[data-test-clear]").addEventListener("click", clearTestObject);
    panel.querySelector("[data-test-cancel]").addEventListener("click", closeTestObjectDialog);
    panel.querySelector("[data-bottom]").addEventListener("click", function () { scrollAreaToBottom("timeline", true); });
    panel.querySelector("[data-dev-bottom]").addEventListener("click", function () { scrollAreaToBottom(state.activeDiagnosticTab, true); });
    panel.querySelector("[data-timeline]").addEventListener("scroll", function () { handleAreaScroll("timeline"); });
    state.devScrollNode.addEventListener("scroll", function () { handleAreaScroll(state.activeDiagnosticTab); });
    setDiagnosticTab("tasks");
    bindGlobalShortcut();
    traceBootstrapStage("handlers bound", { shortcut: state.shortcutBound });
  }

  function applyEnvironmentVisibility(panel, config) {
    if (!panel || !isProductionMode(config)) return;
    [
      "[data-settings]",
      "[data-export]",
      "[data-mock]"
    ].forEach(function (selector) {
      var node = panel.querySelector(selector);
      if (node && node.parentNode) node.parentNode.removeChild(node);
    });
    if (state.diagnosticsNode) state.diagnosticsNode.hidden = true;
  }

  function isProductionMode(config) {
    config = config || state.currentConfig || {};
    return String(config.environment || config.mode || "").toLowerCase() === "production" || config.devMode === false || config.debug === false && config.production === true;
  }

  function ensureLauncher(config) {
    if (state.launcher) return state.launcher;
    installPanelStyles();
    var launcher = document.createElement("button");
    launcher.id = "ev-agent-launcher";
    launcher.className = "ev-agent-launcher";
    launcher.type = "button";
    launcher.textContent = "EV Agent";
    launcher.addEventListener("click", function () {
      if (state.runtimeState.state === "offline") {
        openOfflinePanel(state.health || {});
        return;
      }
      if (state.runtimeState.state === "webgl_waiting") {
        openStatusPanel("WEBGL_NOT_READY", "WebGL scene is not ready.", state.runtimeState.error);
        return;
      }
      if (state.panel) openPanel();
    });
    document.body.appendChild(launcher);
    state.launcher = launcher;
    return launcher;
  }

  function setLauncherState(kind, label) {
    if (!state.launcher) return;
    state.launcher.dataset.state = kind || "";
    state.launcher.textContent = label || "EV Agent";
    state.launcher.hidden = !!(state.panel && state.panel.hidden !== true && !state.closed);
  }

  function renderOfflineLauncher(health) {
    ensureLauncher(state.currentConfig || DEFAULT_CONFIG);
    setLauncherState("offline", "EV Agent offline");
    state.launcher.title = health && health.message || "EV Agent service is not connected";
  }

  function hideFullPanel() {
    if (!state.panel) {
      setRuntimeState(state.runtimeState.state, { panel_created: false, panel_visible: false });
      return;
    }
    state.panel.hidden = true;
    setRuntimeState(state.runtimeState.state, { panel_created: true, panel_visible: false });
  }

  function openOfflinePanel(health) {
    var config = merge(state.currentConfig || DEFAULT_CONFIG, { skipStreamScripts: true });
    ensurePanel(config);
    renderOfflinePanel(health);
    state.panel.hidden = false;
    state.closed = false;
    setRuntimeState("offline", {
      backend_online: false,
      panel_created: true,
      panel_visible: true,
      error: {
        code: health && health.error_code || "AGENT_BACKEND_OFFLINE",
        message: health && health.message || "EV Agent service is not connected."
      }
    });
    setStatus("Agent offline");
  }

  function openStatusPanel(code, message, detail) {
    var config = merge(state.currentConfig || DEFAULT_CONFIG, { skipStreamScripts: true });
    ensurePanel(config);
    renderStatusPanel(code, message, detail);
    state.panel.hidden = false;
    state.closed = false;
    setRuntimeState("webgl_waiting", {
      panel_created: true,
      panel_visible: true,
      error: { code: code, message: message, detail: detail || null }
    });
    setStatus(message);
  }

  function renderOfflinePanel(health) {
    renderStatusPanel(
      "AGENT_BACKEND_OFFLINE",
      "EV Agent service is not connected.",
      {
        backend_url: trimTrailingSlash((state.currentConfig || DEFAULT_CONFIG).agentBaseUrl),
        detail: health && health.message || "Health check failed."
      }
    );
  }

  function renderStatusPanel(code, message, detail) {
    if (!state.panel) return;
    var input = state.panel.querySelector("[data-query]");
    var send = state.panel.querySelector("[data-send]");
    if (input) input.disabled = true;
    if (send) send.disabled = true;
    if (state.timelineNode) {
      replaceChildren(state.timelineNode);
      var card = document.createElement("div");
      card.className = "ev-agent-card error_card";
      card.innerHTML =
        '<strong>' + escapeHtml(message || "EV Agent initialization failed.") + '</strong>' +
        '<div style="color:var(--ev-agent-text-secondary);margin-top:6px;">Backend URL: ' +
        escapeHtml(trimTrailingSlash((state.currentConfig || DEFAULT_CONFIG).agentBaseUrl)) +
        '</div>' +
        '<div class="ev-agent-card-actions">' +
        '<button data-retry-backend>Reconnect</button>' +
        '<button data-close-offline>Close</button>' +
        '<button data-show-offline-detail>Details</button>' +
        '</div>' +
        '<pre data-offline-detail hidden style="white-space:pre-wrap;margin-top:8px;">' +
        escapeHtml(stringify({ code: code, detail: detail || null })) +
        '</pre>';
      state.timelineNode.appendChild(card);
      card.querySelector("[data-retry-backend]").addEventListener("click", retryAgentConnection);
      card.querySelector("[data-close-offline]").addEventListener("click", function () {
        hideFullPanel();
      });
      card.querySelector("[data-show-offline-detail]").addEventListener("click", function () {
        var detailNode = card.querySelector("[data-offline-detail]");
        detailNode.hidden = !detailNode.hidden;
      });
    }
    if (state.taskNode) state.taskNode.textContent = "Offline";
    renderDiagnostics();
  }

  function retryAgentConnection() {
    var config = state.currentConfig || normalizeConfig(merge(DEFAULT_CONFIG, global.EV_AGENT_CONFIG || {}));
    setRuntimeState("checking_backend", { page_enabled: true, frontend_ready: true });
    setLauncherState("checking", "EV Agent checking...");
    checkAgentBackendHealth(config).then(function (health) {
      state.health = health;
      if (!health.online) {
        renderOfflineLauncher(health);
        if (state.panel && !state.panel.hidden) renderOfflinePanel(health);
        setRuntimeState("offline", {
          backend_online: false,
          error: { code: health.error_code || "AGENT_BACKEND_OFFLINE", message: health.message }
        });
        return;
      }
      setRuntimeState("checking_backend", { backend_online: true, error: null });
      ensureSelectedObjectBridge(config, function () {
        ensureEVPickPatch(config, function () {
          waitForReady(config, 0);
        });
      });
    }).catch(function (error) {
      var health = {
        online: false,
        error_code: "AGENT_BACKEND_HEALTH_FAILED",
        message: error && error.message ? error.message : String(error)
      };
      state.health = health;
      renderOfflineLauncher(health);
      if (state.panel && !state.panel.hidden) renderOfflinePanel(health);
      setRuntimeState("offline", {
        backend_online: false,
        error: { code: health.error_code, message: health.message }
      });
    });
  }

  function sendFromPanel(query) {
    query = String(query || "").trim();
    if (!query) return;
    if (isTaskRunning()) {
      cancelCurrentAgentTask();
      return;
    }
    if (!state.adapter) {
      appendErrorCard("Agent 适配器尚未挂接。");
      return;
    }
    if (!global.EVAgentStreamClient || !global.AgentUIEventQueue) {
      appendWarning("流式客户端未加载，已尝试使用兼容模式。");
      state.adapter.sendQuery(query).catch(function (error) {
        appendErrorCard(error && error.message ? error.message : stringify(error));
      });
      return;
    }
    var controller = new AbortController();
    var taskId = "task_" + Date.now();
    var requestId = "request_" + Date.now() + "_" + Math.random().toString(16).slice(2);
    state.currentController = controller;
    state.processedEventKeys = {};
    state.activeTask = createTask(taskId, query, requestId);
    state.panelRuntime.activeRequestId = requestId;
    state.panelRuntime.activeTaskId = taskId;
    state.panelRuntime.abortController = controller;
    state.panelRuntime.startedAt = Date.now();
    state.panelRuntime.completedAt = null;
    state.panelRuntime.responseFinished = false;
    state.panelRuntime.streamNode = null;
    state.activeRequestStatus = "running";
    setAgentPanelState("submitting", "提交中");
    state.uiEventQueue = new global.AgentUIEventQueue({
      bridge: global.WebGLAgentBridge,
      taskId: taskId,
      requestId: requestId,
      handlerTimeoutMs: state.currentConfig && state.currentConfig.handlerTimeoutMs,
      onUpdate: function (item, items) {
        if (!isCurrentTask(taskId, requestId)) return;
        state.uiEventResults = items || [];
        updateUIEventLedger(items || []);
        syncBufferLedger(item);
        syncBufferQueryLedger(item);
        if (item && item.object_resolution && item.object_resolution.status === "resolved") {
          rememberObjectResolution(item.object_resolution, item);
          if (!item.__objectCardShown) {
            item.__objectCardShown = true;
            addObjectCard(item.object_resolution);
          }
        }
        syncObjectFromUIEventResult(item);
        renderCompletedToolResult(item);
        updateTaskFromQueue(items || []);
        renderTask();
        renderDiagnostics();
        maybeFinishActiveTask();
      }
    });
    state.panelRuntime.eventQueue = state.uiEventQueue;
    setRequestActive(true, "提交中");
    state.scrollFollow.timeline = true;
    appendTimelineMessage("user_message", query);
    var assistantNode = appendTimelineMessage("streaming_assistant_message", "", {
      message_id: "assistant_" + requestId,
      request_id: requestId,
      kind: "assistant_stream"
    });
    state.panelRuntime.streamNode = assistantNode;
    var payload = buildStreamPayload(query);
    payload.request_id = requestId;
    payload.task_id = taskId;
    var conversationId = payload.conversation_id;
    var streamEndpoint = trimTrailingSlash(state.currentConfig.agentBaseUrl) + "/api/agent/chat/stream";
    var fallbackEndpoint = trimTrailingSlash(state.currentConfig.agentBaseUrl) + "/agent/chat";

    global.EVAgentStreamClient.streamAgentQuery({
      endpoint: streamEndpoint,
      fallbackEndpoint: fallbackEndpoint,
      payload: payload,
      requestId: requestId,
      signal: controller.signal,
      onEvent: function (event) {
        if (!isCurrentTask(taskId, requestId)) return false;
        var eventConversationId = event && event.data && event.data.conversation_id;
        var currentConversationId = global.EVConversationUI && global.EVConversationUI.currentConversationId() || sessionStorage.getItem("ev_agent_session_id");
        if ((eventConversationId && eventConversationId !== conversationId) || currentConversationId !== conversationId) return false;
        if (!markEventProcessed(requestId, event)) return false;
        recordRawEvent(event);
        return true;
      },
      onConnected: function (data) {
        if (!isCurrentTask(taskId, requestId)) return;
        state.transportMode = data.transport_mode || "sse";
        setAgentPanelState("streaming", "生成中");
      },
      onStatus: function (data) {
        if (!isCurrentTask(taskId, requestId)) return;
        setAgentPanelState(stageToRuntimeState(data.stage), data.message || statusLabel(stageToRuntimeState(data.stage)));
        updateTaskStage(data.stage, data.message);
      },
      onToken: function (data) {
        if (!isCurrentTask(taskId, requestId)) return;
        appendAssistantToken(assistantNode, data.delta || "");
      },
      onObject_resolution: function (data) {
        if (!isCurrentTask(taskId, requestId)) return;
        addObjectCard(data);
        updateTaskStage("resolving_object", data.name ? "识别对象：" + data.name : "解析对象");
      },
      onObjectResolution: function (data) {
        if (!isCurrentTask(taskId, requestId)) return;
        addObjectCard(data);
        updateTaskStage("resolving_object", data.name ? "识别对象：" + data.name : "解析对象");
      },
      onSemantic_trace: function (data) {
        if (!isCurrentTask(taskId, requestId)) return;
        rememberSemanticTrace(data);
      },
      onSemanticTrace: function (data) {
        if (!isCurrentTask(taskId, requestId)) return;
        rememberSemanticTrace(data);
      },
      onUI_event: function (data) {
        if (!isCurrentTask(taskId, requestId)) return;
        enqueueUIEvent(data);
      },
      onUi_event: function (data) {
        if (!isCurrentTask(taskId, requestId)) return;
        enqueueUIEvent(data);
      },
      onUIEvent: function (data) {
        if (!isCurrentTask(taskId, requestId)) return;
        enqueueUIEvent(data);
      },
      onWarning: function (data) {
        if (!isCurrentTask(taskId, requestId)) return;
        appendWarning(data.message || stringify(data));
      },
      onError: function (data) {
        if (!isCurrentTask(taskId, requestId)) return;
        failStreamingMessage(assistantNode, data.code || data.error_code || "REQUEST_FAILED", userReadableError(data.code || data.error_code, data.message), taskId, requestId);
        appendErrorCard(userReadableError(data.code || data.error_code, data.message));
        state.panelRuntime.responseFinished = true;
        failActiveTask("执行失败");
      },
      onDone: function (data) {
        if (!isCurrentTask(taskId, requestId)) return;
        if (data.conversation_id && data.conversation_id !== conversationId) return;
        if (data.trace_id && state.activeTask) state.activeTask.trace_id = data.trace_id;
        if (!String(assistantNode && assistantNode.textContent || "").trim()) {
          updateStreamingNode(assistantNode, "请求处理已结束，正在汇总地图执行结果。");
        }
        finishStreamingNode(assistantNode, "completed");
        state.transportMode = data.transport_mode || state.transportMode;
        state.panelRuntime.responseFinished = true;
        updateTaskStage(hasPendingUIEvents() ? "executing" : "completed", hasPendingUIEvents() ? "正在执行地图操作" : "已完成");
        maybeFinishActiveTask();
        global.dispatchEvent(new CustomEvent("agent:conversation-updated", { detail: { conversation_id: conversationId, request_id: requestId } }));
      },
      onAbort: function () {
        if (!isCurrentTask(taskId, requestId)) return;
        finishStreamingNode(assistantNode, "canceled");
        cancelCurrentAgentTask({ fromAbortCallback: true });
      },
      onMalformed: function (data) {
        if (!isCurrentTask(taskId, requestId)) return;
        recordRawEvent({ event: "malformed", data: data });
      }
    }).catch(function (error) {
      if (controller.signal.aborted) return;
      var message = userReadableError(error && error.code, error && error.message);
      failStreamingMessage(assistantNode, error && error.code || "REQUEST_FAILED", message, taskId, requestId);
      appendErrorCard(message);
      state.panelRuntime.responseFinished = true;
      failActiveTask("连接失败");
    });
  }

  function submitPanelInput(textarea) {
    if (!textarea) return;
    var requestText = String(textarea.value || "").trim();
    if (!requestText || isTaskRunning()) return;
    textarea.value = "";
    textarea.style.height = "";
    try {
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    } catch (error) {}
    sendFromPanel(requestText);
  }

  function openTestObjectDialog() {
    if (!state.panel) return;
    var dialog = state.panel.querySelector("[data-test-dialog]");
    if (!dialog) return;
    var current = state.testObject || {};
    state.panel.querySelector("[data-test-name]").value = current.object_name || "";
    state.panel.querySelector("[data-test-id]").value = current.object_id || "";
    state.panel.querySelector("[data-test-type]").value = current.object_type || "";
    state.panel.querySelector("[data-test-admin]").value = current.admin_name || current.adcode || "";
    state.panel.querySelector("[data-test-note]").value = current.note || "";
    var errorNode = state.panel.querySelector("[data-test-error]");
    errorNode.hidden = true;
    errorNode.textContent = "";
    dialog.hidden = false;
    state.panel.querySelector("[data-test-name]").focus();
  }

  function closeTestObjectDialog() {
    if (!state.panel) return;
    var dialog = state.panel.querySelector("[data-test-dialog]");
    if (dialog) dialog.hidden = true;
  }

  function saveTestObjectFromDialog() {
    if (!state.panel) return;
    var name = String(state.panel.querySelector("[data-test-name]").value || "").trim();
    var objectId = String(state.panel.querySelector("[data-test-id]").value || "").trim();
    var objectType = String(state.panel.querySelector("[data-test-type]").value || "").trim();
    var adminValue = String(state.panel.querySelector("[data-test-admin]").value || "").trim();
    var note = String(state.panel.querySelector("[data-test-note]").value || "").trim();
    var errorNode = state.panel.querySelector("[data-test-error]");
    if (!adminValue && (!name || !objectId)) {
      errorNode.textContent = "普通对象必须填写名称和 ID；行政区必须填写名称或 adcode。";
      errorNode.hidden = false;
      return;
    }
    var isAdcode = /^\d{6,12}$/.test(adminValue);
    state.testObject = {
      object_name: name || (!isAdcode ? adminValue : ""),
      object_id: objectId || null,
      object_type: objectType || (adminValue ? "administrative_region" : "test_object"),
      admin_name: adminValue && !isAdcode ? adminValue : null,
      adcode: isAdcode ? adminValue : null,
      note: note,
      source: "panel_test_object",
      is_test_object: true,
      is_admin_region: !!adminValue
    };
    global.__EV_AGENT_TEST_OBJECT__ = state.testObject;
    closeTestObjectDialog();
    renderContextBar();
    renderQuickActions();
    appendTimelineMessage("system_status_message", "测试对象已设置：" + (state.testObject.object_name || state.testObject.adcode || state.testObject.object_id));
  }

  function clearTestObject() {
    state.testObject = null;
    global.__EV_AGENT_TEST_OBJECT__ = null;
    closeTestObjectDialog();
    renderContextBar();
    renderQuickActions();
    appendTimelineMessage("system_status_message", "测试对象已清除；真实选择和最近上下文未改变。");
  }

  function ensureStreamScripts(config) {
    loadScriptOnce("ev-agent-stream-client", trimTrailingSlash(config.agentBaseUrl) + "/ev_agent_stream_client.js");
    loadScriptOnce("ev-agent-ui-event-queue", trimTrailingSlash(config.agentBaseUrl) + "/ev_agent_ui_event_queue.js");
    loadScriptOnce("ev-conversation-client", trimTrailingSlash(config.agentBaseUrl) + "/conversation_client.js");
    loadScriptOnce("ev-attachment-client", trimTrailingSlash(config.agentBaseUrl) + "/attachment_client.js");
    loadScriptOnce("ev-conversation-ui", trimTrailingSlash(config.agentBaseUrl) + "/conversation_ui.js");
    loadScriptOnce("ev-knowledge-manager-ui", trimTrailingSlash(config.agentBaseUrl) + "/knowledge_manager_ui.js");
  }

  function loadScriptOnce(id, src) {
    if (document.getElementById(id)) return;
    var script = document.createElement("script");
    script.id = id;
    script.async = true;
    script.src = src;
    script.onerror = function () {
      warn("script load failed", src);
    };
    document.head.appendChild(script);
  }

  function buildStreamPayload(query) {
    var selected = getSelectedObject();
    var lastContextObject = state.panelRuntime.currentObject || null;
    var lastAdminRegion = state.panelRuntime.currentAdminRegion || null;
    if (selected && !selected.object_id) selected = null;
    var conversationId = global.EVConversationUI && global.EVConversationUI.currentConversationId() || sessionStorage.getItem("ev_agent_session_id") || "webgl-session";
    var userId = state.currentConfig && state.currentConfig.userId || "webgl-user";
    return {
      session_id: conversationId,
      user_id: userId,
      role: state.currentConfig && state.currentConfig.role || "developer",
      query: query,
      conversation_id: conversationId,
      message: query,
      gis_context: {
        selected_object: selected,
        last_context_object: lastContextObject,
        last_admin_region: lastAdminRegion,
        last_buffer: state.buffers.length ? state.buffers[state.buffers.length - 1] : null,
        last_buffer_query: state.bufferQueries.length ? state.bufferQueries[state.bufferQueries.length - 1] : null,
        buffers: state.buffers,
        buffer_queries: state.bufferQueries,
        dialogue_state: state.dialogueState,
        test_object: state.testObject || null,
        active_layers: global.currentActiveLayers || [],
        camera_state: {},
        current_object_id: selected && selected.object_id || null
      },
      context: {
        selected_object: selected,
        last_context_object: lastContextObject,
        last_admin_region: lastAdminRegion,
        test_object: state.testObject || null,
        current_object_id: selected && selected.object_id || null,
        camera_state: {},
        active_layers: global.currentActiveLayers || []
      }
    };
  }

  function createTask(taskId, query, requestId) {
    var task = {
      task_id: taskId,
      request_id: requestId || null,
      trace_id: null,
      query: query,
      stage: "understanding",
      status: "understanding",
      collapsed: false,
      responseFinished: false,
      pendingUIEvents: 0,
      runningUIEvents: 0,
      startedAt: Date.now(),
      completedAt: null,
      steps: [
        { label: "理解请求", status: "running" },
        { label: "识别对象", status: "pending" },
        { label: "规划操作", status: "pending" },
        { label: "生成回答", status: "pending" },
        { label: "执行地图事件", status: "pending" }
      ]
    };
    state.activeTask = task;
    state.taskHistory.push(task);
    if (state.taskHistory.length > 100) state.taskHistory.shift();
    renderTask();
    return task;
  }

  function updateTaskStage(stage, message) {
    if (!state.activeTask) return;
    state.activeTask.stage = stage || state.activeTask.stage;
    state.activeTask.status = stage || state.activeTask.status;
    state.activeTask.message = message || "";
    var order = ["understanding", "resolving_object", "planning", "generating", "executing", "completed"];
    var index = order.indexOf(stage);
    state.activeTask.steps.forEach(function (step, stepIndex) {
      if (stage === "canceled") step.status = step.status === "success" ? "success" : "canceled";
      else if (stage === "failed") step.status = step.status === "success" ? "success" : "failed";
      else if (stage === "awaiting_clarification") step.status = stepIndex < 4 ? "success" : "pending";
      else if (index < 0) return;
      else if (stepIndex < index) step.status = "success";
      else if (stepIndex === index) step.status = stage === "completed" ? "success" : "running";
      else step.status = "pending";
    });
    renderTask();
  }

  function renderTask() {
    if (!state.taskNode) return;
    var task = state.activeTask;
    if (!task) {
      state.taskNode.textContent = "";
      state.taskNode.hidden = true;
      return;
    }
    state.taskNode.hidden = false;
    replaceChildren(state.taskNode);
    state.taskNode.dataset.status = task.status || "running";
    var summary = document.createElement("div");
    summary.className = "ev-agent-task-summary";
    var statusText = statusLabel(task.status);
    var elapsed = task.completedAt ? formatDuration(task.completedAt - task.startedAt) : formatDuration(Date.now() - task.startedAt);
    summary.innerHTML =
      '<span class="ev-agent-task-state">' + escapeHtml(taskIcon(task.status) + " " + statusText) + '</span>' +
      '<span class="ev-agent-task-desc">' + escapeHtml(task.message || summarizeTask(task)) + '</span>' +
      '<span class="ev-agent-task-time">' + escapeHtml(elapsed) + '</span>';
    state.taskNode.appendChild(summary);
    renderDiagnostics();
  }

  function launchCustomerMode(config, health) {
    var customerConfig = merge(config, {
      __serverModeResolved: true,
      mode: "customer",
      devMode: false,
      role: "user",
      debug: false,
      showPanel: false
    });
    state.currentConfig = customerConfig;
    setRuntimeState(health && health.online ? "ready" : "offline", {
      page_enabled: true,
      frontend_ready: true,
      backend_online: !!(health && health.online),
      webgl_ready: !!(global.viewer && global.viewer.scene),
      panel_created: false,
      panel_visible: true,
      error: health && health.online ? null : { message: "服务暂时不可用" }
    });
    if (health && health.online) {
      initializeCustomerWebGLRuntime(customerConfig);
    }
    var script = document.getElementById("ev-customer-agent-app");
    if (script) {
      if (global.EVCustomerAgentApp) global.EVCustomerAgentApp.start(customerConfig, health);
      return;
    }
    script = document.createElement("script");
    script.id = "ev-customer-agent-app";
    script.src = versionedFrontendAsset(
      trimTrailingSlash(config.agentBaseUrl) + "/frontend/customer_agent_app.js",
      config.releaseId || config.release_id
    );
    script.onload = function () {
      if (global.EVCustomerAgentApp) global.EVCustomerAgentApp.start(customerConfig, health);
    };
    script.onerror = function () {
      setRuntimeState("failed", { error: { message: "客户界面加载失败" } });
    };
    document.head.appendChild(script);
  }

  function versionedFrontendAsset(source, version) {
    var value = String(version || "development").trim();
    return source + (source.indexOf("?") >= 0 ? "&" : "?") + "v=" + encodeURIComponent(value);
  }

  function initializeCustomerWebGLRuntime(config) {
    ensureSelectedObjectBridge(config, function () {
      ensureEVPickPatch(config, function () {
        waitForReady(config, 0);
      });
    });
  }

  function updateTaskFromQueue(items) {
    if (!state.activeTask) return;
    items = items || [];
    var pending = items.filter(function (item) {
      return item.status === "queued" || item.status === "pending";
    }).length;
    var running = items.filter(function (item) {
      return item.status === "executing";
    }).length;
    state.activeTask.pendingUIEvents = pending;
    state.activeTask.runningUIEvents = running;
    if (pending || running) {
      updateTaskStage("executing", running ? "正在执行地图操作" : "等待地图操作");
      setAgentPanelState("executing", running ? "执行中" : "等待中");
    } else if (state.panelRuntime.responseFinished) {
      updateTaskStage("completed", "已完成");
    }
  }

  function stageToRuntimeState(stage) {
    return ({
      understanding: "streaming",
      submitting: "submitting",
      connecting: "connecting",
      reading_document: "streaming",
      analyzing_sections: "streaming",
      generating_summary: "streaming",
      resolving_object: "resolving_object",
      object_resolution: "resolving_object",
      planning: "streaming",
      generating: "streaming",
      executing: "executing",
      completed: "completed",
      failed: "failed",
      canceled: "canceled"
    })[stage] || "streaming";
  }

  function markEventProcessed(requestId, event) {
    event = event || {};
    var data = event.data || {};
    var key = [
      requestId || "-",
      event.event || "message",
      data.event_id || data.sequence || data.index || state.rawEvents.length,
      data.delta || data.stage || data.type || ""
    ].join("|");
    if (state.processedEventKeys[key]) return false;
    state.processedEventKeys[key] = true;
    return true;
  }

  function isCurrentTask(taskId, requestId) {
    return !!(
      state.activeTask &&
      state.activeTask.task_id === taskId &&
      state.panelRuntime.activeTaskId === taskId &&
      state.panelRuntime.activeRequestId === requestId
    );
  }

  function statusLabel(status) {
    if (status === "partial_success") return "部分完成";
    return ({
      idle: "空闲",
      pending: "等待中",
      queued: "等待中",
      running: "执行中",
      executing: "执行中",
      success: "成功",
      completed: "已完成",
      failed: "失败",
      canceled: "已取消",
      awaiting_clarification: "等待补充信息",
      understanding: "执行中",
      reading_document: "正在读取文件",
      analyzing_sections: "正在分析章节",
      generating_summary: "正在生成摘要",
      resolving_object: "解析中",
      planning: "规划中",
      generating: "生成中",
      streaming: "生成中",
      submitting: "提交中",
      connecting: "连接中"
    })[status] || "执行中";
  }

  function taskIcon(status) {
    if (status === "completed" || status === "success") return "完成";
    if (status === "partial_success") return "部分";
    if (status === "failed") return "失败";
    if (status === "canceled") return "取消";
    if (status === "awaiting_clarification") return "等待";
    return "进行中";
  }
  function summarizeTask(task) {
    var object = state.panelRuntime.currentObject;
    if (object && object.name) return "定位并高亮" + object.name;
    return task.query || "执行 Agent 任务";
  }

  function formatDuration(ms) {
    return (Math.max(0, ms || 0) / 1000).toFixed(1) + " s";
  }

  function appendTimelineMessage(type, text, meta) {
    if (!state.timelineNode) return null;
    meta = meta || {};
    if (type !== "streaming_assistant_message" && text) {
      var last = state.timelineNode.lastElementChild;
      var lastContent = last && last.querySelector && last.querySelector(".ev-agent-message-content");
      if (last && last.className.indexOf(type) >= 0 && lastContent && lastContent.textContent === text) {
        return last;
      }
    }
    var node = document.createElement("div");
    node.className = "ev-agent-message " + type;
    if (meta.message_id) node.dataset.messageId = meta.message_id;
    if (meta.request_id) node.dataset.requestId = meta.request_id;
    if (meta.kind) node.dataset.kind = meta.kind;
    var content = document.createElement("div");
    content.className = "ev-agent-message-content";
    content.textContent = text || "";
    node.appendChild(content);
    var actions = document.createElement("div");
    actions.className = "ev-agent-message-actions";
    if (type.indexOf("assistant") >= 0) {
      var copy = document.createElement("button");
      copy.textContent = "复制";
      copy.addEventListener("click", function () {
        copyText(content.textContent || "");
      });
      actions.appendChild(copy);
    }
    if (actions.childNodes.length) node.appendChild(actions);
    var record = {
      type: type,
      text: text || "",
      message_id: meta.message_id || null,
      request_id: meta.request_id || null,
      created_at: new Date().toISOString()
    };
    node.__messageRecord = record;
    state.messageHistory.push(record);
    if (state.messageHistory.length > 500) state.messageHistory.shift();
    state.timelineNode.appendChild(node);
    if (type === "assistant_message") enhanceCitationChips(content);
    scheduleAreaScroll("timeline", false);
    renderDiagnostics();
    return node;
  }

  function loadConversationMessages(messages, conversationId) {
    if (!state.timelineNode) return;
    replaceChildren(state.timelineNode);
    state.messageHistory = [];
    state.activeTask = null;
    state.panelRuntime.activeRequestId = null;
    state.panelRuntime.activeTaskId = null;
    (messages || []).forEach(function (message) {
      var type = message.role === "user" ? "user_message" : (message.status === "failed" ? "assistant_message is-error" : "assistant_message");
      appendTimelineMessage(type, message.content || "", {
        message_id: message.message_id,
        request_id: message.request_id,
        kind: message.role
      });
    });
    state.dialogueState = null;
    state.messageHistory.forEach(function (item) { item.conversation_id = conversationId || null; });
    renderDiagnostics();
  }

  function appendAssistantToken(node, delta) {
    if (!node) return;
    var content = node.querySelector(".ev-agent-message-content");
    if (!content) return;
    delta = String(delta || "");
    if (!delta) return;
    var current = content.textContent + (content.__pendingText || "");
    if (current && delta === current) return;
    if (current && current.slice(-delta.length) === delta) return;
    var pending = content.__pendingText || "";
    content.__pendingText = pending + delta;
    if (content.__raf) return;
    content.__raf = requestAnimationFrame(function () {
      content.textContent += content.__pendingText || "";
      content.__pendingText = "";
      content.__raf = null;
      if (node.__messageRecord) node.__messageRecord.text = content.textContent;
      scheduleAreaScroll("timeline", false);
      renderDiagnostics();
    });
  }

  function finishStreamingNode(node, status) {
    if (!node) return;
    node.className = node.className.replace("streaming_assistant_message", "assistant_message");
    node.setAttribute("data-status", status || "completed");
    enhanceCitationChips(node.querySelector(".ev-agent-message-content"));
  }

  function enhanceCitationChips(content) {
    if (!content || content.dataset.citationsEnhanced === "true") return;
    var text = content.textContent || "";
    var pattern = /\[(E\d+)\]/g;
    if (!pattern.test(text)) return;
    pattern.lastIndex = 0;
    var fragment = document.createDocumentFragment();
    var cursor = 0;
    var match;
    while ((match = pattern.exec(text))) {
      if (match.index > cursor) fragment.appendChild(document.createTextNode(text.slice(cursor, match.index)));
      var chip = document.createElement("button");
      chip.type = "button";
      chip.className = "ev-citation-chip";
      chip.textContent = "[" + match[1] + "]";
      chip.dataset.citationId = match[1];
      chip.title = "查看引用证据";
      chip.addEventListener("click", function () { toggleDiagnostics(true); setDiagnosticTab("semantic"); });
      fragment.appendChild(chip);
      cursor = pattern.lastIndex;
    }
    if (cursor < text.length) fragment.appendChild(document.createTextNode(text.slice(cursor)));
    replaceChildren(content);
    content.appendChild(fragment);
    content.dataset.citationsEnhanced = "true";
  }

  function failStreamingMessage(node, code, message, taskId, requestId) {
    if (!node) return;
    var content = node.querySelector(".ev-agent-message-content");
    if (content && !(content.textContent || content.__pendingText || "").trim()) content.textContent = message || "请求执行失败。";
    if (node.__messageRecord) {
      node.__messageRecord.text = content && content.textContent || message || "请求执行失败。";
      node.__messageRecord.status = "failed";
      node.__messageRecord.error_code = code || "REQUEST_FAILED";
      node.__messageRecord.task_id = taskId || null;
      node.__messageRecord.request_id = requestId || node.__messageRecord.request_id || null;
    }
    node.dataset.errorCode = code || "REQUEST_FAILED";
    finishStreamingNode(node, "failed");
  }

  function userReadableError(code, message) {
    return ({
      SPATIAL_QUERY_CENTER_REQUIRED: "无法确定范围查询的中心，请先选择对象、定位对象，或明确指定中心位置。",
      AGENT_PLAN_SCHEMA_INVALID: "本次请求的执行计划生成异常，系统未执行地图操作。请重试或使用更明确的对象名称。",
      AGENT_PLAN_REPAIR_FAILED: "本次请求的执行计划修复失败，系统未执行地图操作。请使用更明确的对象名称。",
      OBJECT_NOT_FOUND: "当前场景中没有找到该对象。",
      OBJECT_AMBIGUOUS: "找到多个同名对象，请根据 ID、类型或来源进一步指定。",
      BUFFER_TARGET_REQUIRED: "请先选择对象、定位对象，或明确指定缓冲区目标。",
      BUFFER_TARGET_NOT_FOUND: "当前场景中没有找到缓冲区目标。",
      BUFFER_TARGET_AMBIGUOUS: "找到多个同名缓冲区目标，请进一步指定。",
      BUFFER_GEOMETRY_UNAVAILABLE: "已找到目标，但当前数据源没有可用于缓冲区计算的空间位置或几何。",
      BUFFER_DISTANCE_REQUIRED: "请提供缓冲距离，例如500米或1公里。",
      BUFFER_DISTANCE_INVALID: "缓冲距离无效，请输入大于0的距离，例如500米或1公里。",
      BUFFER_DISTANCE_OUT_OF_RANGE: "缓冲距离超出当前安全范围。",
      BUFFER_SOURCE_UNAVAILABLE: "当前 WebGL 缓冲区计算组件不可用。",
      BUFFER_CALCULATION_FAILED: "缓冲区计算失败，请检查目标几何。",
      BUFFER_NOT_FOUND: "当前没有可查看或清除的缓冲区。",
      BUFFER_QUERY_UNSUPPORTED: "缓冲区已生成，但当前运行时对象数据不足，暂时无法可靠查询缓冲区内部对象。",
      BUFFER_NOT_RENDERED: "指定缓冲区当前未处于可查询的渲染状态。",
      SPATIAL_OBJECT_INDEX_EMPTY: "当前场景没有可建立空间索引的运行时对象。",
      SPATIAL_OBJECT_SOURCE_UNAVAILABLE: "当前运行时空间对象数据源不可用。",
      NO_QUERYABLE_OBJECTS: "当前筛选结果中没有具有可查询空间位置或几何的对象。",
      OBJECT_GEOMETRY_UNAVAILABLE: "对象存在，但没有可用于空间查询的 geometry。",
      BUFFER_QUERY_CALCULATION_FAILED: "缓冲区对象空间关系计算失败。",
      BUFFER_QUERY_HIGHLIGHT_UNSUPPORTED: "匹配对象缺少可安全恢复的高亮能力。"
      ,BUFFER_QUERY_RESULT_REQUIRED: "请先查询缓冲区内对象，再高亮查询结果。"
      ,QWEN_CONNECT_TIMEOUT: "当前模型服务暂时不可用，地图确定性操作仍可继续使用。"
      ,QWEN_READ_TIMEOUT: "当前模型服务暂时不可用，地图确定性操作仍可继续使用。"
      ,QWEN_DEADLINE_EXCEEDED: "当前模型服务暂时不可用，地图确定性操作仍可继续使用。"
      ,QWEN_CIRCUIT_OPEN: "当前模型服务暂时不可用，地图确定性操作仍可继续使用。"
      ,QWEN_UNAVAILABLE: "当前模型服务暂时不可用，地图确定性操作仍可继续使用。"
    })[code] || (message && !/Invalid AgentPlan|missing_center/.test(message) ? message : "请求执行失败，请查看错误详情后重试。");
  }

  function addObjectCard(data) {
    data = data || {};
    rememberObjectResolution(data);
    renderContextBar(data);
    var cardKey = data.object_id || data.query || data.name || data.object_name;
    if (cardKey && state.objectCardKeys[cardKey]) return;
    if (cardKey) state.objectCardKeys[cardKey] = true;
    var card = document.createElement("div");
    card.className = "ev-agent-card object_card";
    var title = document.createElement("strong");
    title.textContent = data.name || data.object_id || "Spatial object";
    card.appendChild(title);
    var fields = [
      ["类型", data.business_type || data.object_type || "-"],
      ["图层", data.layer_name || data.data_source_name || "-"],
      ["业务 ID", data.business_id || "-"],
      ["ID 来源", data.business_id_source || "-"],
      ["稳定 ID", data.stable_id ? "是" : "否"]
    ];
    fields.forEach(function (field) {
      var row = document.createElement("div");
      row.textContent = field[0] + ": " + field[1];
      card.appendChild(row);
    });
    var id = data.object_id;
    var actions = document.createElement("div");
    actions.className = "ev-agent-card-actions";
    addObjectAction(actions, "定位", "fly_to_object", id);
    addObjectAction(actions, "高亮", "gis_highlight", id);
    addObjectAction(actions, "清除高亮", "clear_highlight", id);
    addObjectAction(actions, "属性", "get_object_properties", id);
    card.appendChild(actions);
    state.timelineNode.appendChild(card);
    autoScrollTimeline();
    renderDiagnostics();
  }

  function rememberObjectResolution(data, sourceItem) {
    if (!data || !data.object_id && !data.query) return;
    var key = (data.object_id || "") + "|" + (data.query || "");
    var exists = state.objectResolutionResults.some(function (item) {
      return ((item.object_id || "") + "|" + (item.query || "")) === key;
    });
    if (!exists) state.objectResolutionResults.push(data);
    updateCurrentObject(data, sourceItem);
  }

  function updateCurrentObject(data, sourceItem) {
    data = data || {};
    var current = state.panelRuntime.currentObject || {};
    var payload = sourceItem && sourceItem.payload || {};
    var resultPayload = sourceItem ? unwrapBridgeResult(sourceItem.result) : {};
    var position = data.position || resultPayload.position || {};
    var center = data.center || resultPayload.center || {};
    var next = {
      object_id: pickFirst(data.object_id, payload.object_id, current.object_id),
      name: pickFirst(data.name, data.object_name, payload.object_name, current.name),
      object_name: pickFirst(data.name, data.object_name, payload.object_name, current.object_name),
      layer_name: pickFirst(data.layer_name, payload.layer_name, current.layer_name),
      data_source_name: pickFirst(data.data_source_name, payload.data_source_name, current.data_source_name),
      business_id: pickFirst(data.business_id, payload.business_id, current.business_id),
      business_id_source: pickFirst(data.business_id_source, payload.business_id_source, current.business_id_source),
      stable_id: data.stable_id === true || current.stable_id === true,
      highlighted: current.highlighted === true
      ,longitude: pickFirst(data.longitude, position.longitude, center.longitude, resultPayload.longitude, current.longitude)
      ,latitude: pickFirst(data.latitude, position.latitude, center.latitude, resultPayload.latitude, current.latitude)
      ,height: pickFirst(data.height, position.height, center.height, resultPayload.height, current.height)
      ,center: pickFirst(data.center, resultPayload.center, position.position_valid ? { longitude: position.longitude, latitude: position.latitude, height: position.height } : null, current.center)
      ,center_source: pickFirst(data.center_source, position.position_source, resultPayload.center_source, resultPayload.position_source, current.center_source)
      ,bounding_sphere_center: pickFirst(data.bounding_sphere_center, resultPayload.bounding_sphere_center, current.bounding_sphere_center)
      ,bounding_sphere_radius: pickFirst(data.bounding_sphere_radius, resultPayload.bounding_sphere_radius, current.bounding_sphere_radius)
      ,geometry_type: pickFirst(data.geometry_type, resultPayload.geometry_type, data.graphics_types && data.graphics_types[0], resultPayload.graphics_types && resultPayload.graphics_types[0], current.geometry_type)
    };
    next.spatial_query_available = isFinite(Number(next.longitude || next.center && next.center.longitude)) && isFinite(Number(next.latitude || next.center && next.center.latitude));
    if (!next.object_id && !next.name) return null;
    state.panelRuntime.currentObject = next;
    global.__EV_AGENT_CURRENT_OBJECT__ = next;
    renderContextBar();
    renderQuickActions();
    renderDiagnostics();
    return next;
  }

  function syncObjectFromUIEventResult(item) {
    if (!item) return;
    if (item.object_resolution && item.object_resolution.status === "resolved") {
      updateCurrentObject(item.object_resolution, item);
    }
    if (item.status !== "success") return;
    if (["locate_admin_region", "highlight_admin_boundary", "get_admin_region_properties"].indexOf(item.type) >= 0) {
      var adminPayload = item.result && (item.result.result || item.result) || {};
      var region = adminPayload.matched_region || adminPayload.region;
      if (region) {
        state.panelRuntime.currentAdminRegion = region;
        global.__EV_AGENT_LAST_ADMIN_REGION__ = region;
      }
    }
    if (["fly_to_object", "gis_highlight", "get_object_properties"].indexOf(item.type) >= 0) {
      var resultPayload = unwrapBridgeResult(item.result);
      var current = updateCurrentObject(merge(item.object_resolution || item.payload || {}, resultPayload || {}), item);
      if (current && item.type === "gis_highlight") current.highlighted = true;
    }
    if (item.type === "clear_highlight" && state.panelRuntime.currentObject) {
      state.panelRuntime.currentObject.highlighted = false;
      renderContextBar();
      renderQuickActions();
    }
  }

  function unwrapBridgeResult(value) {
    value = value || {};
    return value.result && typeof value.result === "object" ? value.result : value.payload && typeof value.payload === "object" ? value.payload : value;
  }

  function addObjectAction(container, label, type, objectId) {
    if (!objectId) return;
    var button = document.createElement("button");
    button.textContent = label;
    button.addEventListener("click", function () {
      global.WebGLAgentBridge.executeUIEvents([{ type: type, payload: { object_id: objectId } }]).then(function (result) {
        renderBridgeResultCard(label, type, result);
      }).catch(function (error) {
        appendErrorCard(error && error.message ? error.message : stringify(error));
      });
    });
    container.appendChild(button);
  }

  function enqueueUIEvent(data) {
    if (!state.uiEventQueue) return;
    var item = state.uiEventQueue.enqueue(data);
    updateUIEventLedger([item]);
    renderDiagnostics();
  }

  function updateUIEventLedger(items) {
    (items || []).forEach(function (item) {
      if (!item || !item.event_id) return;
      var existing = state.uiEventLedger.filter(function (entry) { return entry.event_id === item.event_id; })[0];
      var snapshot = {
        event_id: item.event_id,
        request_id: item.request_id || null,
        trace_id: item.trace_id || null,
        task_id: item.task_id || null,
        type: item.type || item.event_type,
        payload: sanitizeExportValue(item.payload || {}),
        sequence: Number(item.sequence || 0),
        queued_at: item.queued_at || null,
        started_at: item.started_at || null,
        completed_at: item.completed_at || null,
        status: item.status || "pending",
        result: sanitizeExportValue(item.result || null),
        error_code: item.error_code || null,
        error: item.error || null
      };
      if (existing) Object.keys(snapshot).forEach(function (key) { existing[key] = snapshot[key]; });
      else state.uiEventLedger.push(snapshot);
      if (["success", "failed", "canceled"].indexOf(snapshot.status) >= 0) {
        reduceDialogueStateFromUIEvent(snapshot);
        reconcileSemanticTrace(snapshot.request_id);
      }
    });
    if (state.uiEventLedger.length > 1000) state.uiEventLedger.splice(0, state.uiEventLedger.length - 1000);
  }

  function focusActions(kind, snapshot) {
    var matrix = {
      object: ["fly_to_object", "gis_highlight", "get_object_properties"],
      admin_region: ["locate_admin_region", "highlight_admin_boundary", "get_admin_region_properties", "create_buffer"],
      buffer: ["get_buffer_state", "get_buffer_result", "fly_to_buffer", "query_objects_in_buffer", "clear_buffer"],
      object_set: ["highlight_buffer_query_results", "clear_buffer_query_highlight", "select_by_rank"],
      buffer_query: ["highlight_buffer_query_results", "clear_buffer_query_highlight", "select_by_rank"]
    };
    var actions = (matrix[kind] || []).slice();
    if (kind === "object" && snapshot && (snapshot.geometry || snapshot.center || snapshot.position)) actions.push("create_buffer", "query_nearby_objects");
    return actions;
  }

  function upsertDialogueFocus(kind, referenceId, label, sourceTool, snapshot, active) {
    if (!referenceId) return;
    state.dialogueState = state.dialogueState || { conversation_id: sessionStorage.getItem("ev_agent_session_id") || "webgl-session", focus_stack: [] };
    var stack = state.dialogueState.focus_stack || (state.dialogueState.focus_stack = []);
    var current = stack.filter(function (focus) { return focus.kind === kind && focus.reference_id === referenceId; })[0];
    var next = {
      focus_id: current && current.focus_id || "focus_" + kind + "_" + referenceId,
      kind: kind, reference_id: referenceId, label: label || current && current.label || null,
      source_turn: state.dialogueState.turn_index || 0, source_tool: sourceTool,
      confidence: 1, plural: kind === "object_set" || kind === "buffer_query",
      available_actions: focusActions(kind, snapshot), active: active !== false,
      snapshot: sanitizeExportValue(snapshot || {})
    };
    if (current) Object.keys(next).forEach(function (key) { current[key] = next[key]; });
    else stack.push(next);
  }

  function reduceDialogueStateFromUIEvent(item) {
    if (!item || item.status !== "success") return;
    var result = unwrapBridgeResult(item.result || {});
    state.dialogueState = state.dialogueState || { focus_stack: [] };
    if (item.type === "create_buffer" && result.buffer_id) {
      state.dialogueState.last_buffer = sanitizeExportValue(result);
      upsertDialogueFocus("buffer", result.buffer_id, result.display_value || String(result.distance_m || "") + " m", item.type, result, true);
    } else if (item.type === "clear_buffer" && result.buffer_id) {
      var clearedAt = result.cleared_at || new Date().toISOString();
      var focus = (state.dialogueState.focus_stack || []).filter(function (entry) { return entry.kind === "buffer" && entry.reference_id === result.buffer_id; })[0];
      if (focus) { focus.active = false; focus.snapshot.status = "cleared"; focus.snapshot.rendered = false; focus.snapshot.cleared_at = clearedAt; }
      if (state.dialogueState.last_buffer && state.dialogueState.last_buffer.buffer_id === result.buffer_id) state.dialogueState.last_buffer = null;
    } else if (item.type === "clear_all_buffers") {
      (state.dialogueState.focus_stack || []).forEach(function (focus) { if (focus.kind === "buffer") { focus.active = false; if (focus.snapshot) { focus.snapshot.status = "cleared"; focus.snapshot.rendered = false; } } });
      state.dialogueState.last_buffer = null;
      state.dialogueState.last_buffer_query = null;
      state.dialogueState.last_object_set = null;
    } else if (item.type === "query_objects_in_buffer" && result.query_id) {
      state.dialogueState.last_buffer_query = sanitizeExportValue(result);
      var items = result.matches || result.items || result.objects || [];
      var set = { set_id: result.query_id, kind: "object_set", source_tool: item.type, items: items.map(function (entry, index) { return { reference_id: entry.reference_id || entry.object_id || entry.id, name: entry.name || entry.object_name, object_type: entry.object_type, rank: index + 1, active: true, data: entry }; }), count: items.length, active: true };
      state.dialogueState.last_object_set = set;
      upsertDialogueFocus("object_set", result.query_id, "查询结果（" + items.length + "项）", item.type, set, true);
      upsertDialogueFocus("buffer_query", result.query_id, "缓冲区查询", item.type, result, true);
    } else if (["locate_admin_region", "highlight_admin_boundary", "get_admin_region_properties"].indexOf(item.type) >= 0 && result) {
      var regionId = result.reference_id || result.region_id || result.boundary_object_id || result.object_id;
      if (regionId) {
        state.dialogueState.last_admin_region = sanitizeExportValue(result);
        upsertDialogueFocus("admin_region", regionId, result.name || result.object_name, item.type, result, true);
      }
    }
    state.dialogueState.updated_at = new Date().toISOString();
  }

  function reconcileSemanticTrace(requestId) {
    if (!requestId) return;
    var trace = state.semanticTraces.filter(function (entry) { return entry.request_id === requestId; })[0];
    if (!trace) return;
    var events = state.uiEventLedger.filter(function (entry) { return entry.request_id === requestId && ["show_message", "ask_clarification"].indexOf(entry.type) < 0; });
    trace.executed_ui_events = events.map(function (entry) { return { event_id: entry.event_id, type: entry.type, status: entry.status, payload: entry.payload, result: entry.result, error_code: entry.error_code }; });
    trace.executed_plan = { intent: events.length > 1 ? "multi_step" : events.length === 1 ? events[0].type : null, steps: events.map(function (entry, index) { return { sequence: index + 1, intent: entry.type, status: entry.status, parameters: entry.payload || {} }; }) };
    trace.final_status = events.some(function (entry) { return entry.status === "failed"; }) ? (events.some(function (entry) { return entry.status === "success"; }) ? "partial" : "failed") : (events.length ? "success" : trace.final_status || "awaiting_clarification");
  }

  function syncBufferLedger(item) {
    if (!item || item.status !== "success") return;
    var result = unwrapBridgeResult(item.result || {});
    if (item.type === "create_buffer" && result && result.buffer_id) {
      var existing = state.buffers.filter(function (entry) { return entry.buffer_id === result.buffer_id; })[0];
      var snapshot = sanitizeExportValue(result);
      if (existing) Object.keys(snapshot).forEach(function (key) { existing[key] = snapshot[key]; });
      else state.buffers.push(snapshot);
    } else if (item.type === "clear_buffer" && result && result.buffer_id) {
      var record = state.buffers.filter(function (entry) { return entry.buffer_id === result.buffer_id; })[0];
      if (record) { record.status = "cleared"; record.rendered = false; record.cleared_at = result.cleared_at || new Date().toISOString(); }
    } else if (item.type === "clear_all_buffers") {
      state.buffers.forEach(function (entry) { if (entry.status !== "cleared") { entry.status = "cleared"; entry.rendered = false; entry.cleared_at = new Date().toISOString(); } });
    }
  }

  function appendWarning(message) {
    appendTimelineMessage("system_status_message", message || "请检查当前状态。");
  }

  function syncBufferQueryLedger(item) {
    if (!item || item.type !== "query_objects_in_buffer" || ["success", "failed"].indexOf(item.status) < 0) return;
    var result = unwrapBridgeResult(item.result || {});
    var queryId = result.query_id || item.event_id;
    var snapshot = sanitizeExportValue(result);
    snapshot.query_id = queryId;
    snapshot.status = result.status || (item.status === "success" ? "completed" : "failed");
    var existing = state.bufferQueries.filter(function (entry) { return entry.query_id === queryId; })[0];
    if (existing) Object.keys(snapshot).forEach(function (key) { existing[key] = snapshot[key]; });
    else state.bufferQueries.push(snapshot);
  }

  function appendErrorCard(message) {
    state.errors.push({ message: message || "Agent 服务连接失败。", created_at: new Date().toISOString() });
    if (state.errors.length > 200) state.errors.shift();
    var node = appendTimelineMessage("error_card", message || "Agent 服务连接失败。");
    if (node) node.className += " is-error";
  }

  function recordRawEvent(event) {
    state.rawEvents.push(event);
    if (state.rawEvents.length > 300) state.rawEvents.shift();
    renderDiagnostics();
  }

  function rememberSemanticTrace(data) {
    if (!data) return;
    var trace = sanitizeExportValue(data.semantic_orchestration || data);
    trace.request_id = data.request_id || trace.request_id || null;
    trace.created_at = new Date().toISOString();
    state.semanticTraces.push(trace);
    if (state.semanticTraces.length > 200) state.semanticTraces.shift();
    if (data.dialogue_state) state.dialogueState = sanitizeExportValue(data.dialogue_state);
    if (data.model_participation) state.modelParticipation.push(sanitizeExportValue(data.model_participation));
    if (Array.isArray(data.qwen_invocations)) data.qwenInvocations = sanitizeExportValue(data.qwen_invocations);
    if (data.rag) state.ragSummary = sanitizeRagSummary(data.rag);
    if (data.rag_index_status) state.ragIndexStatus = sanitizeRagIndexStatus(data.rag_index_status);
    if (Array.isArray(data.rag_retrievals)) data.rag_retrievals.forEach(function (item) {
      state.ragRetrievals.push(sanitizeRagRetrieval(item));
    });
    if (Array.isArray(data.rag_evidences)) data.rag_evidences.forEach(function (item) {
      state.ragEvidences.push(sanitizeRagEvidence(item));
    });
    if (Array.isArray(data.rag_citations)) data.rag_citations.forEach(function (item) {
      state.ragCitations.push(sanitizeRagCitation(item));
    });
    if (Array.isArray(data.rag_fallbacks)) data.rag_fallbacks.forEach(function (item) {
      state.ragFallbacks.push(sanitizeExportValue(item));
    });
    if (Array.isArray(data.rag_source_operations)) data.rag_source_operations.forEach(function (item) {
      state.ragSourceOperations.push(sanitizeRagSourceOperation(item));
    });
    if (Array.isArray(data.rag_index_jobs)) data.rag_index_jobs.forEach(function (item) {
      state.ragIndexJobs.push(sanitizeRagIndexJob(item));
    });
    if (Array.isArray(data.rag_backup_operations)) data.rag_backup_operations.forEach(function (item) {
      state.ragBackupOperations.push(sanitizeRagBackupOperation(item));
    });
    if (state.ragRetrievals.length > 100) state.ragRetrievals = state.ragRetrievals.slice(-100);
    if (state.ragEvidences.length > 300) state.ragEvidences = state.ragEvidences.slice(-300);
    if (state.ragCitations.length > 300) state.ragCitations = state.ragCitations.slice(-300);
    if (state.ragFallbacks.length > 100) state.ragFallbacks = state.ragFallbacks.slice(-100);
    if (state.ragSourceOperations.length > 100) state.ragSourceOperations = state.ragSourceOperations.slice(-100);
    if (state.ragIndexJobs.length > 100) state.ragIndexJobs = state.ragIndexJobs.slice(-100);
    if (state.ragBackupOperations.length > 100) state.ragBackupOperations = state.ragBackupOperations.slice(-100);
    if (Array.isArray(data.clarifications)) sanitizeExportValue(data.clarifications).forEach(function (entry) {
      var key = entry.clarification_id || entry.plan_id;
      var existing = state.clarifications.filter(function (item) { return (item.clarification_id || item.plan_id) === key; })[0];
      if (existing) Object.keys(entry).forEach(function (name) { existing[name] = entry[name]; });
      else state.clarifications.push(entry);
    });
    renderDiagnostics();
  }

  function renderDiagnostics() {
    if (!state.rawEventsNode) return;
    var tab = state.activeDiagnosticTab || "tasks";
    var content = {
      tasks: state.taskHistory.length ? state.taskHistory : { empty: true, message: "暂无任务记录" },
      events: state.uiEventLedger.length ? state.uiEventLedger : { empty: true, message: "暂无 UI Event" },
      messages: state.rawEvents.length ? state.rawEvents : { empty: true, message: "暂无原始消息" },
      semantic: state.semanticTraces.length ? {
        semantic_traces: state.semanticTraces,
        model_participation: state.modelParticipation,
        qwen_invocations: state.qwenInvocations,
        qwen_health: state.qwenHealth,
        qwen_fallbacks: state.qwenInvocations.filter(function (item) {
          return item && (item.fallback_used || item.status === "fallback" || item.status === "timeout" || item.status === "circuit_open");
        }),
        rag: state.ragSummary,
        rag_index_status: state.ragIndexStatus,
        dialogue_state: state.dialogueState,
        clarifications: state.clarifications
      } : { empty: true, message: "暂无语义解析记录" },
      errors: state.errors.length ? state.errors : { empty: true, message: "暂无错误" },
      state: {
        handlers: global.WebGLAgentBridge && global.WebGLAgentBridge.listHandlers ? global.WebGLAgentBridge.listHandlers() : [],
        agent_ready: !!state.adapter,
        transport_mode: state.transportMode,
        request_active: isTaskRunning(),
        panel_runtime: state.panelRuntime,
        current_object: state.panelRuntime.currentObject,
        test_object: state.testObject
        ,connection_status: state.connectionStatus
        ,active_request_status: state.activeRequestStatus
        ,last_request_status: state.lastRequestStatus
        ,session_health: state.sessionHealth
        ,qwen_health: state.qwenHealth
        ,release: state.health && state.health.raw || null
        ,release_warning: state.releaseWarning
      }
    };
    state.rawEventsNode.textContent = stringify(content[tab]);
    if (state.panel) {
      var diagnosticTabs = typeof state.panel.querySelectorAll === "function" ? state.panel.querySelectorAll("[data-tab]") : [];
      Array.prototype.forEach.call(diagnosticTabs, function (button) {
        button.classList.toggle("is-active", button.dataset.tab === tab);
      });
      var count = state.panel.querySelector("[data-error-count]");
      if (count) count.textContent = state.errors.length ? "(" + state.errors.length + ")" : "";
    }
    scheduleAreaScroll(tab, false);
  }

  function setDiagnosticTab(tab) {
    if (["tasks", "events", "semantic", "messages", "errors", "state"].indexOf(tab) < 0) return;
    state.activeDiagnosticTab = tab;
    state.scrollFollow[tab] = true;
    renderDiagnostics();
    scheduleAreaScroll(tab, true);
  }

  function renderContextBar(objectData) {
    if (!state.contextNode) return;
    var selected = objectData || state.panelRuntime.currentObject || getSelectedObject();
    if (selected && !state.panelRuntime.currentObject) updateCurrentObject(selected);
    if (!selected && !state.testObject) {
      state.contextNode.textContent = "当前对象：未选择\n测试对象：未设置\n模式：低风险临时定位/高亮";
      return;
    }
    if (!selected && state.testObject) {
      state.contextNode.textContent = "当前对象：未选择\n测试对象：" + (state.testObject.name || state.testObject.object_name || state.testObject.admin_name || state.testObject.adcode || state.testObject.object_id || "已设置") + "\n模式：低风险临时定位/高亮";
      return;
    }
    var name = selected.name || selected.object_name || selected.business_id || "-";
    var layer = selected.layer_name || selected.data_source_name || selected.layer_id || "-";
    var highlight = selected.highlighted ? "已高亮" : "未高亮";
    state.contextNode.textContent = "当前对象：" + name + "\n图层：" + layer + " | 状态：" + highlight + "\n测试对象：" + (state.testObject ? state.testObject.name || state.testObject.object_name || state.testObject.admin_name || state.testObject.adcode || state.testObject.object_id : "未设置") + " | 模式：低风险临时定位/高亮";
  }

  function renderQuickActions() {
    if (!state.quickActionsNode) return;
    replaceChildren(state.quickActionsNode);
    var target = resolveQuickActionTarget();
    state.quickActionsNode.hidden = !target;
    if (!target) return;
    [
      { label: "定位", title: "定位目标对象", action: "locate", needsObject: true },
      { label: "高亮", title: "高亮目标对象", action: "highlight", needsObject: true },
      { label: "属性", title: "查看目标属性", action: "properties", needsObject: true },
      { label: "清除", title: "清除普通对象和行政区高亮", action: "clear", needsObject: false },
      { label: "视角", title: "读取当前相机视角", action: "camera", needsObject: false }
    ].forEach(function (item) {
      var button = document.createElement("button");
      button.type = "button";
      button.textContent = item.label;
      button.title = item.title;
      button.dataset.quickAction = item.action;
      button.dataset.state = "idle";
      button.addEventListener("click", function () {
        runQuickAction(button, item);
      });
      state.quickActionsNode.appendChild(button);
    });
  }

  function resolveQuickActionTarget() {
    if (state.testObject) {
      if (state.testObject.is_admin_region || state.testObject.adcode) return { kind: "admin", value: state.testObject, source: "test_object" };
      return { kind: "object", value: state.testObject, source: "test_object" };
    }
    var selected = getSelectedObject();
    if (selected && selected.object_id) return { kind: "object", value: selected, source: "selected_object" };
    var current = state.panelRuntime.currentObject;
    if (current && current.object_id) return { kind: "object", value: current, source: "last_context_object" };
    var admin = state.panelRuntime.currentAdminRegion;
    if (admin && (admin.name || admin.adcode || admin.region_id)) return { kind: "admin", value: admin, source: "last_admin_region" };
    return null;
  }

  function quickActionEvent(item, target) {
    if (item.action === "clear") return { type: "clear_highlight", payload: {} };
    if (item.action === "camera") return { type: "get_camera_state", payload: {} };
    if (!target) return null;
    var value = target.value || {};
    if (target.kind === "admin") {
      var adminPayload = {
        name: value.name || value.region_name || value.admin_name || value.object_name || null,
        adcode: value.adcode || null,
        level: value.level || null,
        parent: value.parent_name || value.parent || null
      };
      return {
        type: item.action === "locate" ? "locate_admin_region" : item.action === "highlight" ? "highlight_admin_boundary" : "get_admin_region_properties",
        payload: adminPayload
      };
    }
    return {
      type: item.action === "locate" ? "fly_to_object" : item.action === "highlight" ? "gis_highlight" : "get_object_properties",
      payload: { object_id: value.object_id || value.id, duration: item.action === "locate" ? 1.5 : undefined }
    };
  }

  function runQuickAction(button, item) {
    if (!global.WebGLAgentBridge || typeof global.WebGLAgentBridge.executeUIEvents !== "function") {
      appendErrorCard("WebGLAgentBridge.executeUIEvents 不可用。");
      return;
    }
    var target = resolveQuickActionTarget();
    if (item.needsObject && !target) {
      appendWarning("请先选择、定位或设置测试对象。");
      setQuickButtonState(button, "failed", item.label);
      return;
    }
    var event = quickActionEvent(item, target);
    setQuickButtonState(button, "loading", item.label);
    appendTimelineMessage("system_status_message", "正在执行：" + item.title);
    global.WebGLAgentBridge.executeUIEvents([event]).then(function (result) {
      var normalized = Array.isArray(result) ? result[0] : result || {};
      if (normalized.success === false) {
        renderBridgeResultCard(item.title, event.type, result);
        appendErrorCard(normalized.error || normalized.message || item.title + "失败");
        setQuickButtonState(button, "failed", item.label);
        return;
      }
      if (event.type === "gis_highlight" && state.panelRuntime.currentObject) state.panelRuntime.currentObject.highlighted = true;
      if (event.type === "clear_highlight" && state.panelRuntime.currentObject) state.panelRuntime.currentObject.highlighted = false;
      if (target && target.kind === "object" && ["fly_to_object", "gis_highlight", "get_object_properties"].indexOf(event.type) >= 0) {
        updateCurrentObject(target.value);
      }
      renderBridgeResultCard(item.title, event.type, result);
      appendTimelineMessage("system_status_message", item.title + "已完成。");
      renderContextBar();
      setQuickButtonState(button, "success", item.label);
    }).catch(function (error) {
      appendErrorCard(error && error.message ? error.message : stringify(error));
      setQuickButtonState(button, "failed", item.label);
    });
  }

  function setQuickButtonState(button, stateName, label) {
    if (!button) return;
    button.dataset.state = stateName;
    button.disabled = stateName === "loading";
    button.textContent = stateName === "loading" ? "..." : stateName === "success" ? "完成" : stateName === "failed" ? "失败" : label;
    if (stateName === "success" || stateName === "failed") {
      setTimeout(function () {
        if (!button.isConnected) return;
        button.dataset.state = "idle";
        button.disabled = false;
        button.textContent = label;
      }, 1200);
    }
  }

  function renderBridgeResultCard(label, type, result) {
    var normalized = Array.isArray(result) ? result[0] : result;
    normalized = normalized || {};
    var payload = normalized.result || normalized.payload || normalized.data || normalized;
    if (type === "get_camera_state") {
      addInfoCard("当前视角", payload);
      return;
    }
    if (type === "get_object_properties") {
      addObjectPropertiesCard(payload);
      return;
    }
    if (type === "search_business_objects") {
      addObjectSearchCard(payload);
      return;
    }
    if (type === "query_nearby_objects") {
      addNearbyObjectsCard(payload);
      return;
    }
    if (["create_buffer", "get_buffer_state", "get_buffer_result", "fly_to_buffer", "clear_buffer", "clear_all_buffers", "query_objects_in_buffer"].indexOf(type) >= 0) {
      if (payload.success === false) addReadableErrorCard(payload.error_code || payload.code, payload.error || payload.message);
      else if (type === "query_objects_in_buffer") addBufferQueryResultCard(payload);
      else addBufferResultCard(payload);
      return;
    }
    if (["highlight_buffer_query_results", "clear_buffer_query_highlight"].indexOf(type) >= 0) {
      if (payload.success === false) addReadableErrorCard(payload.error_code || payload.code, payload.error || payload.message);
      else addInfoCard(type === "highlight_buffer_query_results" ? "缓冲区查询高亮" : "缓冲区查询高亮已清除", payload);
      return;
    }
    if (type === "get_layer_tree") {
      addLayerTreeCard(payload);
      return;
    }
    if (type === "get_selection_state" || type === "get_selected_object") {
      addSelectionCard(payload);
      return;
    }
    if (["locate_admin_region", "highlight_admin_boundary", "get_admin_region_properties"].indexOf(type) >= 0) {
      addAdminRegionCard(payload);
      return;
    }
    addInfoCard(label || "操作结果", { type: type, success: normalized.success !== false, result: payload });
  }

  function renderCompletedToolResult(item) {
    if (!item || item.__toolResultShown || ["success", "failed"].indexOf(item.status) < 0) return;
    item.__toolResultShown = true;
    renderBridgeResultCard(toolLabel(item.type), item.type, item.result || item);
  }

  function toolLabel(type) {
    return ({
      get_layer_tree: "图层树查询",
      get_object_properties: "对象属性查询",
      get_selection_state: "当前选择查询",
      get_selected_object: "当前对象查询"
      ,search_business_objects: "对象名称查询"
      ,query_nearby_objects: "邻近对象查询"
      ,locate_admin_region: "行政区定位"
      ,highlight_admin_boundary: "行政区边界高亮"
      ,get_admin_region_properties: "行政区属性查询"
      ,create_buffer: "创建缓冲区"
      ,get_buffer_state: "缓冲区状态"
      ,get_buffer_result: "缓冲区结果"
      ,fly_to_buffer: "定位缓冲区"
      ,clear_buffer: "清除缓冲区"
      ,clear_all_buffers: "清除全部缓冲区"
      ,query_objects_in_buffer: "缓冲区内对象查询"
      ,highlight_buffer_query_results: "高亮缓冲区查询结果"
      ,clear_buffer_query_highlight: "清除缓冲区查询高亮"
    })[type] || "Tool 执行结果";
  }

  function addBufferResultCard(data) {
    data = data || {};
    var target = data.target || {};
    var lines = [
      "状态：" + (data.code || data.state || data.status || "SUCCESS"),
      "缓冲区 ID：" + (data.buffer_id || data.active_buffer_id || "-"),
      "目标：" + (target.name || target.id || "-") + (target.geometry_type ? "（" + target.geometry_type + "）" : ""),
      "距离：" + (data.distance_m !== undefined ? data.distance_m + " m" : "-"),
      "计算方法：" + (data.calculation_method || "-"),
      "已渲染：" + (data.rendered === true ? "是" : data.rendered === false ? "否" : "-"),
      "仅中心点：" + (data.center_only === true ? "是" : "否"),
      "几何可信度：" + (target.geometry_confidence || "-"),
      "数据来源：" + (target.source || data.source || "-")
    ];
    if (data.center_only === true) lines.push("提示：当前对象未公开完整几何，本次缓冲区围绕对象中心点生成。");
    if (data.visibility_hint) lines.push("提示：" + data.visibility_hint);
    if (data.visibility) lines.push("可见性校验：" + (data.visibility.visible ? "通过" : "未通过"));
    if (data.buffer_count !== undefined) lines.push("当前缓冲区数量：" + data.buffer_count);
    addTextCard("缓冲区分析", lines.join("\n"));
  }

  function addBufferQueryResultCard(data) {
    var lines = [
      "缓冲区 ID：" + (data.buffer_id || "-"),
      "缓冲目标/距离：" + ((data.buffer_target && (data.buffer_target.name || data.buffer_target.id)) || "-") + " / " + (data.buffer_distance_m !== undefined ? data.buffer_distance_m + " m" : "-"),
      "候选：" + Number(data.candidate_count || 0) + "，可查询：" + Number(data.queryable_count || 0) + "，匹配：" + Number(data.matched_count || 0) + "，跳过：" + Number(data.skipped_count || 0)
    ];
    (data.matches || []).slice(0, 30).forEach(function (item) { lines.push("• " + (item.name || item.object_id || "未命名") + " | " + (item.object_type || "-") + " | " + (item.layer_name || "-") + " | " + (item.match_relation || "-") + " | " + (item.geometry_confidence || "-") + (item.match_basis === "bounding_center" ? "（仅包围球中心代表）" : "")); });
    (data.limitations || []).forEach(function (item) { lines.push("限制：" + item); });
    if (!data.matched_count) lines.push("缓冲区查询已完成，当前可查询对象中没有匹配结果。");
    addTextCard("缓冲区内对象查询", lines.join("\n"));
  }

  function addLayerTreeCard(data) {
    data = data || {};
    if (data.success === false) {
      addReadableErrorCard(data.error_code, data.error);
      return;
    }
    var lines = ["图层总数：" + Number(data.total_count || 0) + "，可见：" + Number(data.visible_count || 0) + "，隐藏：" + Number(data.hidden_count || 0)];
    (data.layers || []).slice(0, 100).forEach(function (layer) {
      var prefix = layer.parent ? "  └─ " : "• ";
      lines.push(prefix + (layer.name || layer.layer_id || "未命名图层") + " [" + (layer.visible === false ? "隐藏" : "可见") + "]" + (layer.type ? " · " + layer.type : ""));
    });
    addTextCard("当前图层树", lines.join("\n"));
  }

  function addObjectPropertiesCard(data) {
    data = data || {};
    if (data.success === false || data.found === false) {
      if (data.error_code === "OBJECT_AMBIGUOUS" && data.candidates) {
        addTextCard("找到多个对象", data.candidates.map(function (item) { return "• " + (item.name || "未命名") + " | " + (item.object_id || "-") + " | " + (item.business_type || item.object_type || "-") + " | " + (item.source || item.layer_name || "-"); }).join("\n"));
      } else addReadableErrorCard(data.error_code, data.error || data.message);
      return;
    }
    var lines = [
      "名称：" + (data.name || "-"),
      "ID：" + (data.object_id || "-"),
      "类型：" + (data.business_type || data.object_type || "-"),
      "来源：" + (data.source || "-"),
      "匹配方式：" + (data.matched_by || "-")
    ];
    var position = data.position || {};
    if (position.position_valid || position.longitude !== null && position.longitude !== undefined) lines.push("位置：" + [position.longitude, position.latitude, position.height].filter(function (value) { return value !== null && value !== undefined; }).join(", "));
    if (data.bounding_sphere_valid) lines.push("包围球半径：" + (data.bounding_sphere_radius || "可用"));
    var properties = data.business_properties || data.standard_properties || data.properties || {};
    Object.keys(properties).filter(function (key) { return key.charAt(0) !== "_" && typeof properties[key] !== "function"; }).slice(0, 20).forEach(function (key) {
      var value = properties[key];
      if (value && typeof value === "object") value = "[结构化数据]";
      lines.push(key + "：" + String(value).slice(0, 240));
    });
    addTextCard("对象属性", lines.join("\n"));
  }

  function addObjectSearchCard(data) {
    data = data || {};
    if (data.success === false) {
      if (data.error_code === "OBJECT_AMBIGUOUS" && data.candidates) {
        addTextCard("找到多个同名对象", data.candidates.map(function (item) { return "• " + (item.name || "-") + " | " + (item.object_id || "-") + " | " + (item.object_type || "-") + " | " + (item.source || "-"); }).join("\n"));
      } else addReadableErrorCard(data.error_code || data.code, data.error);
      return;
    }
    var item = data.object || data.candidates && data.candidates[0] || {};
    addTextCard("对象查询结果", ["名称：" + (item.name || "-"), "ID：" + (item.object_id || "-"), "类型：" + (item.object_type || "-"), "图层：" + (item.layer_name || "-"), "来源：" + (item.source || "-"), "匹配方式：" + (item.matched_by || "-")].join("\n"));
  }

  function addNearbyObjectsCard(data) {
    data = data || {};
    if (data.success === false) {
      addReadableErrorCard(data.error_code || data.code, data.error);
      return;
    }
    var center = data.center || {};
    var lines = [
      "查询中心：" + (center.anchor_name || [center.longitude, center.latitude].join(", ")),
      "中心来源：" + (data.center_source || center.source || "-"),
      "半径：" + Number(data.radius_m || 0) + " 米",
      "结果数量：" + Number(data.total_count || 0)
    ];
    if (data.large_area_notice) lines.push("说明：" + data.large_area_notice);
    (data.objects || []).slice(0, 30).forEach(function (item) { lines.push("• " + (item.object_name || item.name || item.object_id || "未命名对象")); });
    addTextCard("邻近对象查询", lines.join("\n"));
  }

  function addSelectionCard(data) {
    data = data || {};
    if (data.success === false || data.found === false) {
      addReadableErrorCard(data.error_code, data.error);
      return;
    }
    var selected = data.selected_object || data;
    addTextCard("当前选中对象", ["名称：" + (selected.object_name || selected.name || "-"), "ID：" + (selected.object_id || "-"), "类型：" + (selected.object_type || "-"), "选择来源：" + (data.selection_source || data.source || "-")].join("\n"));
  }

  function addAdminRegionCard(data) {
    data = data || {};
    if (data.success === false) {
      if (data.error_code === "ADMIN_REGION_AMBIGUOUS" && data.candidates) {
        addTextCard("找到多个同名行政区", data.candidates.map(function (item) { return "• " + (item.name || "-") + " | " + (item.adcode || "-") + " | " + (item.level || "-") + " | " + (item.parent || "-"); }).join("\n"));
      } else addReadableErrorCard(data.error_code || data.code, data.error);
      return;
    }
    var region = data.matched_region || data.region || {};
    addTextCard("行政区结果", [
      "正式名称：" + (region.name || "-"),
      "行政级别：" + (region.level || "-"),
      "上级行政区：" + (region.parent_name || "-"),
      "adcode：" + (region.adcode || "-"),
      "数据来源：" + (region.source || "-"),
      "中心点：" + (data.center_available !== false && region.center ? "可用（" + (region.center_source || "真实数据") + "）" : "不可用"),
      "边界：" + (data.boundary_available !== false && region.boundary_available ? "可用" : "不可用"),
      "定位完成：" + (data.fly_completed === true ? "是" : "否"),
      "高亮完成：" + (data.highlight_completed === true ? "是" : "否")
    ].join("\n"));
  }

  function addReadableErrorCard(code, message) {
    var descriptions = {
      LAYER_TREE_EMPTY: "WebGL 已初始化，但当前没有加载任何图层。",
      VIEWER_NOT_READY: "WebGL Viewer 尚未初始化，请等待地图加载完成。",
      ADMIN_REGION_NOT_FOUND: "当前行政区数据中未找到该地区。",
      ADMIN_REGION_AMBIGUOUS: "找到多个同名行政区，请指定上级省市。",
      ADMIN_BOUNDARY_UNAVAILABLE: "可以定位，但当前数据源没有可高亮的行政区边界。",
      ADMIN_REGION_SOURCE_UNAVAILABLE: "当前行政区数据源不可用。",
      LAYER_SOURCE_UNAVAILABLE: "当前无法读取图层管理器或 Viewer 图层集合。",
      OBJECT_NOT_FOUND: "当前场景中没有找到该对象。",
      NO_OBJECT_SELECTED: "当前没有选中对象，请先在地图上选择一个对象。",
      OBJECT_AMBIGUOUS: "找到多个同名对象，请根据 ID、类型或来源进一步指定。",
      OBJECT_PROPERTIES_UNAVAILABLE: "对象存在，但属性当前不可读取。"
      ,SPATIAL_QUERY_CENTER_REQUIRED: "无法确定范围查询的中心，请先选择对象、定位对象，或明确指定中心位置。"
      ,AGENT_PLAN_SCHEMA_INVALID: "本次请求的执行计划生成异常，系统未执行地图操作。请重试或使用更明确的对象名称。"
      ,AGENT_PLAN_REPAIR_FAILED: "本次请求的执行计划修复失败，系统未执行地图操作。请使用更明确的对象名称。"
    };
    addTextCard("查询失败 · " + (code || "UNKNOWN_ERROR"), descriptions[code] || message || "未知错误");
  }

  function addTextCard(title, text) {
    if (!state.timelineNode) return;
    var card = document.createElement("div");
    card.className = "ev-agent-card info_card";
    var strong = document.createElement("strong");
    strong.textContent = title;
    card.appendChild(strong);
    var pre = document.createElement("pre");
    pre.textContent = text || "";
    card.appendChild(pre);
    state.timelineNode.appendChild(card);
    autoScrollTimeline();
  }

  function addInfoCard(title, data) {
    if (!state.timelineNode) return;
    var card = document.createElement("div");
    card.className = "ev-agent-card info_card";
    var strong = document.createElement("strong");
    strong.textContent = title;
    card.appendChild(strong);
    var pre = document.createElement("pre");
    pre.textContent = stringify(data || {});
    card.appendChild(pre);
    state.timelineNode.appendChild(card);
    autoScrollTimeline();
  }

  function setRequestActive(active, label) {
    var button = state.panel && state.panel.querySelector("[data-send]");
    updateSendButton(active);
    setStatus(label || (active ? "正在生成" : "空闲"));
    if (!active) state.currentController = null;
  }

  function stopActiveRequest() {
    cancelCurrentAgentTask();
  }

  function isTaskRunning() {
    return ["submitting", "connecting", "streaming", "resolving_object", "executing"].indexOf(state.panelRuntime.state) >= 0;
  }

  function setAgentPanelState(nextState, label) {
    state.panelRuntime.state = nextState || state.panelRuntime.state || "idle";
    state.activeRequestStatus = isTaskRunning() ? state.panelRuntime.state : state.activeRequestStatus;
    var statusRecord = { state: state.panelRuntime.state, label: label || statusLabel(state.panelRuntime.state), created_at: new Date().toISOString() };
    var last = state.statusEvents[state.statusEvents.length - 1];
    if (!last || last.state !== statusRecord.state || last.label !== statusRecord.label) state.statusEvents.push(statusRecord);
    if (state.statusEvents.length > 300) state.statusEvents.shift();
    setStatus(label || statusLabel(state.panelRuntime.state));
    updateSendButton(isTaskRunning());
    renderDiagnostics();
  }

  function updateSendButton(running) {
    var button = state.panel && state.panel.querySelector("[data-send]");
    if (!button) return;
    var label = button.querySelector(".send-label");
    var stop = button.querySelector(".stop-indicator");
    button.dataset.state = running ? "running" : "idle";
    button.title = running ? "中断当前任务" : "发送";
    button.setAttribute("aria-label", running ? "中断当前 Agent 请求" : "发送 Agent 请求");
    if (label) label.hidden = !!running;
    if (stop) stop.hidden = !running;
  }

  function cancelCurrentAgentTask(options) {
    options = options || {};
    if (!isTaskRunning() && !state.currentController && !state.uiEventQueue) return;
    if (state.currentController) {
      try {
        state.currentController.abort();
      } catch (error) {}
      state.currentController = null;
    }
    cancelActiveWebGLFlight();
    if (state.uiEventQueue) {
      var hadExecuting = (state.uiEventQueue.items || []).some(function (item) { return item.status === "executing"; });
      state.uiEventQueue.cancel();
      if (hadExecuting) appendWarning("当前地图操作已开始，后续步骤已取消。");
    }
    if (state.panelRuntime.streamNode) finishStreamingNode(state.panelRuntime.streamNode, "canceled");
    if (!options.fromAbortCallback) appendTimelineMessage("system_status_message", "任务已中断");
    updateTaskStage("canceled", "任务已中断");
    finalizePanelTask("canceled", "已中断");
  }

  function failActiveTask(label) {
    if (state.uiEventQueue) {
      state.uiEventQueue.cancel();
      updateUIEventLedger(state.uiEventQueue.items || []);
    }
    updateTaskStage("failed", label || "执行失败");
    finalizePanelTask("failed", label || "执行失败");
  }

  function cancelActiveWebGLFlight() {
    try {
      if (state.adapter && typeof state.adapter.cancelCurrentFlight === "function") {
        state.adapter.cancelCurrentFlight();
        return;
      }
      if (global.viewer && global.viewer.camera && typeof global.viewer.camera.cancelFlight === "function") {
        global.viewer.camera.cancelFlight();
      }
    } catch (error) {
      appendWarning("已请求中断，但当前镜头飞行可能不支持安全取消。");
    }
  }

  function maybeFinishActiveTask() {
    if (!state.activeTask || !state.panelRuntime.responseFinished) return;
    if (!isTaskFullySettled(state.activeTask)) {
      setAgentPanelState("executing", "正在执行地图操作");
      return;
    }
    var requestItems = state.uiEventLedger.filter(function (item) { return item.request_id === state.activeTask.request_id; });
    var canceledMessage = requestItems.some(function (item) { return item.type === "show_message" && item.payload && item.payload.task_status === "canceled"; });
    if (canceledMessage) {
      updateTaskStage("canceled", "已取消");
      finalizePanelTask("canceled", "已取消");
      return;
    }
    var clarificationOnly = requestItems.length && requestItems.every(function (item) { return ["ask_clarification", "show_message"].indexOf(item.type) >= 0; });
    if (clarificationOnly) {
      updateTaskStage("awaiting_clarification", "等待补充信息");
      finalizePanelTask("awaiting_clarification", "等待补充信息");
      return;
    }
    appendExecutionSummary(state.activeTask);
    updateTaskStage("completed", "已完成");
    finalizePanelTask("completed", "已完成");
  }

  function appendExecutionSummary(task) {
    if (!task || task.finalSummaryGenerated) return;
    var items = state.uiEventLedger.filter(function (item) { return item.request_id === task.request_id; });
    if (!items.length) return;
    var successTypes = items.filter(function (item) { return item.status === "success"; }).map(function (item) { return item.type; });
    var failedItems = items.filter(function (item) { return item.status === "failed"; });
    var labels = {
      fly_to_object: "定位", gis_highlight: "高亮", get_object_properties: "对象属性读取",
      search_business_objects: "对象查询", query_nearby_objects: "邻近对象查询",
      locate_admin_region: "行政区定位", highlight_admin_boundary: "行政区边界高亮",
      get_admin_region_properties: "行政区属性读取"
      ,create_buffer: "缓冲区生成"
      ,get_buffer_state: "缓冲区状态读取"
      ,get_buffer_result: "缓冲区结果读取"
      ,fly_to_buffer: "缓冲区定位"
      ,clear_buffer: "缓冲区清除"
      ,clear_all_buffers: "全部缓冲区清除"
      ,query_objects_in_buffer: "缓冲区内对象查询"
      ,highlight_buffer_query_results: "缓冲区查询结果高亮"
      ,clear_buffer_query_highlight: "缓冲区查询高亮清除"
    };
    var parts = successTypes.map(function (type) { return labels[type] || type; });
    var text;
    if (!failedItems.length) text = "执行完成：已成功完成" + parts.join("、") + "。";
    else if (parts.length) text = "部分完成：" + parts.join("、") + "成功；" + failedItems.map(function (item) { return (labels[item.type] || item.type) + "失败：" + userReadableError(item.error_code, item.error); }).join("；") + "。";
    else text = "执行失败：" + failedItems.map(function (item) { return (labels[item.type] || item.type) + "：" + userReadableError(item.error_code, item.error); }).join("；") + "。";
    appendTimelineMessage("assistant_message", text.replace(/。。$/, "。"), { request_id: task.request_id, kind: "execution_summary" });
    task.finalSummaryGenerated = true;
  }

  function isTaskFullySettled(task) {
    var items = state.uiEventQueue && state.uiEventQueue.items || [];
    var pending = items.filter(function (item) {
      return item.status === "queued" || item.status === "pending";
    }).length;
    var running = items.filter(function (item) {
      return item.status === "executing";
    }).length;
    task.responseFinished = state.panelRuntime.responseFinished === true;
    task.pendingUIEvents = pending;
    task.runningUIEvents = running;
    return task.responseFinished === true && pending === 0 && running === 0;
  }

  function hasPendingUIEvents() {
    var items = state.uiEventQueue && state.uiEventQueue.items || [];
    return items.some(function (item) {
      return item.status === "queued" || item.status === "pending" || item.status === "executing";
    });
  }

  function finalizePanelTask(finalState, label) {
    if (finalState === "completed") {
      finalState = computeFinalTaskState();
      if (finalState === "partial_success") label = "部分完成";
      if (finalState === "failed") label = "执行失败";
    }
    if (state.activeTask) {
      state.activeTask.status = finalState;
      state.activeTask.completedAt = Date.now();
      state.activeTask.responseFinished = true;
      state.activeTask.pendingUIEvents = 0;
      state.activeTask.runningUIEvents = 0;
      state.activeTask.collapsed = finalState === "completed";
      if (finalState === "completed") {
        state.activeTask.steps.forEach(function (step) {
          if (step.status !== "failed" && step.status !== "canceled") step.status = "success";
        });
      } else if (finalState === "failed" || finalState === "partial_success") {
        var executionStep = state.activeTask.steps[state.activeTask.steps.length - 1];
        if (executionStep) executionStep.status = "failed";
      }
      renderTask();
    }
    state.panelRuntime.state = finalState;
    state.panelRuntime.completedAt = Date.now();
    state.panelRuntime.activeRequestId = null;
    state.panelRuntime.activeTaskId = null;
    state.panelRuntime.abortController = null;
    state.panelRuntime.eventQueue = state.uiEventQueue;
    state.panelRuntime.responseFinished = true;
    state.activeRequestStatus = "idle";
    state.lastRequestStatus = finalState === "partial_success" ? "partial" : finalState === "canceled" ? "cancelled" : finalState;
    state.sessionHealth = state.runtimeState.backend_online === false ? "degraded" : "healthy";
    state.currentController = null;
    updateSendButton(false);
    setStatus(label || statusLabel(finalState));
    if (finalState === "completed") {
      var completedTaskId = state.activeTask && state.activeTask.task_id;
      setTimeout(function () {
        if (state.panelRuntime.state === "completed") {
          setStatus("在线");
          if (state.taskNode && state.activeTask && state.activeTask.task_id === completedTaskId) state.taskNode.hidden = true;
        }
      }, 2400);
    }
    var input = state.panel && state.panel.querySelector("[data-query]");
    if (input && typeof input.focus === "function") input.focus();
    renderDiagnostics();
  }

  function computeFinalTaskState() {
    var items = state.uiEventQueue && state.uiEventQueue.items || [];
    if (items.some(function (item) { return item.status === "canceled"; })) return "canceled";
    var failed = items.filter(function (item) { return item.status === "failed"; });
    var business = items.filter(function (item) { return ["ask_clarification", "show_message"].indexOf(item.type) < 0; });
    if (!business.length && items.some(function (item) { return ["ask_clarification", "show_message"].indexOf(item.type) >= 0; })) return "awaiting_clarification";
    failed = business.filter(function (item) { return item.status === "failed"; });
    if (!failed.length) return "completed";
    var succeeded = business.some(function (item) { return item.status === "success"; });
    return succeeded ? "partial_success" : "failed";
  }

  function toggleDiagnostics(forceOpen) {
    if (!state.diagnosticsNode) return;
    state.diagnosticsNode.hidden = typeof forceOpen === "boolean" ? !forceOpen : !state.diagnosticsNode.hidden;
    if (state.panel) state.panel.classList.toggle("has-developer-drawer", !state.diagnosticsNode.hidden);
    renderDiagnostics();
    if (!state.diagnosticsNode.hidden) scheduleAreaScroll(state.activeDiagnosticTab, true);
  }

  function closePanel() {
    if (!state.panel) return;
    setPanelPresentation("launcher");
    saveWindowState(state.panel);
  }

  function openPanel() {
    if (!state.panel) return;
    setPanelPresentation("expanded");
    saveWindowState(state.panel);
  }

  function setPanelPresentation(mode) {
    if (["expanded", "minimized", "launcher"].indexOf(mode) < 0) mode = "expanded";
    var visible = mode !== "launcher";
    var minimized = mode === "minimized";
    state.presentationMode = mode;
    state.closed = !visible;
    state.minimized = minimized;
    if (state.panel) {
      state.panel.hidden = !visible;
      state.panel.classList.toggle("is-expanded", mode === "expanded");
      state.panel.classList.toggle("is-minimized", minimized);
      var minimizeButton = state.panel.querySelector("[data-minimize]");
      if (minimizeButton) {
        minimizeButton.textContent = minimized ? "+" : "−";
        minimizeButton.title = minimized ? "恢复完整面板" : "最小化到标题栏";
      }
      if (mode === "expanded" && state.expandedSize) {
        if (!state.panel.classList.contains("is-docked") && state.expandedSize.height) state.panel.style.height = state.expandedSize.height;
        if (state.expandedSize.width) state.panel.style.width = state.expandedSize.width;
      }
    }
    if (state.launcher) {
      state.launcher.hidden = visible;
      if (!visible && state.runtimeState.state !== "offline") {
        state.launcher.dataset.state = "online";
        state.launcher.textContent = "EV Agent";
      }
    }
    setRuntimeState(state.runtimeState.state, {
      panel_created: !!state.panel,
      panel_visible: visible && !!state.panel
    });
    if (mode === "expanded") {
      scheduleAreaScroll("timeline", true);
      if (state.diagnosticsNode && !state.diagnosticsNode.hidden) scheduleAreaScroll(state.activeDiagnosticTab, true);
    }
  }

  function toggleMinimized() {
    if (!state.panel) return;
    if (state.presentationMode !== "minimized") {
      var rect = state.panel.getBoundingClientRect();
      if (rect.height > 80) state.expandedSize = { width: Math.round(rect.width) + "px", height: Math.round(rect.height) + "px" };
      setPanelPresentation("minimized");
    } else {
      setPanelPresentation("expanded");
    }
    saveWindowState(state.panel);
  }

  function toggleDock(panel) {
    panel.classList.toggle("is-docked");
    if (panel.classList.contains("is-docked")) {
      panel.style.top = "64px";
      panel.style.right = "12px";
      panel.style.left = "auto";
      panel.style.bottom = "12px";
      panel.style.height = "calc(100vh - 76px)";
    } else {
      panel.style.height = state.expandedSize && state.expandedSize.height || "620px";
    }
    appendTimelineMessage("system_status_message", panel.classList.contains("is-docked") ? "面板已停靠到右侧。" : "面板已恢复浮动布局。");
    saveWindowState(panel);
  }

  function clearSession() {
    if (state.timelineNode) replaceChildren(state.timelineNode);
    state.rawEvents = [];
    state.uiEventResults = [];
    state.objectResolutionResults = [];
    state.messageHistory = [];
    state.taskHistory = [];
    state.errors = [];
    state.statusEvents = [];
    state.activeTask = null;
    if (state.uiEventQueue) state.uiEventQueue.reset();
    renderDiagnostics();
    appendTimelineMessage("system_status_message", "会话显示和开发者记录已清空；地图上下文与测试对象保持不变。");
  }

  function exportSession() {
    try {
      var now = new Date();
      var filename = "ev-agent-session-" + formatExportTimestamp(now) + ".json";
      var consistencyWarnings = reconcileExportState();
      var payload = sanitizeExportValue({
        schema_version: "1.0",
        exported_at: now.toISOString(),
        session: {
          session_id: sessionStorage.getItem("ev_agent_session_id") || "webgl-session",
          started_at: state.sessionStartedAt || null,
          current_status: state.activeRequestStatus,
          connection_status: state.connectionStatus,
          active_request_status: state.activeRequestStatus,
          last_request_status: state.lastRequestStatus,
          session_health: state.sessionHealth
        },
        messages: state.messageHistory,
        tasks: state.taskHistory,
        ui_events: state.uiEventLedger,
        buffers: state.buffers,
        buffer_queries: state.bufferQueries,
        dialogue_state: state.dialogueState,
        semantic_traces: state.semanticTraces,
        model_participation: state.modelParticipation,
        qwen_invocations: state.qwenInvocations,
        qwen_health: state.qwenHealth,
        qwen_fallbacks: state.qwenInvocations.filter(function (item) {
          return item && (item.fallback_used || item.status === "fallback" || item.status === "timeout" || item.status === "circuit_open");
        }),
        rag: state.ragSummary,
        rag_retrievals: state.ragRetrievals,
        rag_evidences: state.ragEvidences,
        rag_citations: state.ragCitations,
        rag_fallbacks: state.ragFallbacks,
        rag_source_operations: state.ragSourceOperations,
        rag_index_jobs: state.ragIndexJobs,
        rag_backup_operations: state.ragBackupOperations,
        rag_index_status: state.ragIndexStatus,
        clarifications: state.clarifications,
        export_consistency_warnings: consistencyWarnings,
        raw_messages: state.rawEvents,
        errors: state.errors,
        status_events: state.statusEvents,
        context: {
          selected_object: getSelectedObject(),
          last_context_object: state.panelRuntime.currentObject,
          last_admin_region: state.panelRuntime.currentAdminRegion,
          test_object: state.testObject
        },
        environment: {
          frontend_version: BOOTSTRAP_VERSION,
          backend_configured: !!(state.currentConfig && state.currentConfig.agentBaseUrl)
        }
      });
      var blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
      var url = URL.createObjectURL(blob);
      var anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.hidden = true;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      requestAnimationFrame(function () { URL.revokeObjectURL(url); });
      appendTimelineMessage("system_status_message", "导出成功：" + filename + "（消息 " + state.messageHistory.length + " 条，Tool/UI Event " + state.uiEventLedger.length + " 条）");
      return filename;
    } catch (error) {
      appendErrorCard("会话导出失败：" + (error && error.message || String(error)));
      return null;
    }
  }

  function reconcileExportState() {
    var warnings = [];
    state.dialogueState = state.dialogueState || {};
    var activeBuffers = state.buffers.filter(function (entry) { return entry.rendered === true && entry.status !== "cleared"; });
    var latest = activeBuffers.length ? activeBuffers[activeBuffers.length - 1] : null;
    if ((state.dialogueState.last_buffer && state.dialogueState.last_buffer.buffer_id) !== (latest && latest.buffer_id)) {
      warnings.push({ code: "STATE_RECONCILED", field: "last_buffer", authoritative_source: "adapter_registry" });
      state.dialogueState.last_buffer = latest ? sanitizeExportValue(latest) : null;
    }
    (state.dialogueState.focus_stack || []).forEach(function (focus) {
      if (focus.kind !== "buffer") return;
      var registry = state.buffers.filter(function (entry) { return entry.buffer_id === focus.reference_id; })[0];
      var shouldBeActive = !!(registry && registry.rendered === true && registry.status !== "cleared");
      if (focus.active !== shouldBeActive) { focus.active = shouldBeActive; warnings.push({ code: "STATE_RECONCILED", field: "focus", reference_id: focus.reference_id }); }
    });
    state.semanticTraces.forEach(function (trace) { reconcileSemanticTrace(trace.request_id); });
    return warnings;
  }

  function formatExportTimestamp(date) {
    function pad(value) { return String(value).padStart(2, "0"); }
    return date.getFullYear() + pad(date.getMonth() + 1) + pad(date.getDate()) + "-" + pad(date.getHours()) + pad(date.getMinutes()) + pad(date.getSeconds());
  }

  function sanitizeRagSummary(item) {
    item = item || {};
    var allowed = ["triggered", "retrieval_status", "retrieval_method", "candidate_count", "fused_count", "returned_evidence_count", "top_score", "retrieval_latency_ms", "generation_invoked", "generation_status", "fallback_used", "citation_validation", "citation_coverage", "lexical_invoked", "dense_invoked", "reranker_invoked", "embedding_model", "reranker_model", "index_status", "index_version"];
    var result = {};
    allowed.forEach(function (key) { if (item[key] !== undefined) result[key] = item[key]; });
    return result;
  }

  function sanitizeRagRetrieval(item) {
    item = item || {};
    var allowed = ["request_id", "retrieval_id", "query_chars", "query_language", "retrieval_mode", "retrieval_method", "lexical_invoked", "dense_invoked", "reranker_invoked", "candidate_count", "fused_count", "returned_count", "top_score", "latency_ms", "index_version", "embedding_model", "reranker_model", "status", "generation_invoked", "generation_status", "citation_status", "fallback_used", "error_code", "created_at"];
    var result = {};
    allowed.forEach(function (key) { if (item[key] !== undefined) result[key] = item[key]; });
    return result;
  }

  function sanitizeRagIndexStatus(item) {
    item = item || {};
    var allowed = ["status", "active_index_version", "document_count", "chunk_count", "source_count", "lexical_available", "dense_available", "dense_status", "embedding_model", "vector_backend", "last_build_status", "last_build_at"];
    var result = {};
    allowed.forEach(function (key) { if (item[key] !== undefined) result[key] = item[key]; });
    return result;
  }

  function sanitizeRagEvidence(item) {
    item = item || {};
    return {
      evidence_id: item.evidence_id || null,
      document_id: item.document_id || null,
      chunk_id: item.chunk_id || null,
      title: item.title || null,
      source_type: item.source_type || null,
      source_name: item.source_name || null,
      content: String(item.content || "").slice(0, 360),
      score: item.score === undefined ? null : item.score,
      rank: item.rank === undefined ? null : item.rank,
      section_path: Array.isArray(item.section_path) ? item.section_path.slice(0, 8) : [],
      page_start: item.page_start === undefined ? null : item.page_start,
      page_end: item.page_end === undefined ? null : item.page_end,
      retrieval_method: item.retrieval_method || null,
      metadata: item.metadata && typeof item.metadata === "object" ? { section: item.metadata.section || null } : {}
    };
  }

  function sanitizeRagCitation(item) {
    item = item || {};
    return { citation_id: item.citation_id || null, evidence_id: item.evidence_id || null };
  }

  function sanitizeRagSourceOperation(item) {
    item = item || {};
    var allowed = ["source_id", "operation", "status", "source_status", "job_id", "error_code", "created_at", "updated_at"];
    var result = {};
    allowed.forEach(function (key) { if (item[key] !== undefined) result[key] = item[key]; });
    return result;
  }

  function sanitizeRagIndexJob(item) {
    item = item || {};
    var allowed = ["job_id", "mode", "status", "added", "modified", "deleted", "unchanged", "parsed", "failed", "chunk_count", "embedding_count", "cache_hits", "duration_ms", "version", "error_code", "created_at", "updated_at"];
    var result = {};
    allowed.forEach(function (key) { if (item[key] !== undefined) result[key] = item[key]; });
    return result;
  }

  function sanitizeRagBackupOperation(item) {
    item = item || {};
    var allowed = ["backup_id", "operation", "status", "active_index_version", "active_vector_backend", "source_count", "document_count", "chunk_count", "job_id", "error_code", "created_at", "updated_at", "source_files_restored"];
    var result = {};
    allowed.forEach(function (key) { if (item[key] !== undefined) result[key] = item[key]; });
    return result;
  }

  function sanitizeExportValue(value, seen) {
    seen = seen || [];
    if (value === null || value === undefined) return value;
    if (typeof value === "function") return undefined;
    if (typeof value !== "object") {
      var textValue = String(value);
      if (/bearer\s+[a-z0-9._~+\/-]+/i.test(textValue)) return "[REDACTED]";
      if (/https?:\/\//i.test(textValue) && /(base.?url|endpoint|maas|model)/i.test(textValue)) return "remote_model_service";
      return value;
    }
    if (seen.indexOf(value) >= 0) return "[Circular]";
    seen.push(value);
    if (Array.isArray(value)) return value.map(function (entry) { return sanitizeExportValue(entry, seen); });
    var output = {};
    Object.keys(value).forEach(function (key) {
      if (/(api.?key|authorization|cookie|token|password|secret|credential)/i.test(key)) output[key] = "[REDACTED]";
      else if (/(base.?url|model.?endpoint|endpoint.?url)/i.test(key)) output["model_endpoint_configured"] = !!value[key];
      else output[key] = sanitizeExportValue(value[key], seen);
    });
    return output;
  }

  function showPanelPayload(payload) {
    setEvents([{ event_type: "open_panel", payload: payload }]);
  }

  function showBasicOpenPanel(payload) {
    payload = payload || {};
    var selectedObject = getSelectedObject();
    var panel = ensureBasicPanel();
    var normalized = normalizeOpenPanelPayload(payload);
    panel.querySelector("[data-title]").textContent = normalized.title || "Agent Debug Panel";
    panel.querySelector("[data-object-id]").textContent = selectedObject && selectedObject.object_id || normalized.object_id || "-";
    panel.querySelector("[data-object-type]").textContent = selectedObject && selectedObject.object_type || normalized.object_type || "-";
    panel.querySelector("[data-object-name]").textContent = selectedObject && selectedObject.object_name || normalized.object_name || "-";
    panel.querySelector("[data-payload]").textContent = stringify({
      panel_type: normalized.panel_type || normalized.panel_id || "open_panel_basic",
      payload: normalized.payload || normalized,
      selected_object: selectedObject
    });
    panel.style.display = "block";
  }

  function ensureBasicPanel() {
    if (state.basicPanel) return state.basicPanel;
    var panel = document.createElement("div");
    panel.id = "ev-agent-open-panel-basic";
    panel.style.cssText = [
      "position:fixed",
      "right:16px",
      "top:72px",
      "z-index:999998",
      "width:360px",
      "max-height:calc(100vh - 120px)",
      "overflow:auto",
      "background:rgba(2,6,23,.96)",
      "color:#e5f0ff",
      "font:12px Arial,Microsoft YaHei,sans-serif",
      "border:1px solid rgba(125,211,252,.5)",
      "border-radius:6px",
      "box-shadow:0 10px 30px rgba(0,0,0,.36)",
      "padding:12px",
      "display:none"
    ].join(";");
    panel.innerHTML =
      '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;">' +
      '<strong data-title data-drag-handle style="cursor:move;">Agent Debug Panel</strong>' +
      '<button data-close style="border:0;background:#334155;color:#fff;border-radius:3px;padding:2px 7px;cursor:pointer;">x</button>' +
      '</div>' +
      '<div style="margin-top:8px;color:#93c5fd;">open_panel_basic / mock only</div>' +
      '<table style="width:100%;margin-top:8px;border-collapse:collapse;color:#e5f0ff;">' +
      rowHtml("object_id", "data-object-id") +
      rowHtml("object_type", "data-object-type") +
      rowHtml("object_name", "data-object-name") +
      '</table>' +
      '<div style="margin-top:8px;color:#bfdbfe;">Payload</div>' +
      '<pre data-payload style="white-space:pre-wrap;background:#020617;color:#d9f99d;padding:8px;border-radius:3px;min-height:120px;"></pre>';
    document.body.appendChild(panel);
    makeDraggable(panel, panel.querySelector("[data-drag-handle]"));
    panel.querySelector("[data-close]").addEventListener("click", function () {
      panel.style.display = "none";
    });
    state.basicPanel = panel;
    return panel;
  }

  function rowHtml(label, attr) {
    return '<tr><th style="text-align:left;color:#bfdbfe;padding:4px 6px 4px 0;width:96px;">' +
      escapeHtml(label) +
      '</th><td ' + attr + ' style="padding:4px 0;color:#f8fafc;">-</td></tr>';
  }

  function normalizeOpenPanelPayload(payload) {
    var source = payload.payload && typeof payload.payload === "object" ? payload.payload : payload;
    var result = {};
    Object.keys(source || {}).forEach(function (key) { result[key] = source[key]; });
    if (payload.panel_type && !result.panel_type) result.panel_type = payload.panel_type;
    if (payload.object_id && !result.object_id) result.object_id = payload.object_id;
    if (payload.title && !result.title) result.title = payload.title;
    return result;
  }

  function makeDraggable(panel, handle) {
    bindPanelInteractions(panel, handle, panel && panel.querySelector("[data-resize]"));
  }

  function makeResizable(panel, handle) {
    bindPanelInteractions(panel, panel && panel.querySelector("[data-drag-handle]"), handle);
  }

  function bindPanelInteractions(panel, titleBar, resizeHandle) {
    if (!panel || panel.dataset.interactionsBound === "true") return;
    panel.dataset.interactionsBound = "true";
    var abortController = new AbortController();
    var interaction = {
      mode: "idle",
      pointerId: null,
      startPointerX: 0,
      startPointerY: 0,
      startLeft: 0,
      startTop: 0,
      startWidth: 0,
      startHeight: 0,
      frame: null,
      next: null,
      activeHandle: null,
      abortController: abortController
    };
    panel.__evAgentInteractionState = interaction;
    if (panel.id === "ev-agent-debug-panel") {
      state.interaction = interaction;
    }

    if (titleBar) {
      titleBar.addEventListener("pointerdown", function (event) {
        if (!canStartPanelInteraction(event, panel, "dragging")) return;
        var rect = panel.getBoundingClientRect();
        interaction.mode = "dragging";
        interaction.pointerId = event.pointerId;
        interaction.startPointerX = event.clientX;
        interaction.startPointerY = event.clientY;
        interaction.startLeft = rect.left;
        interaction.startTop = rect.top;
        interaction.activeHandle = titleBar;
        panel.style.left = rect.left + "px";
        panel.style.top = rect.top + "px";
        panel.style.right = "auto";
        panel.style.bottom = "auto";
        if (panel.classList.contains("is-docked")) panel.classList.remove("is-docked");
        if (titleBar.setPointerCapture) titleBar.setPointerCapture(event.pointerId);
        event.preventDefault();
      }, { signal: abortController.signal });
    }

    if (resizeHandle) {
      resizeHandle.addEventListener("pointerdown", function (event) {
        if (!canStartPanelInteraction(event, panel, "resizing")) return;
        var rect = panel.getBoundingClientRect();
        interaction.mode = "resizing";
        interaction.pointerId = event.pointerId;
        interaction.startPointerX = event.clientX;
        interaction.startPointerY = event.clientY;
        interaction.startLeft = rect.left;
        interaction.startTop = rect.top;
        interaction.startWidth = rect.width;
        interaction.startHeight = rect.height;
        interaction.activeHandle = resizeHandle;
        if (resizeHandle.setPointerCapture) resizeHandle.setPointerCapture(event.pointerId);
        event.stopPropagation();
        event.preventDefault();
      }, { signal: abortController.signal });
    }

    panel.addEventListener("pointermove", function (event) {
      if (interaction.mode === "idle" || interaction.pointerId !== event.pointerId) return;
      if (interaction.mode === "dragging") {
        var width = panel.offsetWidth || 360;
        var height = panel.offsetHeight || 420;
        interaction.next = clampPanelRect(
          interaction.startLeft + event.clientX - interaction.startPointerX,
          interaction.startTop + event.clientY - interaction.startPointerY,
          width,
          height
        );
      } else if (interaction.mode === "resizing") {
        var maxWidth = Math.min(720, window.innerWidth - interaction.startLeft);
        var maxHeight = Math.min(860, window.innerHeight - interaction.startTop);
        interaction.next = {
          width: clamp(interaction.startWidth + event.clientX - interaction.startPointerX, 360, Math.max(360, maxWidth)),
          height: clamp(interaction.startHeight + event.clientY - interaction.startPointerY, 420, Math.max(420, maxHeight))
        };
      }
      schedulePanelInteractionFrame(panel, interaction);
      event.preventDefault();
    }, { signal: abortController.signal });

    ["pointerup", "pointercancel", "lostpointercapture"].forEach(function (name) {
      [panel, titleBar, resizeHandle].forEach(function (target) {
        if (!target || target.__evAgentInteractionFinishBound === abortController) return;
        target.addEventListener(name, function (event) {
          finishPanelInteraction(panel, interaction, event);
        }, { signal: abortController.signal });
      });
    });

    window.addEventListener("blur", function () {
      finishPanelInteraction(panel, interaction);
    }, { signal: abortController.signal });
  }

  function canStartPanelInteraction(event, panel, mode) {
    if (!event || event.button !== 0) return false;
    if (!panel || panel.dataset.locked === "true") return false;
    var interaction = panel.__evAgentInteractionState;
    if (interaction && interaction.mode !== "idle") return false;
    if (mode === "dragging" && panel.classList.contains("is-docked")) return false;
    if (mode === "dragging" && event.target && event.target.closest && event.target.closest("button,input,textarea,select,a,[data-no-drag]")) return false;
    return true;
  }

  function schedulePanelInteractionFrame(panel, interaction) {
    if (interaction.frame) return;
    interaction.frame = requestAnimationFrame(function () {
      var next = interaction.next || {};
      if (interaction.mode === "dragging") {
        panel.style.left = Math.round(next.left || 0) + "px";
        panel.style.top = Math.round(next.top || 0) + "px";
      } else if (interaction.mode === "resizing") {
        panel.style.width = Math.round(next.width || interaction.startWidth) + "px";
        panel.style.height = Math.round(next.height || interaction.startHeight) + "px";
      }
      interaction.frame = null;
    });
  }

  function finishPanelInteraction(panel, interaction, event) {
    if (!interaction || interaction.mode === "idle") return;
    if (event && event.pointerId !== undefined && interaction.pointerId !== null && interaction.pointerId !== event.pointerId) return;
    var wasActive = interaction.mode !== "idle";
    if (interaction.activeHandle && interaction.activeHandle.releasePointerCapture && interaction.pointerId !== null) {
      try {
        interaction.activeHandle.releasePointerCapture(interaction.pointerId);
      } catch (error) {}
    }
    interaction.mode = "idle";
    interaction.pointerId = null;
    interaction.activeHandle = null;
    interaction.next = null;
    if (wasActive) {
      clampPanelToViewport(panel);
      saveWindowState(panel);
    }
  }

  function clampPanelRect(left, top, width, height) {
    var safeTop = 0;
    return {
      left: clamp(left, 0, Math.max(0, window.innerWidth - width)),
      top: clamp(top, safeTop, Math.max(safeTop, window.innerHeight - height))
    };
  }

  function clampPanelToViewport(panel) {
    var rect = panel.getBoundingClientRect();
    var next = clampPanelRect(rect.left, rect.top, rect.width, rect.height);
    panel.style.left = Math.round(next.left) + "px";
    panel.style.top = Math.round(next.top) + "px";
    panel.style.right = "auto";
    panel.style.bottom = "auto";
  }

  function preventGlobePointerLeak(panel) {
    ["pointerdown", "pointerup", "pointermove", "click", "dblclick", "wheel", "touchstart", "touchmove"].forEach(function (name) {
      panel.addEventListener(name, function (event) {
        event.stopPropagation();
      }, { passive: name !== "wheel" });
    });
  }

  function installPanelStyles() {
    if (document.getElementById("ev-agent-panel-style")) return;
    var style = document.createElement("style");
    style.id = "ev-agent-panel-style";
    style.textContent = [
      ":root{--ev-agent-bg:#091526;--ev-agent-surface:#101f35;--ev-agent-surface-raised:#162942;--ev-agent-border:rgba(148,177,212,.18);--ev-agent-accent:#4da3ff;--ev-agent-success:#42c98a;--ev-agent-warning:#f5c451;--ev-agent-error:#ff7f91;--ev-agent-text-primary:#edf4fc;--ev-agent-text-secondary:#91a5bf;--ev-agent-radius-sm:8px;--ev-agent-radius-md:12px;--ev-agent-radius-lg:16px;--ev-agent-shadow:0 24px 64px rgba(0,0,0,.46);--ev-agent-z-index:999999;}",
      ".ev-agent-panel{position:fixed;right:16px;bottom:16px;z-index:var(--ev-agent-z-index);width:680px;height:760px;min-width:520px;min-height:520px;max-width:calc(100vw - 16px);max-height:calc(100vh - 16px);display:flex;flex-direction:column;background:var(--ev-agent-bg);border:1px solid var(--ev-agent-border);border-radius:var(--ev-agent-radius-md);box-shadow:var(--ev-agent-shadow);color:var(--ev-agent-text-primary);font:13px/1.5 Microsoft YaHei,Segoe UI,Arial,sans-serif;overflow:hidden;box-sizing:border-box}.ev-agent-panel[hidden],.ev-agent-launcher[hidden],.ev-agent-test-object-dialog[hidden],.ev-agent-diagnostics[hidden],.ev-agent-panel button[hidden]{display:none!important}",
      ".ev-agent-titlebar{height:56px;flex:0 0 56px;display:flex;align-items:center;justify-content:space-between;gap:12px;padding:0 14px;background:#0d1b2e;border-bottom:1px solid var(--ev-agent-border);cursor:move;user-select:none;box-sizing:border-box}.ev-agent-title{display:flex;align-items:center;gap:8px;min-width:0}.ev-agent-title strong{font-size:15px}.ev-agent-title [data-status]{font-size:11px;color:var(--ev-agent-success)}.ev-agent-brand-mark{display:grid;place-items:center;width:30px;height:30px;border-radius:9px;background:linear-gradient(145deg,#5b8cff,#6758df);font-weight:800;color:white}.ev-agent-knowledge-badge{padding:3px 8px;border-radius:999px;background:rgba(77,163,255,.11);color:#a8d2ff;font-size:11px}",
      ".ev-agent-title-actions{display:flex;gap:5px;align-items:center}.ev-agent-title-actions button,.ev-agent-panel button{border:1px solid transparent;background:transparent;color:var(--ev-agent-text-primary);border-radius:var(--ev-agent-radius-sm);padding:6px 9px;min-height:30px;line-height:1.3;cursor:pointer}.ev-agent-title-actions [data-devtools]{border-color:var(--ev-agent-border);color:#c7d7ea}.ev-agent-panel button:hover{background:rgba(77,163,255,.11);border-color:rgba(77,163,255,.3)}.ev-agent-panel button:focus-visible{outline:2px solid var(--ev-agent-accent);outline-offset:2px}.ev-agent-panel button:disabled{opacity:.42;cursor:not-allowed}",
      ".ev-agent-workspace{position:relative;display:grid;grid-template-columns:64px minmax(0,1fr);flex:1;min-height:0}.ev-agent-rail{display:flex;flex-direction:column;align-items:center;gap:8px;padding:14px 8px;border-right:1px solid var(--ev-agent-border);background:#0c192b}.ev-agent-rail button{width:46px;height:46px;display:grid;place-items:center;padding:4px!important;color:var(--ev-agent-text-secondary)}.ev-agent-rail button span{font-size:20px;line-height:1}.ev-agent-rail button em{font-style:normal;font-size:9px;line-height:1}.ev-agent-rail button:hover{color:#cfe5ff}.ev-agent-mascot{margin-top:auto;width:42px;height:42px;display:grid;place-items:center;border-radius:14px;background:linear-gradient(145deg,#173c65,#1e567a);color:#aee5ff;font-weight:800;font-size:11px;box-shadow:inset 0 0 0 1px rgba(125,211,252,.16)}",
      ".ev-agent-body{display:flex;flex:1;min-height:0;min-width:0;flex-direction:column;gap:12px;padding:22px 24px 18px;overflow:hidden}.ev-agent-recommendations{display:flex;gap:8px;flex-wrap:wrap;flex:0 0 auto}.ev-agent-recommendations button{min-height:34px!important;padding:7px 12px!important;border-color:var(--ev-agent-border)!important;border-radius:999px!important;color:#c6d5e7!important;font-size:12px}.ev-agent-context{min-width:0;padding:10px;border-radius:10px;background:var(--ev-agent-surface);color:var(--ev-agent-text-secondary);white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.55}",
      ".ev-agent-attachment-cards{display:flex;flex-direction:column;gap:8px;flex:0 0 auto}.ev-agent-attachment-cards.is-friendly-error{padding:9px 12px;border-radius:10px;background:rgba(245,196,81,.1);color:#f8d982}.ev-file-card{position:relative;display:grid;grid-template-columns:38px minmax(0,1fr);gap:10px;align-items:center;min-height:58px;padding:9px 12px;border-radius:12px;background:var(--ev-agent-surface-raised);overflow:hidden}.ev-file-icon{display:grid;place-items:center;width:36px;height:36px;border-radius:9px;background:rgba(77,163,255,.14);color:#9fcaff;font-size:9px;font-weight:800}.ev-file-summary{display:flex;flex-direction:column;min-width:0;gap:3px}.ev-file-summary strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px}.ev-file-state{color:var(--ev-agent-text-secondary);font-size:11px}.status-active .ev-file-state{color:#8de1b6}.status-failed .ev-file-state,.status-ocr_required .ev-file-state{color:#ffabb7}.ev-file-progress{position:absolute;left:0;bottom:0;height:2px;width:38%;background:linear-gradient(90deg,transparent,var(--ev-agent-accent),transparent);animation:evFileProgress 1.4s ease-in-out infinite}@keyframes evFileProgress{from{transform:translateX(-100%)}to{transform:translateX(360%)}}",
      ".ev-agent-timeline{flex:1;min-height:140px;overflow:auto;display:flex;flex-direction:column;gap:24px;padding:8px 4px 16px}.ev-agent-message{max-width:92%;line-height:1.72;white-space:pre-wrap;word-break:break-word;font-size:14px}.ev-agent-message.user_message{align-self:flex-end;max-width:65%;padding:10px 14px;border-radius:16px 16px 4px 16px;background:#243750}.ev-agent-message.assistant_message,.ev-agent-message.streaming_assistant_message{align-self:flex-start;padding:2px 2px;background:transparent}.ev-agent-message.system_status_message{align-self:center;max-width:84%;padding:5px 9px;border-radius:999px;background:rgba(145,165,191,.1);color:var(--ev-agent-text-secondary);font-size:11px}.ev-agent-message.is-error,.ev-agent-message.error_card{align-self:center;padding:8px 12px;border-radius:10px;background:rgba(255,127,145,.1);color:#ffc0c9;font-size:12px}.ev-agent-message-actions{display:flex;gap:4px;margin-top:5px;opacity:0;transition:opacity .16s}.ev-agent-message:hover .ev-agent-message-actions,.ev-agent-message:focus-within .ev-agent-message-actions{opacity:.72}.ev-agent-message-actions button{min-height:24px!important;padding:2px 6px!important;font-size:10px}.ev-citation-chip{display:inline-grid!important;place-items:center;min-height:20px!important;padding:1px 6px!important;margin:0 2px;border-radius:999px!important;background:rgba(77,163,255,.14)!important;color:#9fcaff!important;font-size:10px!important;vertical-align:baseline}",
      ".ev-agent-card{padding:12px;border-radius:12px;background:var(--ev-agent-surface-raised);display:flex;flex-direction:column;gap:6px}.ev-agent-card-actions{display:flex;flex-wrap:wrap;gap:6px;margin-top:5px}.ev-agent-task{flex:0 0 auto;padding:8px 10px;border-radius:10px;background:rgba(77,163,255,.08);line-height:1.4}.ev-agent-task-summary{display:flex;gap:8px;align-items:center}.ev-agent-task-state{color:#9fcaff;font-weight:700}.ev-agent-task-desc{min-width:0;color:#d9e8f8}.ev-agent-task-time{margin-left:auto;color:var(--ev-agent-text-secondary);font-size:11px}",
      ".ev-agent-quick{flex:0 0 auto;display:flex;gap:8px;flex-wrap:wrap}.ev-agent-quick button{min-height:30px!important;padding:5px 10px!important;border-color:var(--ev-agent-border)!important}.ev-agent-quick button[data-state=loading]{opacity:.72;cursor:progress}.ev-agent-quick button[data-state=success]{color:#8de1b6}.ev-agent-quick button[data-state=failed]{color:#ffabb7}.ev-agent-document-scope{flex:0 0 auto;color:var(--ev-agent-text-secondary);font-size:11px;padding:0 4px}.ev-agent-composer{flex:0 0 auto;min-width:0;display:grid;grid-template-columns:38px minmax(0,1fr) 40px;gap:6px;align-items:end;padding:9px 10px;border-radius:16px;background:#f4f7fb;box-shadow:0 10px 30px rgba(0,0,0,.24)}.ev-agent-composer textarea{resize:none;box-sizing:border-box;width:100%;min-width:0;min-height:40px;max-height:160px;border:0;outline:0;background:transparent;color:#14233a;padding:9px 4px;line-height:1.5;font:14px Microsoft YaHei,Segoe UI,sans-serif;overflow:auto}.ev-agent-composer textarea::placeholder{color:#8491a3}.ev-agent-composer button{border:0!important}.ev-agent-attach-button{width:36px;height:36px;padding:0!important;color:#66758a!important;font-size:20px}.ev-agent-send-button{width:38px;height:38px;padding:0!important;border-radius:12px!important;background:#2b84e9!important;color:white!important;font-size:20px}.ev-agent-send-button[data-state=running]{border-radius:50%!important;background:#245d96!important}.stop-indicator{position:relative;width:26px;height:26px;display:grid;place-items:center}.spinner-ring{position:absolute;inset:0;border:2px solid rgba(255,255,255,.28);border-top-color:#fff;border-radius:50%;animation:evAgentSpin .85s linear infinite}.stop-square{width:8px;height:8px;border-radius:2px;background:#fff}@keyframes evAgentSpin{to{transform:rotate(360deg)}}",
      ".ev-agent-diagnostics{position:absolute;z-index:9;top:0;right:0;bottom:0;width:min(380px,82%);display:flex;flex-direction:column;gap:10px;padding:14px;background:#0b1729;border-left:1px solid var(--ev-agent-border);box-shadow:-18px 0 42px rgba(0,0,0,.36)}.ev-agent-dev-heading{display:flex;align-items:center;justify-content:space-between}.ev-agent-dev-heading strong{font-size:15px}.ev-agent-tabs,.ev-agent-dev-actions{display:flex;gap:5px;flex-wrap:wrap}.ev-agent-tabs button{font-size:11px}.ev-agent-tabs button.is-active{background:rgba(77,163,255,.15);color:#a8d2ff}.ev-agent-dev-scroll{position:relative;flex:1;min-height:0;overflow:auto;background:#071221;border-radius:10px}.ev-agent-diagnostics pre{margin:0;min-height:100%;box-sizing:border-box;padding:10px;color:#cde7ff;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word}.ev-agent-bottom{position:absolute;right:28px;bottom:118px;z-index:2}.ev-agent-dev-bottom{position:sticky;float:right;right:8px;bottom:8px;z-index:2}.ev-agent-resize{position:absolute;right:0;bottom:0;width:14px;height:14px;cursor:nwse-resize;border-right:2px solid rgba(77,163,255,.5);border-bottom:2px solid rgba(77,163,255,.5)}",
      ".ev-agent-launcher{position:fixed;right:16px;bottom:16px;z-index:var(--ev-agent-z-index);border:1px solid var(--ev-agent-border);background:#0d1b2e;color:var(--ev-agent-text-primary);border-radius:12px;padding:9px 13px;box-shadow:var(--ev-agent-shadow);cursor:pointer}.ev-agent-launcher[data-state=offline]{color:#f8d982}.ev-agent-launcher[data-state=online]{color:#8de1b6}.ev-agent-panel.is-minimized{height:56px!important;min-height:0!important;max-height:56px!important}.ev-agent-panel.is-minimized .ev-agent-workspace,.ev-agent-panel.is-minimized .ev-agent-resize{display:none!important}.ev-agent-test-object-dialog{position:absolute;inset:56px 0 0;z-index:11;display:grid;place-items:center;padding:16px;background:rgba(4,12,25,.8)}.ev-agent-test-object-card{width:min(380px,100%);max-height:100%;overflow:auto;display:flex;flex-direction:column;gap:9px;padding:16px;border-radius:16px;background:var(--ev-agent-surface)}.ev-agent-test-object-card label{display:flex;flex-direction:column;gap:4px}.ev-agent-test-object-card input,.ev-agent-test-object-card textarea{box-sizing:border-box;width:100%;border:1px solid var(--ev-agent-border);border-radius:8px;background:#0a1728;color:var(--ev-agent-text-primary);padding:8px}.ev-agent-test-actions{display:flex;gap:6px;flex-wrap:wrap}.ev-agent-validation{color:#ffabb7}@media (prefers-reduced-motion:reduce){.spinner-ring,.ev-file-progress{animation:none}}@media (max-width:620px){.ev-agent-panel{min-width:360px}.ev-agent-workspace{grid-template-columns:54px minmax(0,1fr)}.ev-agent-body{padding:16px}.ev-agent-rail{padding-inline:4px}.ev-agent-message.user_message{max-width:82%}.ev-agent-knowledge-badge{display:none}}"
    ].join("\n");
    document.head.appendChild(style);
  }

  function applySavedWindowState(panel) {
    var saved = readSavedWindowState();
    panel.style.width = saved.width || "680px";
    panel.style.height = saved.height || "760px";
    state.expandedSize = { width: saved.width || "680px", height: saved.height || "760px" };
    if (saved.left && saved.top) {
      panel.style.left = saved.left;
      panel.style.top = saved.top;
      panel.style.right = "auto";
      panel.style.bottom = "auto";
    } else {
      panel.style.right = "16px";
      panel.style.bottom = "16px";
    }
    if (saved.minimized) {
      state.minimized = true;
      state.presentationMode = "minimized";
      panel.classList.add("is-minimized");
    } else {
      panel.classList.add("is-expanded");
    }
  }

  function readSavedWindowState() {
    try {
      var saved = JSON.parse(localStorage.getItem("ev_agent_panel_window") || "{}") || {};
      if (saved.version !== PANEL_STATE_VERSION) return {};
      var width = parseCssPixels(saved.width, 520, Math.max(520, global.innerWidth || 680));
      var height = parseCssPixels(saved.height, 520, Math.max(520, global.innerHeight || 760));
      var left = parseCssPixels(saved.left, 0, Math.max(0, (global.innerWidth || 440) - width));
      var top = parseCssPixels(saved.top, 0, Math.max(0, (global.innerHeight || 620) - height));
      return {
        left: left === null ? null : Math.round(left) + "px",
        top: top === null ? null : Math.round(top) + "px",
        width: Math.round(width || 440) + "px",
        height: Math.round(height || 620) + "px",
        docked: saved.docked === true,
        minimized: saved.minimized === true,
        version: PANEL_STATE_VERSION
      };
    } catch (error) {
      return {};
    }
  }

  function parseCssPixels(value, min, max) {
    if (value === undefined || value === null || value === "") return null;
    var number = Number(String(value).replace("px", ""));
    if (!Number.isFinite(number)) return null;
    return clamp(number, min, max);
  }

  function saveWindowState(panel) {
    if (!panel) return;
    var rect = panel.getBoundingClientRect();
    localStorage.setItem("ev_agent_panel_window", JSON.stringify({
      left: Math.round(rect.left) + "px",
      top: Math.round(rect.top) + "px",
      width: state.minimized && state.expandedSize ? state.expandedSize.width : Math.round(rect.width) + "px",
      height: state.minimized && state.expandedSize ? state.expandedSize.height : Math.round(rect.height) + "px",
      minimized: state.minimized,
      docked: panel.classList.contains("is-docked"),
      version: PANEL_STATE_VERSION
    }));
  }

  function handleGlobalShortcut(event) {
    if (event.ctrlKey && event.shiftKey && event.key.toLowerCase() === "e") {
      event.preventDefault();
      openPanel();
    }
    if (event.altKey && event.shiftKey && event.key.toLowerCase() === "z") {
      appendTimelineMessage("system_status_message", "已完成的地图操作不会自动回滚。");
    }
    if (event.ctrlKey && event.shiftKey && event.key.toLowerCase() === "k") {
      appendTimelineMessage("system_status_message", "快捷命令面板将在后续版本接入。");
    }
  }

  function scrollNodeForArea(key) {
    return key === "timeline" ? state.timelineNode : state.devScrollNode;
  }

  function isNearAreaBottom(node) {
    return !node || node.scrollHeight - node.scrollTop - node.clientHeight <= 64;
  }

  function scheduleAreaScroll(key, force) {
    var node = scrollNodeForArea(key);
    if (!node || (!force && state.scrollFollow[key] === false)) {
      updateAreaBottomButton(key);
      return;
    }
    requestAnimationFrame(function () {
      node.scrollTop = node.scrollHeight;
      requestAnimationFrame(function () {
        if (force || state.scrollFollow[key] !== false) node.scrollTop = node.scrollHeight;
        updateAreaBottomButton(key);
      });
    });
  }

  function handleAreaScroll(key) {
    var node = scrollNodeForArea(key);
    if (!node) return;
    state.scrollFollow[key] = isNearAreaBottom(node);
    if (key !== "timeline") state.diagnosticScroll[key] = node.scrollTop;
    updateAreaBottomButton(key);
  }

  function scrollAreaToBottom(key, userAction) {
    state.scrollFollow[key] = true;
    var node = scrollNodeForArea(key);
    if (node && userAction && typeof node.scrollTo === "function") node.scrollTo({ top: node.scrollHeight, behavior: "smooth" });
    else if (node) node.scrollTop = node.scrollHeight;
    scheduleAreaScroll(key, true);
  }

  function updateAreaBottomButton(key) {
    var node = scrollNodeForArea(key);
    var button = key === "timeline" ? state.panel && state.panel.querySelector("[data-bottom]") : state.devBottomNode;
    if (!node || !button) return;
    button.hidden = isNearAreaBottom(node);
  }

  function autoScrollTimeline() { scheduleAreaScroll("timeline", false); }
  function scrollTimelineToBottom() { scrollAreaToBottom("timeline", true); }
  function updateBottomButton() { updateAreaBottomButton("timeline"); }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text || "").catch(function () {});
    }
  }

  function replaceChildren(node) {
    while (node && node.firstChild) node.removeChild(node.firstChild);
  }

  function setStatus(value) {
    var labels = { "Agent online": "在线", "Agent 未连接": "未连接", "空闲": "在线", "已完成": "在线" };
    if (state.statusNode) state.statusNode.textContent = labels[value] || value;
  }

  function setResponse(value) {
    if (state.responseNode) state.responseNode.textContent = value || "";
  }

  function setEvents(value) {
    if (state.eventsNode) state.eventsNode.textContent = stringify(value || []);
  }

  function setError(value) {
    if (state.errorNode) state.errorNode.textContent = value || "";
  }

  function appendMessage(value) {
    if (!state.messagesNode) return;
    state.messagesNode.textContent = (state.messagesNode.textContent + "\n" + value).trim();
  }

  function merge(base, patch) {
    var result = {};
    Object.keys(base || {}).forEach(function (key) { result[key] = base[key]; });
    Object.keys(patch || {}).forEach(function (key) { result[key] = patch[key]; });
    return result;
  }

  function normalizeConfig(config) {
    config = merge(config || {}, readMetaConfig());
    var environment = String(config.environment || config.mode || "development").toLowerCase();
    if (!config.agentBaseUrl) {
      config.agentBaseUrl = environment === "production"
        ? (global.location && global.location.origin ? global.location.origin : "")
        : DEVELOPMENT_AGENT_BASE_URL;
    }
    config.agentBaseUrl = trimTrailingSlash(config.agentBaseUrl);
    config.handlerTimeoutMs = Math.max(1000, Number(config.handlerTimeoutMs || 15000));
    if (config.pollIntervalMs) {
      config.retryIntervalMs = Number(config.pollIntervalMs) || config.retryIntervalMs;
    }
    if (config.viewerTimeoutMs) {
      config.maxRetries = Math.max(
        1,
        Math.ceil(Number(config.viewerTimeoutMs) / Number(config.retryIntervalMs || 300))
      );
    }
    return config;
  }

  function readMetaConfig() {
    var result = {};
    if (!document.querySelector) return result;
    var baseUrl = document.querySelector('meta[name="ev-agent-base-url"]');
    var env = document.querySelector('meta[name="ev-agent-environment"]');
    var releaseId = document.querySelector('meta[name="ev-agent-release-id"]');
    var webglArtifact = document.querySelector('meta[name="ev-agent-webgl-artifact"]');
    if (baseUrl && baseUrl.getAttribute("content")) result.agentBaseUrl = baseUrl.getAttribute("content");
    if (env && env.getAttribute("content")) {
      result.environment = env.getAttribute("content");
      result.devMode = String(env.getAttribute("content")).toLowerCase() !== "production";
    }
    if (releaseId && releaseId.getAttribute("content")) result.releaseId = releaseId.getAttribute("content");
    if (webglArtifact && webglArtifact.getAttribute("content")) result.webglArtifact = webglArtifact.getAttribute("content");
    return result;
  }

  function compareReleaseIds(pageReleaseId, backendReleaseId) {
    if (!pageReleaseId || !backendReleaseId || pageReleaseId === backendReleaseId) return null;
    return {
      code: "EV_AGENT_RELEASE_MISMATCH",
      message: "前端与后端版本不一致，请重新启动当前发布版本。",
      page_release_id: pageReleaseId,
      backend_release_id: backendReleaseId
    };
  }

  function pickFirst() {
    for (var i = 0; i < arguments.length; i += 1) {
      if (arguments[i] !== undefined && arguments[i] !== null && arguments[i] !== "") return arguments[i];
    }
    return null;
  }

  function clamp(value, min, max) {
    return Math.min(Math.max(value, min), max);
  }

  function stringify(value) {
    try {
      return typeof value === "string" ? value : JSON.stringify(value, null, 2);
    } catch (error) {
      return String(value);
    }
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, function (char) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char];
    });
  }

  function log(config, message, detail) {
    if (config.debug !== false && global.console && console.log) {
      console.log("[EV Agent Bootstrap] " + message, detail || "");
    }
  }

  function warn(message, detail) {
    if (global.console && console.warn) {
      console.warn("[EV Agent Bootstrap] " + message, detail || "");
    }
  }

  function traceBootstrapStage(label, detail) {
    if (global.console && console.log) {
      console.log("[EV Agent Bootstrap] " + label, detail || "");
    }
  }

  global.EVAgentBootstrap = {
    start: start,
    state: state,
    version: BOOTSTRAP_VERSION,
    loadConversationMessages: loadConversationMessages,
    _test: {
      bindPanelInteractions: bindPanelInteractions,
      clampPanelRect: clampPanelRect,
      clampPanelToViewport: clampPanelToViewport,
      readSavedWindowState: readSavedWindowState,
      isEVAgentEnabledPage: isEVAgentEnabledPage,
      checkAgentBackendHealth: checkAgentBackendHealth,
      setRuntimeState: setRuntimeState,
      setPanelPresentation: setPanelPresentation,
      exportSession: exportSession,
      sanitizeExportValue: sanitizeExportValue,
      scheduleAreaScroll: scheduleAreaScroll,
      getRuntimeState: function () { return merge({}, state.runtimeState); }
    }
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})(window);
