(function (global) {
  "use strict";

  var state = {
    agentBaseUrl: "",
    sessionId: "webgl-session",
    userId: "webgl-user",
    handlerTimeoutMs: 15000,
    role: "杩愮淮浜哄憳",
    getContext: function () {
      return {};
    },
    eventHandlers: {},
    onResponse: null,
    onError: null
  };

  var defaultHandlers = {};
  var DEVELOPMENT_AGENT_BASE_URL = "http://127.0.0.1:8009";
  var EVENT_ALIASES = {
    getCameraState: "get_camera_state",
    flyToCoordinates: "fly_to_coordinates",
    resetView: "reset_view",
    getLayerList: "get_layer_list",
    getLayerTree: "get_layer_tree",
    setLayerVisibility: "set_layer_visibility",
    getSelectedObject: "get_selected_object",
    getObjectProperties: "get_object_properties",
    locateAdminRegion: "locate_admin_region",
    highlightAdminBoundary: "highlight_admin_boundary",
    getAdminRegionProperties: "get_admin_region_properties",
    flyToObject: "fly_to_object",
    getPanelState: "get_panel_state",
    openSourcePanel: "open_source_panel",
    closeSourcePanel: "close_source_panel",
    getSelectionState: "get_selection_state",
    createBuffer: "create_buffer",
    getBufferState: "get_buffer_state",
    getBufferResult: "get_buffer_result",
    flyToBuffer: "fly_to_buffer",
    clearBuffer: "clear_buffer",
    clearAllBuffers: "clear_all_buffers"
    ,highlightBufferQueryResults: "highlight_buffer_query_results"
    ,clearBufferQueryHighlight: "clear_buffer_query_highlight"
  };

  [
    "fly_to",
    "fly_to_coordinates",
    "gis_highlight",
    "open_panel",
    "show_message",
    "ask_clarification",
    "set_layer_visibility",
    "get_layer_list",
    "get_layer_tree",
    "get_camera_state",
    "reset_view",
    "get_selected_object",
    "get_object_properties",
    "locate_admin_region",
    "highlight_admin_boundary",
    "get_admin_region_properties",
    "fly_to_object",
    "get_panel_state",
    "open_source_panel",
    "close_source_panel",
    "get_selection_state",
    "create_buffer",
    "get_buffer_state",
    "get_buffer_result",
    "fly_to_buffer",
    "clear_buffer",
    "clear_all_buffers",
    "query_objects_in_buffer",
    "highlight_buffer_query_results",
    "clear_buffer_query_highlight",
    "screenshot",
    "clear_highlight",
    "unsupported_event"
  ].forEach(function (eventType) {
    defaultHandlers[eventType] = function (event) {
      if (global.console && console.warn) {
        console.warn("[WebGLAgent] no handler registered: " + event.event_type, event);
      }
      return {
        success: false,
        completed: false,
        error_code: "HANDLER_NOT_REGISTERED",
        error: "WebGL handler is not registered for " + event.event_type + "."
      };
    };
  });

  function configure(options) {
    options = options || {};
    if (options.agentBaseUrl) state.agentBaseUrl = trimTrailingSlash(options.agentBaseUrl);
    if (options.handlerTimeoutMs) state.handlerTimeoutMs = Math.max(1000, Number(options.handlerTimeoutMs) || state.handlerTimeoutMs);
    if (options.sessionId) state.sessionId = options.sessionId;
    if (options.userId) state.userId = options.userId;
    if (options.role) state.role = options.role;
    if (typeof options.getContext === "function") state.getContext = options.getContext;
    if (typeof options.onResponse === "function") state.onResponse = options.onResponse;
    if (typeof options.onError === "function") state.onError = options.onError;
    if (options.eventHandlers) {
      Object.keys(options.eventHandlers).forEach(function (eventType) {
        registerHandler(eventType, options.eventHandlers[eventType]);
      });
    }
    logRegisteredHandlers();
  }

  function registerHandler(eventType, handler) {
    if (!eventType || typeof handler !== "function") {
      throw new Error("registerHandler requires an eventType and a function handler.");
    }
    var canonicalType = canonicalEventType(eventType);
    state.eventHandlers[canonicalType] = handler;
    state.eventHandlers[eventType] = handler;
  }

  function listHandlers() {
    return Object.keys(state.eventHandlers)
      .filter(function (eventType) {
        return typeof state.eventHandlers[eventType] === "function";
      })
      .sort();
  }

  function logRegisteredHandlers() {
    if (global.console && console.log) {
      console.log("[WebGLAgentBridge] registered handlers:", listHandlers());
    }
  }

  async function sendQuery(query, extraContext) {
    extraContext = extraContext || {};
    try {
      var webglContext = await Promise.resolve(state.getContext());
      var gisContext = mergeObjects(webglContext || {}, extraContext.gis_context || extraContext.context || {});
      var payload = {
        session_id: extraContext.session_id || state.sessionId,
        user_id: extraContext.user_id || state.userId,
        role: extraContext.role || state.role,
        query: query,
        gis_context: gisContext
      };

      var response = await fetch(resolveAgentBaseUrl() + "/agent/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      if (!response.ok) {
        var text = await response.text();
        throw new Error("Agent HTTP " + response.status + ": " + text.slice(0, 200));
      }

      var agentResponse = await response.json();
      agentResponse.ui_events = extractUIEvents(agentResponse);
      agentResponse.ui_event_results = await executeUIEvents(agentResponse.ui_events || []);
      if (state.onResponse) state.onResponse(agentResponse, payload);
      return agentResponse;
    } catch (error) {
      var normalized = {
        status: "failed",
        message: error && error.message ? error.message : String(error),
        error: error
      };
      if (state.onError) state.onError(normalized);
      throw normalized;
    }
  }

  async function executeUIEvents(uiEvents, context) {
    var results = [];
    for (var i = 0; i < (uiEvents || []).length; i += 1) {
      results.push(await executeUIEvent(uiEvents[i], context));
    }
    return results;
  }

  async function executeUIEvent(rawEvent, context) {
    var event = normalizeEvent(rawEvent);
    context = context || {};
    if (context.task_id && !event.task_id) event.task_id = context.task_id;
    if (context.trace_id && !event.trace_id) event.trace_id = context.trace_id;
    if (context.request_id && !event.request_id) event.request_id = context.request_id;
    if (!event.event_id) event.event_id = makeEventId(event);
    logAgent("dispatch ui_event: " + event.event_type, event);
    ensureAdapterHandlers();
    var handler = resolveHandler(event.event_type);
    if (!handler) {
      var missing = "No handler registered for " + event.event_type;
      logAgent("no handler registered: " + event.event_type, event);
      return eventResult(event, false, null, "HANDLER_NOT_FOUND", missing);
    }
    try {
      logAgent("call handler: " + event.event_type, event);
      var timeoutMs = Number(event.payload && event.payload.timeout_ms || event.timeout_ms || context.handlerTimeoutMs || state.handlerTimeoutMs);
      var handlerResult = await withTimeout(Promise.resolve(handler(event.payload || {}, event)), timeoutMs, event);
      if (handlerResult && handlerResult.__ev_agent_result_wrapped) return handlerResult.value;
      return normalizeHandlerResult(event, handlerResult);
    } catch (error) {
      return eventResult(
        event,
        false,
        null,
        "HANDLER_EXECUTION_FAILED",
        error && error.message ? error.message : String(error)
      );
    }
  }

  function resolveHandler(eventType) {
    return state.eventHandlers[eventType] || null;
  }

  function eventResult(event, success, result, errorCode, error) {
    var completedAt = Date.now();
    return {
      task_id: event.task_id || null,
      trace_id: event.trace_id || null,
      request_id: event.request_id || null,
      event_id: event.event_id || makeEventId(event),
      type: event.type,
      event_type: event.event_type,
      status: success ? "success" : "failed",
      success: !!success,
      payload: event.payload || {},
      result: result,
      error_code: errorCode || null,
      error: error || null,
      started_at: event.__started_at || null,
      completed_at: completedAt,
      duration_ms: event.__started_at ? completedAt - event.__started_at : null
    };
  }

  function normalizeHandlerResult(event, handlerResult) {
    var success = handlerResult !== undefined && handlerResult !== false && !(handlerResult && handlerResult.success === false);
    return eventResult(
      event,
      success,
      handlerResult === undefined ? null : handlerResult,
      handlerResult && (handlerResult.error_code || handlerResult.code) || (handlerResult === undefined ? "EMPTY_HANDLER_RESULT" : null),
      success ? null : handlerResult && (handlerResult.error || handlerResult.message) || (handlerResult === undefined ? "WebGL handler returned no execution result." : null)
    );
  }

  function withTimeout(promise, timeoutMs, event) {
    event.__started_at = Date.now();
    if (!timeoutMs || timeoutMs <= 0) return promise;
    return new Promise(function (resolve, reject) {
      var done = false;
      var timer = setTimeout(function () {
        if (done) return;
        done = true;
        reject(new Error("HANDLER_TIMEOUT after " + timeoutMs + "ms"));
      }, timeoutMs);
      promise.then(function (value) {
        if (done) return;
        done = true;
        clearTimeout(timer);
        resolve(value);
      }).catch(function (error) {
        if (done) return;
        done = true;
        clearTimeout(timer);
        reject(error);
      });
    }).catch(function (error) {
      if (String(error && error.message || error).indexOf("HANDLER_TIMEOUT") >= 0) {
        return {
          __ev_agent_result_wrapped: true,
          value: eventResult(event, false, null, "HANDLER_TIMEOUT", error.message || String(error))
        };
      }
      throw error;
    });
  }

  function ensureAdapterHandlers() {
    var adapter = global.EVWebGLAgentAdapter || global.AgentAdapter;
    // Customer mode can reach the action queue while the bootstrap retry loop
    // is still waiting on the host viewer. Lazily attach one adapter instance
    // once the real viewer is available, then keep registration idempotent.
    if (!adapter && typeof global.createEVWebGLAgentAdapter === "function" && global.viewer) {
      try {
        adapter = global.createEVWebGLAgentAdapter({
          viewer: global.viewer,
          layerManager: global.LayerManager || global.evLayerManager || global.EV_LayerManager
        });
        if (adapter && typeof adapter.attach === "function") adapter.attach();
      } catch (error) {
        if (global.console && console.warn) console.warn("[WebGLAgent] adapter lazy attach failed", error);
      }
    }
    if (adapter && typeof adapter.registerAllHandlers === "function") {
      adapter.registerAllHandlers();
    }
  }

  function extractUIEvents(agentResponse) {
    var events = [];
    if (agentResponse && Array.isArray(agentResponse.ui_events)) {
      events = events.concat(agentResponse.ui_events);
    }
    if (
      agentResponse &&
      agentResponse.response &&
      Array.isArray(agentResponse.response.ui_events)
    ) {
      events = events.concat(agentResponse.response.ui_events);
    }
    return events;
  }

  function normalizeEvent(event) {
    event = event || {};
    var rawType = event.type || event.event_type || event.action || event.name || null;
    var eventType = rawType ? canonicalEventType(rawType) : "unsupported_event";
    var payload = event.payload || event.params || {};
    if (event.type === "safety_block" && !event.event_type) {
      eventType = "show_message";
      payload = { level: "warning", message: event.message || "Request blocked by safety rules." };
    }
    if (event.type === "ask_clarification" && !event.event_type) {
      eventType = "show_message";
      payload = { level: "info", message: event.message || "Please provide the required parameters." };
    }
    return {
      type: eventType,
      event_type: eventType,
      payload: payload,
      source_event: event,
      task_id: event.task_id || event.taskId || null,
      trace_id: event.trace_id || event.traceId || null,
      request_id: event.request_id || event.requestId || null,
      event_id: event.event_id || event.eventId || null,
      timeout_ms: event.timeout_ms || event.timeoutMs || null
    };
  }

  function makeEventId(event) {
    return [
      event.request_id || "-",
      event.trace_id || "-",
      event.task_id || "-",
      event.event_type || event.type || "event",
      event.source_event && (event.source_event.sequence || event.source_event.index) || event.sequence || "0"
    ].join(":");
  }

  function canonicalEventType(eventType) {
    var value = String(eventType || "").trim();
    return EVENT_ALIASES[value] || value || "unsupported_event";
  }

  function mergeObjects(base, patch) {
    var result = {};
    Object.keys(base || {}).forEach(function (key) {
      result[key] = base[key];
    });
    Object.keys(patch || {}).forEach(function (key) {
      result[key] = patch[key];
    });
    return result;
  }

  function trimTrailingSlash(value) {
    return String(value || "").replace(/\/+$/, "");
  }

  function resolveAgentBaseUrl() {
    if (state.agentBaseUrl) return trimTrailingSlash(state.agentBaseUrl);
    if (global.EV_AGENT_CONFIG && global.EV_AGENT_CONFIG.agentBaseUrl) {
      return trimTrailingSlash(global.EV_AGENT_CONFIG.agentBaseUrl);
    }
    var marker = global.document && document.querySelector && document.querySelector('meta[name="ev-agent-base-url"]');
    if (marker && marker.getAttribute("content")) return trimTrailingSlash(marker.getAttribute("content"));
    var env = global.EV_AGENT_CONFIG && (global.EV_AGENT_CONFIG.environment || global.EV_AGENT_CONFIG.mode);
    env = String(env || "development").toLowerCase();
    if (env === "production") {
      return global.location && global.location.origin ? trimTrailingSlash(global.location.origin) : "";
    }
    return DEVELOPMENT_AGENT_BASE_URL;
  }

  function logAgent(message, detail) {
    if (global.console && console.log) {
      console.log("[WebGLAgent] " + message, detail || "");
    }
  }

  global.WebGLAgentBridge = {
    configure: configure,
    sendQuery: sendQuery,
    executeUIEvents: executeUIEvents,
    executeUIEvent: executeUIEvent,
    extractUIEvents: extractUIEvents,
    registerHandler: registerHandler,
    listHandlers: listHandlers,
    logRegisteredHandlers: logRegisteredHandlers,
    defaultHandlers: defaultHandlers,
    version: "0.1.0"
  };
})(window);
