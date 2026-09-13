(function (global) {
  "use strict";

  var DEFAULT_ALLOWED_EVENTS = [
    "fly_to",
    "fly_to_coordinates",
    "fly_to_object",
    "gis_highlight",
    "clear_highlight",
    "open_panel",
    "show_message",
    "ask_clarification",
    "get_camera_state",
    "get_layer_tree",
    "get_object_properties",
    "search_business_objects",
    "query_nearby_objects",
    "create_buffer",
    "get_buffer_state",
    "get_buffer_result",
    "fly_to_buffer",
    "clear_buffer",
    "clear_all_buffers",
    "query_objects_in_buffer",
    "highlight_buffer_query_results",
    "clear_buffer_query_highlight",
    "locate_admin_region",
    "highlight_admin_boundary",
    "get_admin_region_properties",
    "get_selected_object",
    "get_selection_state",
    "screenshot"
  ];

  function AgentUIEventQueue(options) {
    options = options || {};
    this.allowed = {};
    (options.allowedEvents || DEFAULT_ALLOWED_EVENTS).forEach(function (type) {
      this.allowed[type] = true;
    }, this);
    this.bridge = options.bridge || global.WebGLAgentBridge || null;
    this.sourceAnchors = options.sourceAnchors || global.EVWebGLSourceAnchors || null;
    this.onUpdate = typeof options.onUpdate === "function" ? options.onUpdate : function () {};
    this.taskId = options.taskId || null;
    this.traceId = options.traceId || null;
    this.requestId = options.requestId || null;
    this.handlerTimeoutMs = Number(options.handlerTimeoutMs || 15000);
    this.items = [];
    this.executing = false;
    this.canceled = false;
    this.drainTimer = null;
  }

  AgentUIEventQueue.prototype.enqueue = function (event) {
    var normalized = normalizeEvent(event);
    normalized.task_id = normalized.task_id || this.taskId;
    normalized.trace_id = normalized.trace_id || this.traceId;
    normalized.request_id = normalized.request_id || this.requestId;
    normalized.event_id = normalized.event_id || makeEventId(normalized);
    var duplicate = this.items.filter(function (item) { return item.event_id === normalized.event_id; })[0];
    if (duplicate) return duplicate;
    normalized.queued_at = new Date().toISOString();
    normalized.started_at = null;
    normalized.completed_at = null;
    if (!this.allowed[normalized.type]) {
      normalized.status = "failed";
      normalized.error_code = "UI_EVENT_NOT_ALLOWED";
      normalized.error = "Unsupported ui_event type: " + normalized.type;
      normalized.completed_at = new Date().toISOString();
      this.items.push(normalized);
      this.onUpdate(normalized, this.items.slice());
      return normalized;
    }
    normalized.status = "queued";
    this.items.push(normalized);
    this.items.sort(function (a, b) {
      return (a.sequence || 0) - (b.sequence || 0);
    });
    this.onUpdate(normalized, this.items.slice());
    this.scheduleDrain();
    return normalized;
  };

  AgentUIEventQueue.prototype.scheduleDrain = function () {
    if (this.drainTimer) return;
    var self = this;
    this.drainTimer = setTimeout(function () {
      self.drainTimer = null;
      self.drain();
    }, 0);
  };

  AgentUIEventQueue.prototype.drain = async function () {
    if (this.executing || this.canceled) return;
    this.executing = true;
    try {
      while (!this.canceled) {
        var next = null;
        for (var i = 0; i < this.items.length; i += 1) {
          if (this.items[i].status === "queued") {
            next = this.items[i];
            break;
          }
        }
        if (!next) break;
        await this.execute(next);
        if (next.status === "failed") {
          var failedAt = new Date().toISOString();
          this.items.forEach(function (item) {
            if (item.status === "queued") {
              item.status = "canceled";
              item.error_code = "DEPENDENCY_FAILED";
              item.error = "A previous UI Event failed; dependent execution was stopped.";
              item.completed_at = failedAt;
            }
          });
          this.onUpdate(next, this.items.slice());
          break;
        }
      }
    } finally {
      this.executing = false;
    }
  };

  AgentUIEventQueue.prototype.execute = async function (item) {
    item.status = "executing";
    item.started_at = new Date().toISOString();
    this.onUpdate(item, this.items.slice());
    var resolution = await resolveBusinessObjectReference(item, this.sourceAnchors || global.EVWebGLSourceAnchors);
    item.object_resolution = resolution;
    if (resolution && resolution.status === "resolved" && resolution.object_id) {
      item.payload.object_id = resolution.object_id;
      if (!item.payload.object_name && resolution.name) item.payload.object_name = resolution.name;
    }
    if (resolution && (resolution.status === "not_found" || resolution.status === "ambiguous")) {
      item.status = "failed";
      item.error_code = resolution.error_code;
      item.error = resolution.message;
      item.result = resolution;
      item.completed_at = new Date().toISOString();
      this.onUpdate(item, this.items.slice());
      return item;
    }
    if (!this.bridge || typeof this.bridge.executeUIEvents !== "function") {
      item.status = "failed";
      item.error_code = "BRIDGE_UNAVAILABLE";
      item.error = "WebGLAgentBridge.executeUIEvents is unavailable.";
      item.completed_at = new Date().toISOString();
      this.onUpdate(item, this.items.slice());
      return item;
    }
    try {
      var results = await this.bridge.executeUIEvents([toBridgeEvent(item)], {
        task_id: item.task_id,
        trace_id: item.trace_id,
        request_id: item.request_id,
        handlerTimeoutMs: this.handlerTimeoutMs
      });
      var result = results && results[0] || {};
      item.result = result;
      var domainResult = result.result && typeof result.result === "object" ? result.result : result;
      item.status = result.success === false || domainResult.success === false ? "failed" : "success";
      item.error_code = domainResult.error_code || domainResult.code && domainResult.success === false && domainResult.code || result.error_code || null;
      item.error = domainResult.error || domainResult.message && domainResult.success === false && domainResult.message || result.error || null;
    } catch (error) {
      item.status = "failed";
      item.error_code = "UI_EVENT_EXECUTION_FAILED";
      item.error = error && error.message ? error.message : String(error);
    }
    item.completed_at = new Date().toISOString();
    this.onUpdate(item, this.items.slice());
    return item;
  };

  AgentUIEventQueue.prototype.cancel = function () {
    this.canceled = true;
    var completedAt = new Date().toISOString();
    this.items.forEach(function (item) {
      if (item.status === "queued") {
        item.status = "canceled";
        item.completed_at = completedAt;
      }
      if (item.status === "executing") item.cancel_requested = true;
    });
    this.onUpdate(null, this.items.slice());
  };

  AgentUIEventQueue.prototype.reset = function () {
    this.items = [];
    this.executing = false;
    this.canceled = false;
    this.onUpdate(null, []);
  };

  function normalizeEvent(event) {
    event = event || {};
    var source = event.source_event || event;
    var type = event.type || event.event_type || source.type || source.event_type || "unsupported_event";
    var payload = {};
    if (source.params && typeof source.params === "object") copy(payload, source.params);
    if (source.payload && typeof source.payload === "object") copy(payload, source.payload);
    if (event.payload && typeof event.payload === "object") copy(payload, event.payload);
    return {
      type: type,
      event_type: type,
      payload: payload,
      sequence: Number(event.sequence || source.sequence || 0),
      status: "pending",
      source_event: source,
      task_id: event.task_id || source.task_id || null,
      trace_id: event.trace_id || source.trace_id || null,
      request_id: event.request_id || source.request_id || null,
      event_id: event.event_id || source.event_id || null
    };
  }

  async function resolveBusinessObjectReference(item, sourceAnchors) {
    if (!requiresBusinessObjectResolution(item)) return null;
    var payload = item.payload || {};
    if (isCanonicalBusinessObjectId(payload.object_id)) {
      return {
        query: payload.object_id,
        status: "resolved",
        object_id: payload.object_id,
        name: payload.object_name || null,
        stable_id: true,
        id_scope: "business",
        source: "payload.object_id"
      };
    }
    if (payload.object_id && !payload.object_name) return null;
    var query = firstText(
      payload.object_name,
      payload.entity_name,
      payload.target_name,
      payload.business_name,
      payload.place_name,
      payload.name
    );
    if (!query) return null;
    if (!sourceAnchors || typeof sourceAnchors.findBusinessObjectsByName !== "function") {
      return {
        query: query,
        status: "not_found",
        error_code: "BUSINESS_OBJECT_RESOLVER_UNAVAILABLE",
        message: "\u4e1a\u52a1\u5bf9\u8c61\u67e5\u8be2\u80fd\u529b\u4e0d\u53ef\u7528\u3002"
      };
    }
    var matches = await Promise.resolve(sourceAnchors.findBusinessObjectsByName(query, { exact: true }));
    matches = Array.isArray(matches) ? matches : [];
    if (matches.length === 0) {
      matches = await Promise.resolve(sourceAnchors.findBusinessObjectsByName(query, { exact: false }));
      matches = Array.isArray(matches) ? matches : [];
    }
    if (matches.length === 0) {
      return {
        query: query,
        status: "not_found",
        error_code: "OBJECT_NOT_FOUND",
        message: "\u6ca1\u6709\u5728\u5f53\u524d\u573a\u666f\u4e2d\u627e\u5230\u201c" + query + "\u201d\u3002"
      };
    }
    if (matches.length > 1) {
      return {
        query: query,
        status: "ambiguous",
        error_code: "OBJECT_AMBIGUOUS",
        message: "\u627e\u5230\u591a\u4e2a\u540d\u4e3a\u201c" + query + "\u201d\u7684\u5bf9\u8c61\uff0c\u8bf7\u9009\u62e9\u4e00\u4e2a\u3002",
        candidates: matches.map(publicMatch)
      };
    }
    var match = publicMatch(matches[0]);
    if (!hasValidSpatialTarget(match) && ["fly_to_object", "gis_highlight"].indexOf(item.type) >= 0) {
      return {
        query: query,
        status: "not_found",
        error_code: "BUSINESS_OBJECT_INVALID_SPATIAL_TARGET",
        message: "\u5bf9\u8c61\u201c" + query + "\u201d\u6ca1\u6709\u53ef\u7528\u7684\u4f4d\u7f6e\u3001rectangle \u6216 bounding sphere\u3002",
        candidate: match
      };
    }
    return {
      query: query,
      status: "resolved",
      object_id: match.object_id,
      name: match.name,
      business_type: match.business_type,
      layer_name: match.layer_name,
      data_source_name: match.data_source_name,
      business_id: match.business_id,
      business_id_source: match.business_id_source,
      stable_id: match.stable_id,
      id_scope: match.id_scope || "business",
      source: "findBusinessObjectsByName"
    };
  }

  function hasValidSpatialTarget(match) {
    if (!match) return false;
    if (match.can_fly_to === true || match.position_valid === true || match.bounding_sphere_valid === true || match.runtime_bounding_sphere_valid === true) return true;
    if (match.rectangle || match.bounding_sphere || match.runtime_bounding_sphere) return true;
    if (isFinite(Number(match.longitude)) && isFinite(Number(match.latitude))) return true;
    return match.can_fly_to !== false && match.position_valid !== false && match.bounding_sphere_valid !== false;
  }

  function requiresBusinessObjectResolution(item) {
    return ["fly_to_object", "gis_highlight", "clear_highlight", "get_object_properties"].indexOf(item.type) >= 0;
  }

  function isCanonicalBusinessObjectId(value) {
    return typeof value === "string" && value.indexOf("entity:") === 0 && value.split(":").length >= 4;
  }

  function firstText() {
    for (var i = 0; i < arguments.length; i += 1) {
      if (arguments[i] !== undefined && arguments[i] !== null && String(arguments[i]).trim()) {
        return String(arguments[i]).trim();
      }
    }
    return null;
  }

  function publicMatch(match) {
    match = match || {};
    return {
      object_id: match.object_id || match.id || null,
      name: match.object_name || match.name || null,
      business_type: match.business_type || match.object_type || match.type || null,
      layer_name: match.layer_name || match.data_source_name || match.layer_id || null,
      data_source_name: match.data_source_name || null,
      business_id: match.business_id || null,
      business_id_source: match.business_id_source || null,
      stable_id: match.stable_id === true,
      id_scope: match.id_scope || null,
      can_fly_to: match.can_fly_to,
      can_highlight: match.can_highlight,
      position_valid: match.position_valid,
      bounding_sphere_valid: match.bounding_sphere_valid || match.runtime_bounding_sphere_valid,
      rectangle: match.rectangle || null
    };
  }

  function toBridgeEvent(item) {
    return {
      type: item.type,
      event_type: item.event_type,
      payload: item.payload,
      sequence: item.sequence,
      source_event: item.source_event,
      task_id: item.task_id,
      trace_id: item.trace_id,
      request_id: item.request_id,
      event_id: item.event_id
    };
  }

  function makeEventId(event) {
    return [
      event.request_id || "-",
      event.trace_id || "-",
      event.task_id || "-",
      event.type || event.event_type || "event",
      event.sequence || "0"
    ].join(":");
  }

  function copy(target, source) {
    Object.keys(source || {}).forEach(function (key) {
      target[key] = source[key];
    });
    return target;
  }

  var api = {
    AgentUIEventQueue: AgentUIEventQueue,
    normalizeEvent: normalizeEvent,
    resolveBusinessObjectReference: resolveBusinessObjectReference,
    DEFAULT_ALLOWED_EVENTS: DEFAULT_ALLOWED_EVENTS
  };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  global.AgentUIEventQueue = AgentUIEventQueue;
  global.EVAgentUIEventQueue = api;
})(typeof window !== "undefined" ? window : globalThis);
