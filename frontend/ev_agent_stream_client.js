(function (global) {
  "use strict";

  function createSSEParser(handlers) {
    handlers = handlers || {};
    var buffer = "";
    var stopped = false;

    function push(chunk) {
      if (stopped) return;
      buffer += String(chunk || "");
      buffer = buffer.replace(/\r\n/g, "\n");
      var boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        var frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        parseFrame(frame);
        if (stopped) return;
        boundary = buffer.indexOf("\n\n");
      }
    }

    function parseFrame(frame) {
      if (!frame.trim()) return;
      var eventName = "message";
      var dataLines = [];
      frame.split("\n").forEach(function (line) {
        if (line.indexOf(":") < 0) return;
        var index = line.indexOf(":");
        var field = line.slice(0, index).trim();
        var value = line.slice(index + 1);
        if (value.charAt(0) === " ") value = value.slice(1);
        if (field === "event") eventName = value || "message";
        if (field === "data") dataLines.push(value);
      });
      var rawData = dataLines.join("\n");
      var data = null;
      try {
        data = rawData ? JSON.parse(rawData) : {};
      } catch (error) {
        emit("malformed", {
          event: eventName,
          data: rawData,
          error: error && error.message ? error.message : String(error)
        });
        return;
      }
      emit(eventName, data);
      if (eventName === "done" || eventName === "error") stopped = true;
    }

    function emit(eventName, data) {
      if (typeof handlers.onEvent === "function" && handlers.onEvent({ event: eventName, data: data }) === false) return;
      var specific = handlers["on" + eventName.charAt(0).toUpperCase() + eventName.slice(1)];
      if (typeof specific === "function") specific(data);
    }

    return {
      push: push,
      finish: function () {
        if (buffer.trim()) parseFrame(buffer);
        buffer = "";
      },
      isStopped: function () {
        return stopped;
      }
    };
  }

  async function streamAgentQuery(options) {
    options = options || {};
    var endpoint = options.endpoint;
    var payload = options.payload || {};
    var controller = options.controller || new AbortController();
    var signal = options.signal || controller.signal;
    var response = null;
    var reader = null;
    var decoder = new TextDecoder("utf-8");
    var parser = createSSEParser(options);
    var streamStarted = false;

    if (!global.ReadableStream || !global.TextDecoder) {
      return fallback(options, "READABLE_STREAM_UNSUPPORTED");
    }

    try {
      response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Accept": "text/event-stream" },
        body: JSON.stringify(payload),
        signal: signal
      });
      var contentType = response.headers && response.headers.get
        ? String(response.headers.get("content-type") || "")
        : "";
      if (response.status === 404 || response.status === 405 || contentType.indexOf("text/event-stream") < 0) {
        if (response.ok && contentType.indexOf("application/json") >= 0) {
          return consumeFallbackResponse(response, options, "fallback_non_stream_same_request");
        }
        if (options.preventFallbackRequest === true) {
          throw new Error("SSE unavailable and fallback request is disabled for request_id=" + (payload.request_id || ""));
        }
        return fallback(options, "SSE_UNAVAILABLE");
      }
      if (!response.ok) {
        throw new Error("Agent stream HTTP " + response.status);
      }
      reader = response.body.getReader();
      while (true) {
        var result = await reader.read();
        if (result.done) break;
        streamStarted = true;
        parser.push(decoder.decode(result.value, { stream: true }));
        if (parser.isStopped()) break;
      }
      parser.push(decoder.decode());
      parser.finish();
      return { transport_mode: "sse", aborted: false };
    } catch (error) {
      if (signal && signal.aborted) {
        if (typeof options.onAbort === "function") options.onAbort(error);
        return { transport_mode: "sse", aborted: true };
      }
      if (typeof options.onError === "function") {
        options.onError({
          error_code: "STREAM_FAILED",
          message: error && error.message ? error.message : String(error),
          recoverable: true
        });
      }
      if (!streamStarted && options.fallbackEndpoint && options.preventFallbackRequest !== true) {
        return fallback(options, "STREAM_FAILED_BEFORE_START");
      }
      throw error;
    } finally {
      if (reader && typeof reader.releaseLock === "function") reader.releaseLock();
    }
  }

  async function fallback(options, reason) {
    if (!options.fallbackEndpoint) {
      throw new Error(reason || "SSE unavailable");
    }
    if (typeof options.onWarning === "function") {
      options.onWarning({ code: reason, message: "已切换兼容模式。", transport_mode: "fallback_non_stream" });
    }
    var payload = options.payload || {};
    if (!payload.request_id && options.requestId) payload.request_id = options.requestId;
    var response = await fetch(options.fallbackEndpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: options.signal
    });
    if (!response.ok) throw new Error("Agent fallback HTTP " + response.status);
    var data = await response.json();
    return consumeAgentResponse(data, options, "fallback_non_stream");
  }

  async function consumeFallbackResponse(response, options, mode) {
    var data = await response.json();
    return consumeAgentResponse(data, options, mode);
  }

  function consumeAgentResponse(data, options, mode) {
    if (data.metadata && data.metadata.semantic_orchestration && typeof options.onSemantic_trace === "function") {
      options.onSemantic_trace({
        request_id: data.request_id || data.trace_id,
        conversation_id: data.metadata.conversation_id,
        semantic_orchestration: data.metadata.semantic_orchestration,
        dialogue_state: data.metadata.dialogue_state,
        model_participation: data.metadata.model_participation,
        clarifications: data.metadata.clarifications || []
      });
    }
    if (typeof options.onToken === "function") options.onToken({ delta: data.answer || "", index: 1, conversation_id: data.metadata && data.metadata.conversation_id });
    if (Array.isArray(data.ui_events) && typeof options.onUIEvent === "function") {
      data.ui_events.forEach(function (event, index) {
        options.onUIEvent({
          type: event.type || event.event_type,
          payload: event.payload || event.params || {},
          sequence: index + 1,
          conversation_id: data.metadata && data.metadata.conversation_id,
          source_event: event
        });
      });
    }
    if (typeof options.onDone === "function") {
      options.onDone({
        request_id: data.request_id || data.trace_id,
        conversation_id: data.metadata && data.metadata.conversation_id,
        trace_id: data.trace_id,
        answer: data.answer || "",
        finish_reason: "fallback",
        ui_event_count: Array.isArray(data.ui_events) ? data.ui_events.length : 0,
        transport_mode: mode
      });
    }
    return { transport_mode: mode, response: data };
  }

  var api = {
    createSSEParser: createSSEParser,
    streamAgentQuery: streamAgentQuery
  };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  global.EVAgentStreamClient = api;
})(typeof window !== "undefined" ? window : globalThis);
