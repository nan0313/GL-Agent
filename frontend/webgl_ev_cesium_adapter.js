(function (global) {
  "use strict";

  var DEVELOPMENT_AGENT_BASE_URL = "http://127.0.0.1:8009";

  var ADAPTER_VERSION = "0.1.0";

  function createEVWebGLAgentAdapter(options) {
    options = options || {};
    var attached = false;
    var highlightedObjects = [];
    var spatialObjectRegistry = {};
    var objectIdAliasRegistry = {};
    var businessIdCollisionRegistry = {};
    var highlightStateRegistry = {};
    var adminBoundaryDataSources = {};
    var bufferRegistry = {};
    var bufferOrder = [];
    var nextBufferSequence = 1;
    var spatialGeometryIndex = {};
    var spatialGeometryIndexBuiltAt = null;
    var bufferQueryRegistry = {};
    var bufferQueryOrder = [];
    var bufferQueryHighlightRegistry = {};
    var nextBufferQuerySequence = 1;
    var adapter = {
      attach: attach,
      detach: detach,
      getContext: getContext,
      sendQuery: sendQuery,
      registerAllHandlers: registerAllHandlers,
      handleFlyTo: safeHandler(handleFlyTo),
      handleHighlight: safeHandler(handleHighlight),
      handleOpenPanel: safeHandler(handleOpenPanel),
      handleSetLayerVisibility: safeHandler(handleSetLayerVisibility),
      handleGetLayerList: safeHandler(handleGetLayerList),
      handleGetCameraState: safeHandler(handleGetCameraState),
      handleResetView: safeHandler(handleResetView),
      handleFlyToCoordinates: safeHandler(handleFlyToCoordinates),
      handleGetSelectedObject: safeHandler(handleGetSelectedObject),
      handleScreenshot: safeHandler(handleScreenshot),
      handleGetLayerTree: safeHandler(handleGetLayerTree),
      handleGetObjectProperties: safeHandler(handleGetObjectProperties),
      handleSearchBusinessObjects: safeHandler(handleSearchBusinessObjects),
      handleQueryNearbyObjects: safeHandler(handleQueryNearbyObjects),
      handleFlyToObject: safeHandler(handleFlyToObject),
      handleGetPanelState: safeHandler(handleGetPanelState),
      handleOpenSourcePanel: safeHandler(handleOpenSourcePanel),
      handleCloseSourcePanel: safeHandler(handleCloseSourcePanel),
      handleGetSelectionState: safeHandler(handleGetSelectionState),
      handleLocateAdminRegion: safeHandler(handleLocateAdminRegion),
      handleHighlightAdminBoundary: safeHandler(handleHighlightAdminBoundary),
      handleGetAdminRegionProperties: safeHandler(handleGetAdminRegionProperties),
      handleCreateBuffer: safeHandler(handleCreateBuffer),
      handleGetBufferState: safeHandler(handleGetBufferState),
      handleGetBufferResult: safeHandler(handleGetBufferResult),
      handleFlyToBuffer: safeHandler(handleFlyToBuffer),
      handleClearBuffer: safeHandler(handleClearBuffer),
      handleClearAllBuffers: safeHandler(handleClearAllBuffers),
      handleQueryObjectsInBuffer: safeHandler(handleQueryObjectsInBuffer),
      handleHighlightBufferQueryResults: safeHandler(handleHighlightBufferQueryResults),
      handleClearBufferQueryHighlight: safeHandler(handleClearBufferQueryHighlight),
      getCameraState: getCameraState,
      flyToCoordinates: flyToCoordinatesFromPayload,
      flyToObject: flyToObject,
      cancelCurrentFlight: cancelCurrentFlight,
      resetView: resetView,
      screenshot: screenshot,
      getLayerList: getLayerList,
      getLayerTree: getLayerTree,
      debugLayers: debugLayers,
      setLayerVisibility: setLayerVisibility,
      getSelectedObject: getSelectedObject,
      getSelectedObjectProperties: getSelectedObjectProperties,
      debugSelectedObjectProperties: debugSelectedObjectProperties,
      getSelectionState: getSelectionState,
      debugSelectionState: debugSelectionState,
      getPanelState: getPanelState,
      debugPanels: debugPanels,
      openSourcePanel: openSourcePanel,
      closeSourcePanel: closeSourcePanel,
      getSpatialObjects: getSpatialObjects,
      debugSpatialObjects: debugSpatialObjects,
      debugBusinessObjects: debugBusinessObjects,
      debugBusinessObject: debugBusinessObject,
      debugBusinessIdAliases: debugBusinessIdAliases,
      findBusinessObjectsByName: findBusinessObjectsByName,
      searchBusinessObjects: searchBusinessObjects,
      queryNearbyObjects: queryNearbyObjects,
      locateAdminRegion: locateAdminRegion,
      highlightAdminBoundary: highlightAdminBoundary,
      getAdminRegionProperties: getAdminRegionProperties,
      createBuffer: createBuffer,
      getBufferState: getBufferState,
      getBufferResult: getBufferResult,
      flyToBuffer: flyToBuffer,
      clearBuffer: clearBuffer,
      clearAllBuffers: clearAllBuffers,
      rebuildSpatialObjectGeometryIndex: rebuildSpatialObjectGeometryIndex,
      getSpatialObjectGeometryIndex: getSpatialObjectGeometryIndex,
      queryObjectsInBuffer: queryObjectsInBuffer,
      handleClearHighlight: safeHandler(handleClearHighlight),
      handleShowMessage: safeHandler(handleShowMessage),
      version: ADAPTER_VERSION
    };

    function attach() {
      var bridge = getBridge();
      if (!bridge) return adapter;
      global.EVWebGLAgentAdapter = adapter;
      global.AgentAdapter = adapter;
      ensureSpatialSourceAnchor();
      ensurePortableBaseImagery();
      bridge.configure({
        agentBaseUrl: options.agentBaseUrl || resolveAgentBaseUrl(),
        getContext: getContext,
        onResponse: function (response, requestPayload) {
          if (options.debug) {
            logDebug("agent response", {
              request: sanitizeRequest(requestPayload),
              response: response,
              ui_events: response && response.ui_events
            });
          }
          if (typeof options.onResponse === "function") {
            options.onResponse(response, requestPayload);
          }
        },
        onError: function (error) {
          warn("Agent bridge error", error);
          if (typeof options.onError === "function") {
            options.onError(error);
          }
        }
      });
      registerAllHandlers();
      scheduleHandlerRegistration();
      attached = true;
      return adapter;
    }

    function ensurePortableBaseImagery() {
      var viewer = getViewer();
      var Cesium = global.Cesium || {};
      var layers = viewer && viewer.imageryLayers;
      var Provider = Cesium.TileMapServiceImageryProvider;
      if (!layers || !Provider) return;
      for (var index = 0; index < Number(layers.length || 0); index += 1) {
        var existing = typeof layers.get === "function" ? layers.get(index) : layers[index];
        if (existing && existing._evAgentPortableBase) return;
      }
      var url = new URL("./thirdParty/ev/Cesium/Assets/Textures/NaturalEarthII/", global.location.href).href;
      var providerPromise;
      try {
        providerPromise = typeof Provider.fromUrl === "function"
          ? Provider.fromUrl(url)
          : Promise.resolve(new Provider({ url: url }));
      } catch (error) {
        warn("Portable base imagery initialization failed.", error);
        return;
      }
      Promise.resolve(providerPromise).then(function (provider) {
        var layer = layers.addImageryProvider(provider, 0);
        layer._evAgentPortableBase = true;
        layer.show = true;
        layer.alpha = 1;
        if (viewer.scene && typeof viewer.scene.requestRender === "function") viewer.scene.requestRender();
      }).catch(function (error) {
        warn("Portable base imagery failed to load.", error);
      });
    }

    function detach() {
      if (global.EVWebGLAgentAdapter === adapter) {
        try {
          delete global.EVWebGLAgentAdapter;
        } catch (error) {
          global.EVWebGLAgentAdapter = null;
        }
      }
      attached = false;
      return adapter;
    }

    function registerAllHandlers() {
      var bridge = getBridge();
      if (!bridge) return adapter;
      [
        ["show_message", adapter.handleShowMessage],
        ["ask_clarification", adapter.handleShowMessage],
        ["open_panel", adapter.handleOpenPanel],
        ["gis_highlight", adapter.handleHighlight],
        ["clear_highlight", adapter.handleClearHighlight],
        ["fly_to", adapter.handleFlyTo],
        ["fly_to_coordinates", adapter.handleFlyToCoordinates],
        ["flyToCoordinates", adapter.handleFlyToCoordinates],
        ["set_layer_visibility", adapter.handleSetLayerVisibility],
        ["setLayerVisibility", adapter.handleSetLayerVisibility],
        ["get_layer_list", adapter.handleGetLayerList],
        ["getLayerList", adapter.handleGetLayerList],
        ["get_layer_tree", adapter.handleGetLayerTree],
        ["getLayerTree", adapter.handleGetLayerTree],
        ["get_camera_state", adapter.handleGetCameraState],
        ["getCameraState", adapter.handleGetCameraState],
        ["reset_view", adapter.handleResetView],
        ["resetView", adapter.handleResetView],
        ["get_selected_object", adapter.handleGetSelectedObject],
        ["getSelectedObject", adapter.handleGetSelectedObject],
        ["get_object_properties", adapter.handleGetObjectProperties],
        ["getObjectProperties", adapter.handleGetObjectProperties],
        ["search_business_objects", adapter.handleSearchBusinessObjects],
        ["searchBusinessObjects", adapter.handleSearchBusinessObjects],
        ["query_nearby_objects", adapter.handleQueryNearbyObjects],
        ["queryNearbyObjects", adapter.handleQueryNearbyObjects],
        ["fly_to_object", adapter.handleFlyToObject],
        ["flyToObject", adapter.handleFlyToObject],
        ["get_panel_state", adapter.handleGetPanelState],
        ["getPanelState", adapter.handleGetPanelState],
        ["open_source_panel", adapter.handleOpenSourcePanel],
        ["openSourcePanel", adapter.handleOpenSourcePanel],
        ["close_source_panel", adapter.handleCloseSourcePanel],
        ["closeSourcePanel", adapter.handleCloseSourcePanel],
        ["get_selection_state", adapter.handleGetSelectionState],
        ["getSelectionState", adapter.handleGetSelectionState],
        ["locate_admin_region", adapter.handleLocateAdminRegion],
        ["locateAdminRegion", adapter.handleLocateAdminRegion],
        ["highlight_admin_boundary", adapter.handleHighlightAdminBoundary],
        ["highlightAdminBoundary", adapter.handleHighlightAdminBoundary],
        ["get_admin_region_properties", adapter.handleGetAdminRegionProperties],
        ["getAdminRegionProperties", adapter.handleGetAdminRegionProperties],
        ["create_buffer", adapter.handleCreateBuffer],
        ["createBuffer", adapter.handleCreateBuffer],
        ["get_buffer_state", adapter.handleGetBufferState],
        ["get_buffer_result", adapter.handleGetBufferResult],
        ["fly_to_buffer", adapter.handleFlyToBuffer],
        ["clear_buffer", adapter.handleClearBuffer],
        ["clear_all_buffers", adapter.handleClearAllBuffers],
        ["query_objects_in_buffer", adapter.handleQueryObjectsInBuffer],
        ["highlight_buffer_query_results", adapter.handleHighlightBufferQueryResults],
        ["clear_buffer_query_highlight", adapter.handleClearBufferQueryHighlight],
        ["screenshot", adapter.handleScreenshot]
      ].forEach(function (entry) {
        bridge.registerHandler(entry[0], entry[1]);
      });
      if (global.console && console.log) {
        console.log("[WebGLAgentAdapter] registered WebGL handlers:", [
          "show_message",
          "open_panel",
          "get_camera_state",
          "fly_to_coordinates",
          "reset_view",
          "screenshot",
          "get_layer_list",
          "set_layer_visibility",
          "get_selected_object",
          "get_layer_tree",
          "get_object_properties",
          "search_business_objects",
          "query_nearby_objects",
          "fly_to_object",
          "get_panel_state",
          "open_source_panel",
          "close_source_panel",
          "get_selection_state"
          ,"locate_admin_region"
          ,"highlight_admin_boundary"
          ,"get_admin_region_properties"
          ,"create_buffer"
          ,"get_buffer_state"
          ,"get_buffer_result"
          ,"fly_to_buffer"
          ,"clear_buffer"
          ,"clear_all_buffers"
          ,"query_objects_in_buffer"
          ,"highlight_buffer_query_results"
          ,"clear_buffer_query_highlight"
        ].join(", "));
      }
      if (typeof bridge.logRegisteredHandlers === "function") {
        bridge.logRegisteredHandlers();
      }
      return adapter;
    }

    function scheduleHandlerRegistration() {
      [0, 250, 1000, 3000].forEach(function (delayMs) {
        setTimeout(function () {
          registerAllHandlers();
        }, delayMs);
      });
    }

    function sendQuery(query, extraContext) {
      var bridge = getBridge();
      if (!bridge) {
        return Promise.reject(new Error("window.WebGLAgentBridge is required before sending query."));
      }
      if (options.debug) {
        logDebug("send query", { query: query, context: getContext(), extraContext: extraContext || {} });
      }
      return Promise.resolve()
        .then(function () {
          return bridge.sendQuery(query, extraContext || {});
        })
        .then(function (response) {
          if (options.debug) {
            logDebug("send query response", { response: response, ui_events: response && response.ui_events });
          }
          return response;
        })
        .catch(function (error) {
          warn("sendQuery failed", error);
          if (typeof options.onError === "function") {
            options.onError(error);
          }
          throw error;
        });
    }

    function getContext() {
      var selectedObject = normalizeSelectedObject(readSelectedObject());
      var mapState = readMapState();
      var spatialObjects = getSpatialObjects();
      return {
        selected_object: selectedObject,
        spatial_objects: spatialObjects,
        map_state: mapState,
        map_center: mapState.center || null,
        zoom: mapState.zoom || null,
        active_layers: readActiveLayers(),
        page_state: {
          current_panel: readStoreValue("currentPanel", options) || null,
          selected_tool: readStoreValue("selectedTool", options) || null
        },
        frontend_metadata: {
          webgl_version: readWebGLVersion(),
          project_id: options.projectId || readStoreValue("projectId", options) || null,
          scene_id: options.sceneId || readStoreValue("sceneId", options) || null,
          adapter_version: ADAPTER_VERSION
        }
      };
    }

    function handleShowMessage(payload) {
      payload = unwrapPayload(payload);
      if (typeof options.showMessage === "function") {
        options.showMessage(payload);
        return;
      }
      if (global.console && console.log) {
        console.log("[Agent]", payload.message || payload);
      }
    }

    function handleOpenPanel(payload) {
      payload = unwrapPayload(payload);
      if (typeof options.openPanel === "function") {
        options.openPanel(payload);
        return { success: true, action: "open_panel", payload: payload };
      }
      if (options.eventBus && typeof options.eventBus.emit === "function") {
        options.eventBus.emit("agent:open-panel", payload);
        return { success: true, action: "open_panel", payload: payload };
      }
      if (options.eventBus && typeof options.eventBus.$emit === "function") {
        options.eventBus.$emit("agent:open-panel", payload);
        return { success: true, action: "open_panel", payload: payload };
      }
      global.dispatchEvent(new CustomEvent("agent:open-panel", { detail: payload }));
      return { success: true, action: "open_panel", payload: payload };
    }

    function handleHighlight(payload) {
      payload = unwrapPayload(payload);
      var objectIds = ensureArray(payload.object_ids).filter(Boolean);
      if (objectIds.length) {
        var results = objectIds.map(function (objectId) {
          return highlightOne(mergeObjects(payload, { object_id: objectId }));
        });
        return { success: results.every(function (item) { return item && item.success !== false; }), results: results };
      }
      return highlightOne(payload);
    }

    function highlightOne(payload) {
      payload = payload || {};
      var objectId = payload.object_id || payload.id;
      if (typeof options.highlightObject === "function") {
        var highlighted = options.highlightObject(payload);
        if (highlighted) highlightedObjects.push(highlighted);
        return { success: !!highlighted, found: !!highlighted, object_id: objectId || null, highlight_method: "options.highlightObject", highlighted: !!highlighted };
      }
      if (options.selector && typeof options.selector.highlight === "function") {
        options.selector.highlight(objectId || payload);
        highlightedObjects.push({ type: "selector", object_id: objectId });
        return { success: true, found: true, object_id: objectId || null, highlight_method: "selector.highlight", highlighted: true };
      }
      var resolved = resolveSpatialObject(objectId);
      var entity = resolved && resolved.target || resolveObject(objectId);
      if (entity && isCesiumEntityLike(entity)) {
        return highlightResolvedEntity(resolved || registerResolvedObject(entity, "runtime_object", "runtime_object:" + String(objectId || entity.id || ""), null), payload.style || {});
      }
      warn("No highlight handler is available.", payload);
      return { success: false, found: false, object_id: objectId || null, error_code: "OBJECT_NOT_FOUND", error: "No highlightable object was found." };
    }

    function handleClearHighlight(payload) {
      payload = unwrapPayload(payload);
      removeMapHighlightOverlay();
      if (typeof options.clearHighlight === "function") {
        options.clearHighlight(payload);
        highlightedObjects = [];
        highlightStateRegistry = {};
        return { success: true, highlight_cleared: true, method: "options.clearHighlight" };
      }
      if (options.selector && typeof options.selector.clear === "function") {
        options.selector.clear();
        highlightedObjects = [];
        highlightStateRegistry = {};
        return { success: true, highlight_cleared: true, method: "selector.clear" };
      }
      if (payload && (payload.object_id || payload.id)) {
        if (restoreAdminBoundaryHighlight(payload.object_id || payload.id)) {
          return { success: true, restored_count: 1, highlight_cleared: true, method: "admin_boundary_style_restore" };
        }
        return clearHighlightForObject(payload.object_id || payload.id);
      }
      var results = Object.keys(highlightStateRegistry).map(function (objectId) {
        return clearHighlightForObject(objectId);
      });
      highlightedObjects.forEach(function (item) {
        if (item && item.type !== "selector") tryRestoreHighlight(item);
      });
      highlightedObjects = [];
      var adminRestored = Object.keys(adminBoundaryDataSources).filter(restoreAdminBoundaryHighlight).length;
      return { success: true, restored_count: results.filter(function (item) { return item && item.restored; }).length + adminRestored, highlight_cleared: true, results: results };
    }

    function restoreAdminBoundaryHighlight(regionId) {
      var dataSource = adminBoundaryDataSources[regionId];
      if (!dataSource) return false;
      var restored = false;
      ensureArray(dataSource.entities && dataSource.entities.values).forEach(function (entity) {
        var original = entity && entity._agentAdminOriginalStyle;
        if (!entity || !entity.polygon || !original) return;
        entity.polygon.material = original.material;
        entity.polygon.outline = original.outline;
        entity.polygon.outlineColor = original.outlineColor;
        restored = true;
      });
      return restored;
    }

    function highlightResolvedEntity(resolved, style) {
      if (!resolved || !resolved.target) {
        return { success: false, found: false, error_code: "OBJECT_NOT_FOUND", error: "No highlightable entity was found." };
      }
      var entity = resolved.target;
      var objectId = resolved.object_id;
      if (highlightStateRegistry[objectId]) {
        return {
          success: true,
          action: "gis_highlight",
          completed: true,
          found: true,
          object_id: objectId,
          object_type: resolved.object_type || "entity",
          source: resolved.source || null,
          highlight_method: highlightStateRegistry[objectId].method,
          highlighted: true,
          original_style_saved: true,
          idempotent: true
        };
      }
      var applied = applyEntityHighlight(entity, style || {});
      if (!applied.success) {
        return mergeObjects(applied, {
          found: true,
          object_id: objectId,
          object_type: resolved.object_type || "entity",
          source: resolved.source || null
        });
      }
      highlightStateRegistry[objectId] = {
        object_id: objectId,
        entity: entity,
        method: applied.highlight_method,
        original: applied.original
      };
      highlightedObjects.push(entity);
      return {
        success: true,
        action: "gis_highlight",
        completed: true,
        found: true,
        object_id: objectId,
        runtime_id: resolved.runtime_id || entity.id || null,
        business_id: resolved.business_id || null,
        business_id_source: resolved.business_id_source || null,
        object_type: resolved.object_type || "entity",
        source: resolved.source || null,
        source_ref: resolved.source_ref || null,
        highlight_method: applied.highlight_method,
        highlighted: true,
        original_style_saved: true
      };
    }

    function applyEntityHighlight(entity, style) {
      if (!entity) return { success: false, error_code: "OBJECT_NOT_FOUND" };
      if (entity.polygon) {
        var original = {
          material: entity.polygon.material,
          outline: entity.polygon.outline,
          outlineColor: entity.polygon.outlineColor,
          height: entity.polygon.height,
          extrudedHeight: entity.polygon.extrudedHeight,
          show: entity.show
        };
        entity.polygon.material = style.material || style.color || { evAgentHighlight: true, color: "rgba(255,215,0,0.45)" };
        entity.polygon.outline = true;
        entity.polygon.outlineColor = style.outlineColor || { evAgentHighlight: true, color: "#00ffff" };
        if (entity.show === false) entity.show = true;
        return { success: true, highlight_method: "entity.polygon.material", original: original };
      }
      if (tryHighlightEntity(entity, style)) {
        return { success: true, highlight_method: "entity.graphics.style", original: entity._agentOriginalStyle || {} };
      }
      return { success: false, error_code: "UNSUPPORTED_HIGHLIGHT_TARGET", error: "Entity has no supported highlight graphics." };
    }

    function clearHighlightForObject(objectId) {
      var resolved = resolveSpatialObject(objectId);
      var canonicalId = resolved && resolved.object_id || canonicalObjectId(objectId) || String(objectId || "");
      var state = highlightStateRegistry[canonicalId];
      if (!state) {
        return {
          success: true,
          action: "clear_highlight",
          completed: true,
          object_id: canonicalId || null,
          restored: false,
          highlight_cleared: true,
          reason: "NOT_CURRENTLY_HIGHLIGHTED"
        };
      }
      restoreEntityHighlight(state.entity, state.original, state.method);
      delete highlightStateRegistry[canonicalId];
      return {
        success: true,
        action: "clear_highlight",
        completed: true,
        object_id: canonicalId,
        restored: true,
        highlight_cleared: true
      };
    }

    function restoreEntityHighlight(entity, original, method) {
      if (!entity || !original) return;
      if (method === "entity.polygon.material" && entity.polygon) {
        entity.polygon.material = original.material;
        entity.polygon.outline = original.outline;
        entity.polygon.outlineColor = original.outlineColor;
        entity.polygon.height = original.height;
        entity.polygon.extrudedHeight = original.extrudedHeight;
        entity.show = original.show;
        return;
      }
      tryRestoreHighlight(entity);
    }

    async function handleFlyTo(payload) {
      payload = unwrapPayload(payload);
      var viewer = options.viewer;
      if (!viewer || !viewer.camera) {
        return failureResult("VIEWER_UNAVAILABLE", "WebGL viewer is not available for fly_to.", null);
      }
      if (payload.coordinates) {
        return await flyToCoordinates(payload.coordinates, payload);
      }
      if (payload.object_id) {
        return await flyToObject(payload);
      }
      return failureResult("TARGET_NOT_FOUND", "Cannot locate target for fly_to.", null);
    }

    function cancelCurrentFlight() {
      var viewer = getViewer();
      var camera = viewer && viewer.camera;
      if (camera && typeof camera.cancelFlight === "function") {
        camera.cancelFlight();
        return { success: true, action: "cancel_flight", canceled: true, method: "viewer.camera.cancelFlight" };
      }
      return { success: false, action: "cancel_flight", canceled: false, error_code: "CANCEL_FLIGHT_UNAVAILABLE", error: "viewer.camera.cancelFlight is unavailable." };
    }

    function handleSetLayerVisibility(payload) {
      payload = unwrapPayload(payload);
      agentLog("call source anchor:", payload.source_entry || "window.LayerManager/viewer layer collections");
      if (setLayerVisibility(payload)) return;
      warnSourceFallback("Layer was not found for set_layer_visibility.", payload);
      handleOpenPanel({
        panel_type: "set_layer_visibility_result",
        title: "set_layer_visibility / fallback",
        payload: { fallback_used: true, reason: "layer not found", request: payload }
      });
    }

    function handleGetLayerList(payload) {
      payload = unwrapPayload(payload);
      agentLog("call source anchor:", payload.source_entry || "window.LayerManager");
      var layers = getLayerList();
      handleOpenPanel({
        panel_type: "webgl_layer_list",
        title: "WebGL layer list / source probe",
        payload: {
          source_entry: payload.source_entry,
          fallback_used: layers.fallback_used,
          layers: layers.items
        }
      });
    }

    function handleGetLayerTree(payload) {
      payload = unwrapPayload(payload);
      agentLog("call source anchor:", payload.source_entry || "window.LayerManager/viewer layer tree");
      return queryLayerTree(payload);
    }

    function handleGetCameraState(payload) {
      payload = unwrapPayload(payload);
      agentLog("call source anchor:", payload.source_entry || "window.viewer.camera");
      return getCameraState();
    }

    function handleResetView(payload) {
      payload = unwrapPayload(payload);
      agentLog("call source anchor:", payload.source_entry || "window.viewer.camera.flyTo");
      if (resetView(payload)) return;
      warnSourceFallback("Cannot reset view because camera flyTo is unavailable.", payload);
    }

    function handleFlyToCoordinates(payload) {
      payload = unwrapPayload(payload);
      agentLog("call source anchor:", payload.source_entry || "window.viewer.camera.flyTo");
      return flyToCoordinatesFromPayload(payload);
    }

    function handleGetSelectedObject(payload) {
      payload = unwrapPayload(payload);
      agentLog("call source anchor:", payload.source_entry || "bootstrap getSelectedObject/currentSelectedObject");
      var selected = getSelectedObject();
      return selected ? mergeObjects(selected, {
        success: true,
        found: true,
        selection_source: selected.source || "current_webgl_selection",
        matched_by: "selection_state"
      }) : {
        success: false,
        found: false,
        error_code: getViewer() ? "NO_OBJECT_SELECTED" : "VIEWER_NOT_READY",
        error: getViewer() ? "No object is currently selected." : "WebGL viewer is not ready."
      };
    }

    function handleScreenshot(payload) {
      payload = unwrapPayload(payload);
      agentLog("call source anchor:", payload.source_entry || "window.globeScene.screenshot()");
      var result = screenshot(payload);
      if (result && typeof result.then === "function") {
        return result.then(function (value) { return normalizeScreenshotResult(value, payload); });
      }
      return normalizeScreenshotResult(result, payload);
    }

    function normalizeScreenshotResult(result, payload) {
      if (result && typeof result === "object") return result;
      if (result) {
        return {
          success: true,
          completed: true,
          action: "screenshot",
          file_name: screenshotFileName(payload)
        };
      }
      warnSourceFallback("No screenshot source anchor is available.", payload);
      return {
        success: false,
        completed: false,
        error_code: "SCREENSHOT_UNAVAILABLE",
        error: "The current WebGL canvas cannot be captured."
      };
    }

    function handleGetObjectProperties(payload) {
      payload = unwrapPayload(payload);
      agentLog("call source anchor:", payload.source_entry || "selected_object/getPropertyNames");
      var result = getSelectedObjectProperties(payload);
      if (result && result.found) {
        result.selection_source = payload.selection_source || result.selection_source || (payload.object_id ? null : "current_webgl_selection");
        result.matched_by = payload.matched_by || (payload.object_id ? "object_id" : "selection_state");
      }
      return result;
    }

    function handleFlyToObject(payload) {
      payload = unwrapPayload(payload);
      agentLog("call source anchor:", payload.source_entry || "viewer.flyTo/viewBoundingSphere");
      return flyToObject(payload);
    }

    function handleGetPanelState(payload) {
      payload = unwrapPayload(payload);
      agentLog("call source anchor:", payload.source_entry || "ApplicationVue menu panel DOM/source state");
      handleOpenPanel({
        panel_type: "webgl_panel_state",
        title: "WebGL panel state / source probe",
        payload: getPanelState()
      });
    }

    function handleOpenSourcePanel(payload) {
      payload = unwrapPayload(payload);
      agentLog("call source anchor:", payload.source_entry || "ApplicationVue panel source anchor");
      if (openSourcePanel(payload)) return;
      warnSourceFallback("No safe source panel opener is exposed for this target.", payload);
      handleOpenPanel({
        panel_type: "source_panel_result",
        title: "open_source_panel / partial",
        payload: {
          status: "partial",
          reason: "source panel state is inside Vue bundle closure or target is not exposed",
          request: payload,
          panels: getPanelState()
        }
      });
    }

    function handleCloseSourcePanel(payload) {
      payload = unwrapPayload(payload);
      agentLog("call source anchor:", payload.source_entry || "ApplicationVue panel source anchor");
      if (closeSourcePanel(payload)) return;
      warnSourceFallback("No safe source panel closer is exposed for this target.", payload);
    }

    function handleGetSelectionState(payload) {
      payload = unwrapPayload(payload);
      agentLog("call source anchor:", payload.source_entry || "EVPickTool/selectedEntity selected object state");
      var result = getSelectionState();
      if (!getViewer()) return { success: false, found: false, error_code: "VIEWER_NOT_READY", error: "WebGL viewer is not ready." };
      if (!result.has_selected_object) return mergeObjects(result, { success: false, found: false, error_code: "NO_OBJECT_SELECTED", error: "No object is currently selected." });
      return mergeObjects(result, { success: true, found: true, matched_by: "selection_state", selection_source: result.source });
    }

    function getSourceAnchors() {
      return global.EVWebGLSourceAnchors || {};
    }

    function ensureSpatialSourceAnchor() {
      global.EVWebGLSourceAnchors = global.EVWebGLSourceAnchors || {};
      if (
        typeof global.EVWebGLSourceAnchors.getSpatialObjects !== "function" ||
        global.EVWebGLSourceAnchors.getSpatialObjects.__evAgentFallback
      ) {
        global.EVWebGLSourceAnchors.getSpatialObjects = collectSpatialObjectsPayload;
        global.EVWebGLSourceAnchors.getSpatialObjects.__evAgentFallback = true;
      }
      if (
        typeof global.EVWebGLSourceAnchors.getLayerTree !== "function" ||
        global.EVWebGLSourceAnchors.getLayerTree.__evAgentFallback
      ) {
        global.EVWebGLSourceAnchors.getLayerTree = getLayerTree;
        global.EVWebGLSourceAnchors.getLayerTree.__evAgentFallback = true;
      }
      if (
        typeof global.EVWebGLSourceAnchors.debugLayers !== "function" ||
        global.EVWebGLSourceAnchors.debugLayers.__evAgentFallback
      ) {
        global.EVWebGLSourceAnchors.debugLayers = debugLayers;
        global.EVWebGLSourceAnchors.debugLayers.__evAgentFallback = true;
      }
      if (
        typeof global.EVWebGLSourceAnchors.getSelectedObjectProperties !== "function" ||
        global.EVWebGLSourceAnchors.getSelectedObjectProperties.__evAgentFallback
      ) {
        global.EVWebGLSourceAnchors.getSelectedObjectProperties = getSelectedObjectProperties;
        global.EVWebGLSourceAnchors.getSelectedObjectProperties.__evAgentFallback = true;
      }
      if (
        typeof global.EVWebGLSourceAnchors.debugSelectedObjectProperties !== "function" ||
        global.EVWebGLSourceAnchors.debugSelectedObjectProperties.__evAgentFallback
      ) {
        global.EVWebGLSourceAnchors.debugSelectedObjectProperties = debugSelectedObjectProperties;
        global.EVWebGLSourceAnchors.debugSelectedObjectProperties.__evAgentFallback = true;
      }
      if (
        typeof global.EVWebGLSourceAnchors.getSelectionState !== "function" ||
        global.EVWebGLSourceAnchors.getSelectionState.__evAgentFallback
      ) {
        global.EVWebGLSourceAnchors.getSelectionState = getSelectionState;
        global.EVWebGLSourceAnchors.getSelectionState.__evAgentFallback = true;
      }
      if (
        typeof global.EVWebGLSourceAnchors.debugSelectionState !== "function" ||
        global.EVWebGLSourceAnchors.debugSelectionState.__evAgentFallback
      ) {
        global.EVWebGLSourceAnchors.debugSelectionState = debugSelectionState;
        global.EVWebGLSourceAnchors.debugSelectionState.__evAgentFallback = true;
      }
      if (
        typeof global.EVWebGLSourceAnchors.getPanelState !== "function" ||
        global.EVWebGLSourceAnchors.getPanelState.__evAgentFallback
      ) {
        global.EVWebGLSourceAnchors.getPanelState = getPanelState;
        global.EVWebGLSourceAnchors.getPanelState.__evAgentFallback = true;
      }
      if (
        typeof global.EVWebGLSourceAnchors.debugPanels !== "function" ||
        global.EVWebGLSourceAnchors.debugPanels.__evAgentFallback
      ) {
        global.EVWebGLSourceAnchors.debugPanels = debugPanels;
        global.EVWebGLSourceAnchors.debugPanels.__evAgentFallback = true;
      }
      if (
        typeof global.EVWebGLSourceAnchors.openSourcePanel !== "function" ||
        global.EVWebGLSourceAnchors.openSourcePanel.__evAgentFallback
      ) {
        global.EVWebGLSourceAnchors.openSourcePanel = openSourcePanel;
        global.EVWebGLSourceAnchors.openSourcePanel.__evAgentFallback = true;
      }
      if (
        typeof global.EVWebGLSourceAnchors.closeSourcePanel !== "function" ||
        global.EVWebGLSourceAnchors.closeSourcePanel.__evAgentFallback
      ) {
        global.EVWebGLSourceAnchors.closeSourcePanel = closeSourcePanel;
        global.EVWebGLSourceAnchors.closeSourcePanel.__evAgentFallback = true;
      }
      if (
        typeof global.EVWebGLSourceAnchors.flyToObject !== "function" ||
        global.EVWebGLSourceAnchors.flyToObject.__evAgentFallback
      ) {
        global.EVWebGLSourceAnchors.flyToObject = flyToObject;
        global.EVWebGLSourceAnchors.flyToObject.__evAgentFallback = true;
      }
      if (
        typeof global.EVWebGLSourceAnchors.debugSpatialObjects !== "function" ||
        global.EVWebGLSourceAnchors.debugSpatialObjects.__evAgentFallback
      ) {
        global.EVWebGLSourceAnchors.debugSpatialObjects = debugSpatialObjects;
        global.EVWebGLSourceAnchors.debugSpatialObjects.__evAgentFallback = true;
      }
      if (typeof global.EVWebGLSourceAnchors.debugBusinessObjects !== "function") {
        global.EVWebGLSourceAnchors.debugBusinessObjects = debugBusinessObjects;
      }
      if (typeof global.EVWebGLSourceAnchors.debugBusinessObject !== "function") {
        global.EVWebGLSourceAnchors.debugBusinessObject = debugBusinessObject;
      }
      if (typeof global.EVWebGLSourceAnchors.debugBusinessIdAliases !== "function") {
        global.EVWebGLSourceAnchors.debugBusinessIdAliases = debugBusinessIdAliases;
      }
      if (typeof global.EVWebGLSourceAnchors.findBusinessObjectsByName !== "function") {
        global.EVWebGLSourceAnchors.findBusinessObjectsByName = findBusinessObjectsByName;
      }
    }

    function callSourceAnchor(name, payload) {
      var anchors = getSourceAnchors();
      if (anchors && typeof anchors[name] === "function") {
        try {
          agentLog("call source anchor: " + name, payload || "");
          return anchors[name](payload || {});
        } catch (error) {
          warnSourceFailed(name, error);
          return undefined;
        }
      }
      return undefined;
    }

    function callRealSourceAnchor(name, payload) {
      var anchors = getSourceAnchors();
      if (anchors && typeof anchors[name] === "function" && !anchors[name].__evAgentFallback) {
        try {
          agentLog("call source anchor: " + name, payload || "");
          return anchors[name](payload || {});
        } catch (error) {
          warnSourceFailed(name, error);
          return undefined;
        }
      }
      return undefined;
    }

    function getCameraState() {
      var sourceResult = callSourceAnchor("getCameraState");
      if (sourceResult !== undefined) return sourceResult;
      var mapState = readMapState();
      var viewer = getViewer();
      var camera = viewer && viewer.camera;
      var position = camera && camera.positionCartographic;
      return {
        source: "real_public_runtime",
        anchor: "window.viewer.camera",
        longitude: position ? finiteNumber(toDegrees(position.longitude, global.Cesium || {})) : null,
        latitude: position ? finiteNumber(toDegrees(position.latitude, global.Cesium || {})) : null,
        height: position ? finiteNumber(position.height) : null,
        heading: camera ? finiteNumber(toDegrees(camera.heading, global.Cesium || {})) : null,
        pitch: camera ? finiteNumber(toDegrees(camera.pitch, global.Cesium || {})) : null,
        roll: camera ? finiteNumber(toDegrees(camera.roll, global.Cesium || {})) : null,
        center: normalizeCenter(mapState.center)
      };
    }

    function flyToCoordinatesFromPayload(payload) {
      payload = payload || {};
      var sourceResult = callSourceAnchor("flyToCoordinates", payload);
      if (sourceResult !== undefined) return sourceResult !== false;
      warnSourceFallback("fly_to_coordinates -> window.viewer.camera.flyTo", payload);
      var coordinates = [
        pickNumber(payload.lon, payload.longitude, payload.lng),
        pickNumber(payload.lat, payload.latitude),
        pickNumber(payload.height, payload.altitude, 1000)
      ];
      return flyToCoordinates(coordinates, payload);
    }

    function resetView(payload) {
      payload = payload || {};
      var sourceResult = callSourceAnchor("resetView", payload);
      if (sourceResult !== undefined) return sourceResult !== false;
      warnSourceFallback("reset_view -> default view camera.flyTo", payload);
      var view = payload.default_view || {};
      var destination = view.destination || [110.684138, 33.268607, 5000000];
      var orientation = view.orientation || [0, -1.5, 0];
      var viewer = options.viewer;
      var Cesium = global.Cesium || {};
      if (!viewer || !viewer.camera || !Cesium.Cartesian3) return false;
      viewer.camera.flyTo({
        destination: Cesium.Cartesian3.fromDegrees(destination[0], destination[1], destination[2]),
        orientation: {
          heading: orientation[0],
          pitch: orientation[1],
          roll: orientation[2]
        },
        duration: Number(payload.duration_ms || 1200) / 1000
      });
      return true;
    }

    function screenshot(payload) {
      payload = payload || {};
      var sourceResult = callSourceAnchor("screenshot", payload);
      if (sourceResult !== undefined) return sourceResult;
      var viewer = getViewer();
      if (viewer && viewer.scene && viewer.scene.canvas && typeof viewer.scene.canvas.toDataURL === "function") {
        try {
          warnSourceFallback("screenshot -> viewer.scene.canvas.toDataURL", payload);
          var dataUrl = viewer.scene.canvas.toDataURL("image/png");
          var fileName = screenshotFileName(payload);
          global.dispatchEvent(new CustomEvent("agent:screenshot-ready", { detail: { file_name: fileName } }));
          return { success: true, completed: true, action: "screenshot", file_name: fileName, data_url: dataUrl, method: "viewer.scene.canvas.toDataURL" };
        } catch (error) {
          warnSourceFailed("viewer.scene.canvas.toDataURL", error);
        }
      }
      if (global.globeScene && typeof global.globeScene.screenshot === "function") {
        warnSourceFallback("screenshot -> window.globeScene.screenshot()", payload);
        var vendorResult = global.globeScene.screenshot();
        if (vendorResult !== undefined && vendorResult !== false) return vendorResult;
      }
      return false;
    }

    function screenshotFileName(payload) {
      var requested = String(payload && (payload.file_name || payload.filename) || "").trim();
      if (requested) return /\.png$/i.test(requested) ? requested : requested + ".png";
      var stamp = new Date().toISOString().replace(/[:.]/g, "-");
      return "EV-Agent-WebGL-" + stamp + ".png";
    }

    function getLayerList() {
      var treeResult = callSourceAnchor("getLayerTree");
      if (treeResult !== undefined) {
        return { items: flattenLayerTree(treeResult), fallback_used: false };
      }
      var sourceResult = callSourceAnchor("getLayerList");
      if (sourceResult !== undefined) {
        return { items: ensureArray(sourceResult), fallback_used: false };
      }
      warnSourceFallback("get_layer_list -> LayerManager/viewer collections", {});
      return collectLayerList();
    }

    function getLayerTree() {
      return collectLayerTree();
    }

    function queryLayerTree(payload) {
      payload = payload || {};
      var viewer = getViewer();
      var manager = getLayerManager();
      if (!viewer && !manager) {
        return { success: false, status: "VIEWER_NOT_READY", error_code: "VIEWER_NOT_READY", error: "WebGL viewer is not ready.", layers: [], total_count: 0 };
      }
      var sourceAvailable = !!manager || !!(viewer && (viewer.imageryLayers || viewer.dataSources));
      if (!sourceAvailable) {
        return { success: false, status: "LAYER_SOURCE_UNAVAILABLE", error_code: "LAYER_SOURCE_UNAVAILABLE", error: "Layer manager and viewer layer collections are unavailable.", layers: [], total_count: 0 };
      }
      var layers = flattenLayerTree(getLayerTree());
      var keyword = String(payload.keyword || "").trim().toLowerCase();
      layers = layers.filter(function (layer) {
        if (payload.visible_only && layer.visible === false) return false;
        if (payload.hidden_only && layer.visible !== false) return false;
        if (keyword && (String(layer.name || "") + " " + String(layer.id || "") + " " + String(layer.type || "")).toLowerCase().indexOf(keyword) < 0) return false;
        return true;
      });
      if (!layers.length && !keyword && !payload.visible_only && !payload.hidden_only) {
        return { success: false, status: "LAYER_TREE_EMPTY", error_code: "LAYER_TREE_EMPTY", error: "WebGL is ready but no layers are loaded.", layers: [], total_count: 0, source: manager ? "window.LayerManager" : "viewer collections" };
      }
      return {
        success: true,
        status: "SUCCESS",
        layers: layers,
        total_count: layers.length,
        visible_count: layers.filter(function (layer) { return layer.visible !== false; }).length,
        hidden_count: layers.filter(function (layer) { return layer.visible === false; }).length,
        source: manager ? "window.LayerManager" : "viewer collections",
        filters: { keyword: payload.keyword || null, visible_only: !!payload.visible_only, hidden_only: !!payload.hidden_only, max_depth: payload.max_depth || null }
      };
    }

    function debugLayers() {
      var layers = flattenLayerTree(getLayerTree());
      return {
        source: layers.length ? "webgl" : "fallback",
        count: layers.length,
        layers: layers.map(function (layer) {
          return {
            id: layer && layer.id,
            name: layer && layer.name,
            type: layer && layer.type,
            visible: layer && layer.visible,
            source_ref: layer && layer.source
          };
        })
      };
    }

    function setLayerVisibility(payload) {
      payload = payload || {};
      var sourceResult = callSourceAnchor("setLayerVisibility", payload);
      if (sourceResult !== undefined) return sourceResult !== false;
      warnSourceFallback("set_layer_visibility -> LayerManager/viewer collections", payload);
      if (typeof options.setLayerVisibility === "function") {
        options.setLayerVisibility(payload.layer_id, payload.visible, payload);
        return true;
      }
      if (setLayerVisibilityByManager(payload)) return true;
      var viewer = getViewer();
      if (setLayerVisibilityByCollection(viewer && viewer.dataSources, payload)) return true;
      if (setLayerVisibilityByCollection(viewer && viewer.imageryLayers, payload)) return true;
      return false;
    }

    function getSelectedObject() {
      var sourceResult = callSourceAnchor("getSelectedObject");
      if (sourceResult !== undefined) return normalizeSelectedObject(sourceResult);
      warnSourceFallback("get_selected_object -> currentSelectedObject/viewer.selectedEntity", {});
      return normalizeSelectedObject(
        global.__EV_AGENT_SELECTED_OBJECT__ ||
        global.currentSelectedObject ||
        readSelectedObject()
      );
    }

    function getSelectedObjectProperties(payload) {
      payload = payload || {};
      if (payload.object_id || payload.id) {
        return objectPropertiesResult(payload.object_id || payload.id);
      }
      var selectedObject = getSelectedObject();
      if (!selectedObject) {
        if (!getViewer()) return objectErrorResult("VIEWER_NOT_READY", "WebGL viewer is not ready.", null);
        return objectErrorResult("NO_OBJECT_SELECTED", "No selected object is available.", null);
      }
      return objectPropertiesFromSpatialObject(selectedObject, selectedObject.source || "selected_object");
    }

    function debugSelectedObjectProperties() {
      var properties = getSelectedObjectProperties();
      return {
        source: properties ? properties.source : "unavailable",
        has_selected_object: !!properties,
        object: properties
      };
    }

    function objectPropertiesResult(objectId) {
      var resolved = resolveSpatialObject(objectId);
      if (!resolved || !resolved.target) {
        return objectErrorResult("OBJECT_NOT_FOUND", "Object was not found in public WebGL runtime collections.", objectId);
      }
      return objectPropertiesFromResolved(resolved, objectId);
    }

    function objectPropertiesFromResolved(resolved, requestedId) {
      var target = resolved.target;
      var normalized = normalizeSelectedObject(target) || {};
      var properties = readProperties(target);
      var standardProperties = getStandardEntityProperties(target, resolved.graphics_types || getEntityGraphicsTypes(target));
      var position = resolved.position || readEntityPosition(target);
      if (!position.position_valid) {
        var targetPosition = coordinatesFromValue(target && target.position, "feature.metadata");
        if (targetPosition.position_valid) position = targetPosition;
      }
      if (!position.position_valid) {
        var spherePosition = coordinatesFromBoundingSphere(target && target.boundingSphere, resolved.source === "viewer.scene.primitives" ? "primitive.bounding_sphere" : "tileset.bounding_sphere", true);
        if (spherePosition.position_valid) position = spherePosition;
      }
      if (!position.position_valid) {
        var normalizedPosition = coordinatesFromValue(normalized, "feature.metadata");
        if (normalizedPosition.position_valid) position = normalizedPosition;
      }
      var objectId = pickFirst(resolved.object_id, requestedId, normalized.object_id, target && target.object_id, target && target.id, properties.object_id, properties.id, properties.EVID);
      var metadata = resolved.metadata || getEntityMetadata(target, properties, getEntityGraphicsTypes(target));
      return {
        success: true,
        action: "get_object_properties",
        completed: true,
        found: true,
        object_id: objectId ? String(objectId) : null,
        runtime_id: resolved.runtime_id || target && target.id || null,
        business_id: resolved.business_id || null,
        business_id_source: resolved.business_id_source || null,
        object_type: resolved.object_type || String(pickFirst(normalized.object_type, target && target.object_type, target && target.type, properties.object_type, properties.type, "unknown")),
        business_type: resolved.business_type || null,
        source: resolved.source || normalized.source || "runtime",
        source_ref: resolved.source_ref || null,
        data_source_name: resolved.data_source_name || null,
        data_source_index: resolved.data_source_index !== undefined ? resolved.data_source_index : null,
        layer_id: resolved.layer_id || null,
        layer_name: resolved.layer_name || null,
        stable_id: !!resolved.stable_id,
        id_scope: resolved.id_scope || null,
        resolver: resolved.resolver || null,
        name: pickFirst(resolved.name, normalized.object_name, target && target.object_name, target && target.name, properties.object_name, properties.name, properties.NAME),
        position: {
          longitude: position.position_valid ? finiteNumber(position.longitude) : null,
          latitude: position.position_valid ? finiteNumber(position.latitude) : null,
          height: position.position_valid ? finiteNumber(position.height) : null,
          position_valid: !!position.position_valid,
          position_source: position.position_source || "unavailable",
          position_attempted_source: position.position_attempted_source || null,
          position_error: position.position_error || null,
          cartesian_valid: position.cartesian_valid !== undefined ? position.cartesian_valid : null,
          cartesian_magnitude: position.cartesian_magnitude !== undefined ? position.cartesian_magnitude : null
        },
        bounding_sphere_valid: resolved.bounding_sphere_valid === true,
        bounding_sphere_radius: resolved.bounding_sphere_radius !== undefined ? resolved.bounding_sphere_radius : null,
        graphics_types: resolved.graphics_types || getEntityGraphicsTypes(target),
        standard_properties: standardProperties,
        business_properties: properties,
        properties_deprecated: true,
        metadata: metadata,
        properties: properties
      };
    }

    function objectPropertiesFromSpatialObject(value, source) {
      var properties = value && value.properties || {};
      return {
        found: true,
        object_id: value && value.object_id ? String(value.object_id) : null,
        object_type: value && value.object_type || null,
        source: source || value && value.source || "selected_object",
        source_ref: value && value.source_ref || value && value.feature_ref || null,
        stable_id: !!(value && value.stable_id),
        id_scope: value && value.id_scope || null,
        name: value && value.object_name || null,
        position: positionForSpatialObject(value),
        bounding_sphere_valid: value && value.bounding_sphere_valid === true,
        bounding_sphere_radius: value && value.bounding_sphere_radius !== undefined ? value.bounding_sphere_radius : null,
        graphics_types: value && value.graphics_types || [],
        standard_properties: value && value.standard_properties || {},
        business_properties: properties,
        metadata: value && value.metadata || {},
        properties: properties
      };
    }

    function objectNotFoundResult(objectId, message) {
      return objectErrorResult("OBJECT_NOT_FOUND", message, objectId);
    }

    function objectErrorResult(code, message, objectId) {
      return {
        success: false,
        action: "get_object_properties",
        completed: false,
        found: false,
        object_id: objectId ? String(objectId) : null,
        object_type: null,
        source: null,
        name: null,
        position: null,
        properties: {},
        error_code: code,
        error: message || "Object was not found."
      };
    }

    function getSelectionState() {
      var sourceResult = callRealSourceAnchor("getSelectionState");
      if (sourceResult !== undefined) return sourceResult;
      var selectedObject = getSelectedObject();
      var viewer = getViewer();
      var selectedEntity = viewer && viewer.selectedEntity ? normalizeSelectedObject(viewer.selectedEntity) : null;
      var age = null;
      var anchors = getSourceAnchors();
      if (anchors && typeof anchors.getSelectedObjectAge === "function") {
        try {
          age = anchors.getSelectedObjectAge();
        } catch (error) {
          age = null;
        }
      }
      return {
        source: selectedObject ? selectedObject.source || "selected_object" : "unavailable",
        has_selected_object: !!selectedObject,
        selected_object: selectedObject,
        selected_entity: selectedEntity,
        selected_object_age_ms: age,
        sources: {
          ev_agent_selected_object: !!global.__EV_AGENT_SELECTED_OBJECT__,
          current_selected_object: !!global.currentSelectedObject,
          viewer_selected_entity: !!(viewer && viewer.selectedEntity),
          ev_pick_patch: !!global.__EV_AGENT_PICK_PATCHED__
        }
      };
    }

    function debugSelectionState() {
      return getSelectionState();
    }

    function getPanelState() {
      var sourceResult = callRealSourceAnchor("getPanelState");
      if (sourceResult !== undefined) return sourceResult;
      return collectPanelState();
    }

    function debugPanels() {
      return getPanelState();
    }

    function openSourcePanel(payload) {
      payload = payload || {};
      var sourceResult = callRealSourceAnchor("openSourcePanel", payload);
      if (sourceResult !== undefined) return sourceResult !== false;
      if (typeof options.openSourcePanel === "function") {
        return options.openSourcePanel(payload) !== false;
      }
      var target = String(payload.target || payload.panel_id || payload.panel_type || "");
      if (target === "open_panel_basic" || target === "agent_debug_panel") {
        handleOpenPanel(mergeObjects(payload, { panel_type: target || "open_panel_basic" }));
        return true;
      }
      return false;
    }

    function closeSourcePanel(payload) {
      payload = payload || {};
      var sourceResult = callRealSourceAnchor("closeSourcePanel", payload);
      if (sourceResult !== undefined) return sourceResult !== false;
      if (typeof options.closeSourcePanel === "function") {
        return options.closeSourcePanel(payload) !== false;
      }
      var target = String(payload.target || payload.panel_id || payload.panel_type || "");
      if (target === "agent_debug_panel") {
        hideElementById("ev-agent-debug-panel");
        return true;
      }
      if (target === "open_panel_basic") {
        hideElementById("ev-agent-open-panel-basic");
        return true;
      }
      return false;
    }

    function getSpatialObjects() {
      var anchors = getSourceAnchors();
      if (
        anchors &&
        typeof anchors.getSpatialObjects === "function" &&
        !anchors.getSpatialObjects.__evAgentFallback
      ) {
        try {
          agentLog("call source anchor: getSpatialObjects", "");
          return anchors.getSpatialObjects();
        } catch (error) {
          warnSourceFailed("getSpatialObjects", error);
        }
      }
      warnSourceFallback("get_spatial_objects -> viewer.entities/viewer.dataSources/current selected", {});
      return collectSpatialObjectsPayload();
    }

    function debugSpatialObjects() {
      var payload = getSpatialObjects();
      var objects = ensureArray(payload && payload.objects);
      return {
        source: payload && payload.source || "mock",
        reason: payload && payload.reason || "SceneObjectMap unavailable; scanned public Cesium collections",
        anchor: payload && payload.anchor || "viewer.entities/viewer.dataSources/current selected",
        count: objects.length,
        objects: objects.map(function (item) {
          return {
            object_id: item && item.object_id,
            runtime_id: item && item.runtime_id || null,
            business_id: item && item.business_id || null,
            business_id_source: item && item.business_id_source || null,
            object_name: item && item.object_name,
            object_type: item && item.object_type,
            business_type: item && item.business_type || null,
            layer_id: item && item.layer_id || null,
            layer_name: item && item.layer_name || null,
            layer_key: item && item.layer_key || null,
            layer_key_source: item && item.layer_key_source || null,
            longitude: item && item.longitude,
            latitude: item && item.latitude,
            height: item && item.height,
            position_valid: !!(item && item.position_valid),
            position_source: item && item.position_source || "unavailable",
            position_attempted_source: item && item.position_attempted_source || null,
            position_error: item && item.position_error || null,
            cartesian_valid: item && item.cartesian_valid !== undefined ? !!item.cartesian_valid : null,
            cartesian_magnitude: item && item.cartesian_magnitude !== undefined ? item.cartesian_magnitude : null,
            can_fly_to: !!(item && item.can_fly_to),
            can_highlight: item && item.can_highlight !== undefined ? !!item.can_highlight : null,
            fly_target_type: item && item.fly_target_type || null,
            bounding_sphere_valid: item && item.bounding_sphere_valid === true,
            bounding_sphere_radius: item && item.bounding_sphere_radius !== undefined ? item.bounding_sphere_radius : null,
            has_business_properties: !!(item && item.has_business_properties),
            spatially_valid: item && item.spatially_valid !== undefined ? !!item.spatially_valid : null,
            exclude_reason: item && item.exclude_reason || null,
            graphics_types: item && item.graphics_types || [],
            stable_id: !!(item && item.stable_id),
            id_scope: item && item.id_scope || null,
            collision_reason: item && item.collision_reason || null,
            source: item && item.source || null,
            data_source_name: item && item.data_source_name || null,
            data_source_index: item && item.data_source_index !== undefined ? item.data_source_index : null,
            source_ref: item && (item.source_ref || item.feature_ref || item.source)
          };
        })
      };
    }

    function debugBusinessObjects() {
      var payload = getSpatialObjects();
      var objects = ensureArray(payload && payload.objects);
      var layerCounts = {};
      var businessIdSources = {};
      objects.forEach(function (item) {
        var layer = item.layer_id || item.layer_name || item.data_source_name || "unknown";
        layerCounts[layer] = (layerCounts[layer] || 0) + 1;
        if (item.business_id_source) {
          businessIdSources[item.business_id_source] = (businessIdSources[item.business_id_source] || 0) + 1;
        }
      });
      return {
        total: objects.length,
        stable_business_objects: objects.filter(function (item) { return item && item.stable_id === true; }).length,
        page_session_objects: objects.filter(function (item) { return item && item.id_scope === "page_session"; }).length,
        collisions: Object.keys(businessIdCollisionRegistry).map(function (key) { return businessIdCollisionRegistry[key]; }),
        business_id_sources: businessIdSources,
        layer_counts: layerCounts,
        objects: objects
      };
    }

    function debugBusinessObject(objectId) {
      var resolved = resolveSpatialObject(objectId);
      if (!resolved) {
        return { found: false, object_id: objectId || null, aliases: debugBusinessIdAliases(objectId) };
      }
      return {
        found: true,
        object_id: resolved.object_id,
        runtime_id: resolved.runtime_id || null,
        business_id: resolved.business_id || null,
        business_id_source: resolved.business_id_source || null,
        business_type: resolved.business_type || null,
        layer_key: resolved.layer_key || null,
        layer_key_source: resolved.layer_key_source || null,
        stable_id: resolved.stable_id === true,
        id_scope: resolved.id_scope || null,
        aliases: debugBusinessIdAliases(resolved.object_id),
        supports: {
          properties: true,
          fly: resolved.can_fly_to === true,
          highlight: resolved.can_highlight === true
        },
        source: resolved.source || null,
        source_ref: resolved.source_ref || null
      };
    }

    function debugBusinessIdAliases(objectId) {
      var canonical = canonicalObjectId(objectId || "");
      var aliases = [];
      Object.keys(objectIdAliasRegistry).forEach(function (alias) {
        if (!canonical || objectIdAliasRegistry[alias] === canonical) aliases.push(alias);
      });
      return {
        input: objectId || null,
        canonical_object_id: canonical || null,
        aliases: aliases
      };
    }

    function findBusinessObjectsByName(query, options) {
      options = options || {};
      var text = String(query || "").trim();
      if (!text) return [];
      var lower = text.toLowerCase();
      var payload = getSpatialObjects();
      return ensureArray(payload && payload.objects).filter(function (item) {
        var name = String(item.object_name || item.name || item.business_properties && (item.business_properties.NAME || item.business_properties.name) || "");
        if (!name) return false;
        if (options.exact) return name === text;
        return name === text || name.indexOf(text) >= 0 || name.toLowerCase().indexOf(lower) >= 0;
      }).map(function (item) {
        return publicSpatialObject(item);
      });
    }

    function handleSearchBusinessObjects(payload) {
      return searchBusinessObjects(unwrapPayload(payload));
    }

    function searchBusinessObjects(payload) {
      payload = payload || {};
      if (!getViewer()) return { success: false, code: "VIEWER_NOT_READY", error_code: "VIEWER_NOT_READY", error: "WebGL viewer is not ready." };
      var query = String(payload.query || payload.name || "").trim();
      if (!query) return { success: false, code: "OBJECT_SEARCH_QUERY_REQUIRED", error_code: "OBJECT_SEARCH_QUERY_REQUIRED", error: "Object search query is required." };
      var matches = payload.exact_first !== false ? findBusinessObjectsByName(query, { exact: true }) : [];
      var matchedBy = matches.length ? "exact_name" : "normalized_or_substring";
      if (!matches.length) matches = findBusinessObjectsByName(query, { exact: false });
      var objectType = String(payload.object_type || "").toLowerCase();
      var layerName = String(payload.layer_name || "").toLowerCase();
      matches = matches.filter(function (item) {
        if (objectType && String(item.object_type || item.business_type || "").toLowerCase().indexOf(objectType) < 0) return false;
        if (layerName && String(item.layer_name || item.data_source_name || "").toLowerCase().indexOf(layerName) < 0) return false;
        return true;
      }).slice(0, Math.max(1, Number(payload.limit || 20)));
      var candidates = matches.map(function (item) {
        return {
          object_id: item.object_id || null,
          name: item.object_name || null,
          object_type: item.object_type || item.business_type || null,
          layer_name: item.layer_name || item.data_source_name || null,
          source: item.source || null,
          position_available: item.position_valid === true,
          center: item.position_valid ? { longitude: item.longitude, latitude: item.latitude, height: item.height || 0 } : null,
          properties_preview: previewProperties(item.business_properties || item.properties),
          matched_by: matchedBy
        };
      });
      if (!candidates.length) return { success: false, code: "OBJECT_NOT_FOUND", error_code: "OBJECT_NOT_FOUND", found: false, query: query, candidates: [], error: "Object was not found in runtime sources." };
      if (candidates.length > 1) return { success: false, code: "OBJECT_AMBIGUOUS", error_code: "OBJECT_AMBIGUOUS", found: true, query: query, candidates: candidates, error: "Multiple matching objects were found." };
      return { success: true, code: "OBJECT_SEARCH_SUCCESS", found: true, query: query, total_count: 1, object: candidates[0], candidates: candidates };
    }

    function handleQueryNearbyObjects(payload) {
      return queryNearbyObjects(unwrapPayload(payload));
    }

    function queryNearbyObjects(payload) {
      payload = payload || {};
      if (!getViewer()) return { success: false, code: "VIEWER_NOT_READY", error_code: "VIEWER_NOT_READY", error: "WebGL viewer is not ready." };
      var center = resolveSpatialQueryCenter(payload);
      if (!center) return { success: false, code: "SPATIAL_QUERY_CENTER_REQUIRED", error_code: "SPATIAL_QUERY_CENTER_REQUIRED", error: "Please specify a spatial query center." };
      var radius = Math.max(0, Number(payload.radius_m || payload.radius || 500));
      var category = normalizeObjectCategory(payload.category || payload.object_type);
      var objects = ensureArray(getSpatialObjects().objects).filter(function (item) {
        if (!item || item.position_valid !== true) return false;
        if (center.anchor_id && String(item.object_id) === String(center.anchor_id)) return false;
        if (category && objectCategoryText(item).indexOf(category) < 0) return false;
        return haversineMeters(center.longitude, center.latitude, Number(item.longitude), Number(item.latitude)) <= radius;
      }).slice(0, Math.max(1, Number(payload.limit || 100))).map(publicSpatialObject);
      return {
        success: true,
        code: "SUCCESS",
        radius_m: radius,
        category: payload.category || payload.object_type || null,
        center: center,
        center_source: center.source,
        anchor_name: center.anchor_name,
        large_area_notice: center.large_area === true ? "查询范围围绕该行政区几何中心计算，不代表在整个行政区范围内查询。" : null,
        total_count: objects.length,
        objects: objects
      };
    }

    function resolveSpatialQueryCenter(payload) {
      var explicitSource = payload.center && (payload.center.source || payload.center.center_source) || "explicit_coordinates";
      var explicitAccuracy = payload.center && payload.center.accuracy || "exact";
      var explicit = centerFromValue(payload.center, explicitSource, explicitAccuracy);
      if (explicit) return explicit;
      var anchor = null;
      if (payload.anchor_object_id) anchor = resolveSpatialObject(payload.anchor_object_id);
      if (!anchor && payload.anchor_name) {
        var named = findBusinessObjectsByName(payload.anchor_name, { exact: true });
        if (named.length === 1) anchor = { target: named[0], object_id: named[0].object_id };
      }
      var candidates = [
        { value: anchor && (anchor.target || anchor), source: "explicit_anchor_object" },
        { value: global.__EV_AGENT_SELECTED_OBJECT__ || global.currentSelectedObject, source: "selected_object" },
        { value: global.__EV_AGENT_CURRENT_OBJECT__, source: "last_context_object" },
        { value: global.__EV_AGENT_LAST_ADMIN_REGION__, source: "last_admin_region" },
        { value: global.__EV_AGENT_TEST_OBJECT__, source: "test_object" }
      ];
      for (var i = 0; i < candidates.length; i += 1) {
        var center = centerFromValue(candidates[i].value, candidates[i].source, "object_position_or_geometry");
        if (center) return center;
      }
      if (payload.allow_map_center === true) {
        var map = readMapState();
        var mapCenter = map && map.center;
        if (Array.isArray(mapCenter)) return centerFromValue({ longitude: mapCenter[0], latitude: mapCenter[1], name: "当前地图视图中心" }, "map_view_center", "view_center");
      }
      return null;
    }

    function centerFromValue(value, source, accuracy) {
      if (!value) return null;
      var boundingCenter = value.bounding_sphere_center || value.boundingSphereCenter ||
        value.bounding_sphere && value.bounding_sphere.center || value.boundingSphere && value.boundingSphere.center;
      var position = value.position && typeof value.position === "object" ? value.position :
        value.center && typeof value.center === "object" ? value.center :
        boundingCenter && typeof boundingCenter === "object" ? boundingCenter : value;
      var lon = finiteNumber(pickFirst(value.longitude, position.longitude, position.lon, position.lng));
      var lat = finiteNumber(pickFirst(value.latitude, position.latitude, position.lat));
      if ((lon === null || lat === null) && value.geometry && Array.isArray(value.geometry.coordinates)) {
        var centroid = coordinateCentroid(value.geometry.coordinates);
        if (centroid) {
          lon = centroid.longitude;
          lat = centroid.latitude;
          source = "geometry_centroid";
          accuracy = "derived_from_geometry";
        }
      }
      if (lon === null || lat === null) return null;
      var type = String(value.level || value.object_type || value.business_type || "");
      return {
        longitude: lon,
        latitude: lat,
        height: finiteNumber(pickFirst(value.height, position.height)) || 0,
        source: value.center_source || value.position_source || source,
        anchor_id: value.object_id || value.region_id || null,
        anchor_name: value.object_name || value.name || null,
        accuracy: accuracy,
        large_area: ["province", "city", "administrative_region"].indexOf(type) >= 0
      };
    }

    function coordinateCentroid(coordinates) {
      var points = [];
      (function collect(value) {
        if (!Array.isArray(value)) return;
        if (value.length >= 2 && isFinite(Number(value[0])) && isFinite(Number(value[1]))) {
          points.push([Number(value[0]), Number(value[1])]);
          return;
        }
        value.forEach(collect);
      })(coordinates);
      if (!points.length) return null;
      var total = points.reduce(function (sum, point) {
        sum[0] += point[0]; sum[1] += point[1]; return sum;
      }, [0, 0]);
      return { longitude: total[0] / points.length, latitude: total[1] / points.length };
    }

    function normalizeObjectCategory(value) {
      var text = String(value || "").toLowerCase();
      if (text === "馈线") return "feeder";
      if (text === "变电站") return "substation";
      if (text === "线路") return "line";
      return text;
    }

    function objectCategoryText(item) {
      item = item || {};
      var properties = item.business_properties || item.properties || {};
      return [item.object_type, item.business_type, properties.object_type, properties.objectType, properties.type, properties.category]
        .filter(function (value) { return value !== undefined && value !== null; })
        .join(" ").toLowerCase();
    }

    function haversineMeters(lon1, lat1, lon2, lat2) {
      var rad = Math.PI / 180;
      var a = Math.sin((lat2 - lat1) * rad / 2) ** 2 + Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin((lon2 - lon1) * rad / 2) ** 2;
      return 6371008.8 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    }

    function previewProperties(value) {
      var result = {};
      Object.keys(value || {}).slice(0, 12).forEach(function (key) {
        var item = value[key];
        if (item === null || ["string", "number", "boolean"].indexOf(typeof item) >= 0) result[key] = item;
      });
      return result;
    }

    function handleLocateAdminRegion(payload) {
      return locateAdminRegion(unwrapPayload(payload));
    }

    function handleHighlightAdminBoundary(payload) {
      return highlightAdminBoundary(unwrapPayload(payload));
    }

    function handleGetAdminRegionProperties(payload) {
      return getAdminRegionProperties(unwrapPayload(payload));
    }

    async function resolveAdminRegion(payload) {
      payload = payload || {};
      var viewer = getViewer();
      if (!viewer) return { success: false, code: "VIEWER_NOT_READY", error_code: "VIEWER_NOT_READY", error: "WebGL viewer is not ready." };
      var runtime = resolveRuntimeAdminRegion(payload);
      if (runtime && (runtime.success || runtime.code === "ADMIN_REGION_AMBIGUOUS")) return runtime;
      var params = [];
      ["name", "adcode", "level", "parent"].forEach(function (key) {
        if (payload[key] !== undefined && payload[key] !== null && String(payload[key]).trim()) {
          params.push(encodeURIComponent(key) + "=" + encodeURIComponent(String(payload[key]).trim()));
        }
      });
      if (!params.length && payload.region_id && String(payload.region_id).indexOf("admin:") === 0) {
        params.push("adcode=" + encodeURIComponent(String(payload.region_id).slice(6)));
      }
      var base = options.agentBaseUrl || resolveAgentBaseUrl();
      try {
        var response = await global.fetch(base + "/api/admin-regions/search?" + params.join("&"));
        if (!response.ok) throw new Error("HTTP " + response.status);
        var result = await response.json();
        if (!result.success) {
          result.error_code = result.code || "ADMIN_REGION_NOT_FOUND";
          result.error = adminRegionErrorMessage(result.error_code);
        }
        return result;
      } catch (error) {
        return { success: false, code: "ADMIN_REGION_SOURCE_UNAVAILABLE", error_code: "ADMIN_REGION_SOURCE_UNAVAILABLE", error: error && error.message || "Admin region source is unavailable." };
      }
    }

    function resolveRuntimeAdminRegion(payload) {
      var queryName = String(payload.name || "").trim();
      var queryAdcode = String(payload.adcode || "").trim();
      var queryParent = normalizeAdminName(payload.parent || "");
      var queryLevel = String(payload.level || "").trim();
      var objects = ensureArray(getSpatialObjects().objects).filter(function (item) {
        var props = item.business_properties || {};
        var name = String(item.object_name || props.NAME || props.NAME99 || props.name || "");
        var adcode = String(props.adcode || props.ADCODE || props.ADCODE99 || "");
        var parent = normalizeAdminName(props.parent_name || props.PARENT_NAME || "");
        var level = String(props.level || props.LEVEL || inferAdminLevel(name));
        if (queryAdcode && adcode !== queryAdcode) return false;
        if (queryName && normalizeAdminName(name) !== normalizeAdminName(queryName)) return false;
        if (queryParent && parent && parent !== queryParent) return false;
        if (queryLevel && level && level !== queryLevel) return false;
        return !!(queryAdcode || queryName) && (item.business_type === "administrative_region" || inferAdminLevel(name) !== "unknown");
      });
      if (!objects.length) return null;
      if (objects.length > 1) return {
        success: false,
        code: "ADMIN_REGION_AMBIGUOUS",
        error_code: "ADMIN_REGION_AMBIGUOUS",
        error: adminRegionErrorMessage("ADMIN_REGION_AMBIGUOUS"),
        candidates: objects.map(function (item) {
          var props = item.business_properties || {};
          return { name: item.object_name, adcode: props.adcode || props.ADCODE || props.ADCODE99 || null, level: props.level || inferAdminLevel(item.object_name), parent: props.parent_name || null, source: item.source };
        })
      };
      var item = objects[0];
      var properties = item.business_properties || {};
      var position = item.position || {};
      var longitude = pickFirst(item.longitude, position.longitude, properties.CENTROID_X, properties.center_x);
      var latitude = pickFirst(item.latitude, position.latitude, properties.CENTROID_Y, properties.center_y);
      var graphics = ensureArray(item.standard_properties && item.standard_properties.graphics_types);
      return {
        success: true,
        code: "SUCCESS",
        matched_by: queryAdcode ? "adcode" : "runtime_name",
        region: {
          region_id: item.object_id,
          name: item.object_name,
          aliases: [normalizeAdminName(item.object_name)],
          adcode: properties.adcode || properties.ADCODE || properties.ADCODE99 || null,
          level: properties.level || inferAdminLevel(item.object_name),
          parent_name: properties.parent_name || properties.PARENT_NAME || null,
          center: isFinite(Number(longitude)) && isFinite(Number(latitude)) ? { longitude: Number(longitude), latitude: Number(latitude), height: Number(position.height || 0) } : null,
          center_source: item.position_source || "runtime_geometry",
          boundary_object_id: item.object_id,
          boundary_available: graphics.indexOf("polygon") >= 0 || !!item.bounding_sphere_valid,
          source: item.source,
          source_confidence: "runtime_loaded_object",
          properties: properties
        }
      };
    }

    function normalizeAdminName(value) {
      var text = String(value || "").replace(/[\s·•,，的]/g, "").replace(/市市辖区$/, "市");
      ["特别行政区", "自治区", "自治州", "地区", "省", "市", "区", "县", "盟", "旗"].some(function (suffix) {
        if (text.length > suffix.length && text.slice(-suffix.length) === suffix) {
          text = text.slice(0, -suffix.length);
          return true;
        }
        return false;
      });
      return text;
    }

    function inferAdminLevel(name) {
      name = String(name || "");
      if (/省$|自治区$|特别行政区$/.test(name)) return "province";
      if (/市市辖区$|市$|自治州$|盟$/.test(name)) return "city";
      if (/区$/.test(name)) return "district";
      if (/县$|旗$/.test(name)) return "county";
      return "unknown";
    }

    async function locateAdminRegion(payload) {
      var resolved = await resolveAdminRegion(payload);
      if (!resolved.success) return resolved;
      var region = resolved.region || {};
      var center = region.center;
      if (!center || !isFinite(Number(center.longitude)) || !isFinite(Number(center.latitude))) {
        if (region.source_confidence === "runtime_loaded_object" && region.boundary_object_id) {
          var boundaryFly = await flyToObject({ object_id: region.boundary_object_id, duration: Number(payload && payload.duration || 2) });
          if (boundaryFly.success) return { success: true, code: "SUCCESS", matched_region: region, center_available: false, boundary_available: true, fly_completed: true, fly_method: boundaryFly.method, matched_by: resolved.matched_by };
        }
        return mergeObjects(resolved, { success: false, code: "ADMIN_REGION_POSITION_UNAVAILABLE", error_code: "ADMIN_REGION_POSITION_UNAVAILABLE", error: "Administrative region has no geographic center." });
      }
      var defaultHeight = region.level === "province" ? 1500000 : region.level === "city" ? 350000 : 120000;
      var targetHeight = Number(payload && payload.height || defaultHeight);
      var fly = await flyToCoordinates([Number(center.longitude), Number(center.latitude), Number(center.height || 0)], {
        duration_ms: Number(payload && payload.duration || 2) * 1000, height: targetHeight
      }, { object_id: region.region_id, object_type: region.level, source: region.source });
      return {
        success: fly.success !== false,
        code: fly.success === false ? fly.error_code : "SUCCESS",
        matched_region: region,
        center_available: true,
        boundary_available: !!region.boundary_available,
        fly_completed: !!fly.completed,
        fly_method: fly.method,
        matched_by: resolved.matched_by
      };
    }

    async function getAdminRegionProperties(payload) {
      var resolved = await resolveAdminRegion(payload);
      if (!resolved.success) return resolved;
      return {
        success: true,
        code: "SUCCESS",
        found: true,
        matched_region: resolved.region,
        properties: resolved.region.properties || {},
        matched_by: resolved.matched_by,
        selection_source: payload && payload.selection_source || "admin_region_query"
      };
    }

    async function highlightAdminBoundary(payload) {
      var resolved = await resolveAdminRegion(payload);
      if (!resolved.success) return resolved;
      var region = resolved.region || {};
      if (!region.boundary_available || !region.region_id) {
        return { success: false, code: "ADMIN_BOUNDARY_UNAVAILABLE", error_code: "ADMIN_BOUNDARY_UNAVAILABLE", matched_region: region, boundary_available: false, error: adminRegionErrorMessage("ADMIN_BOUNDARY_UNAVAILABLE") };
      }
      var viewer = getViewer();
      var Cesium = global.Cesium || {};
      if (!viewer) return { success: false, code: "VIEWER_NOT_READY", error_code: "VIEWER_NOT_READY", error: adminRegionErrorMessage("VIEWER_NOT_READY") };
      try {
        var center = region.center || {};
        if (isFinite(Number(center.longitude)) && isFinite(Number(center.latitude))) {
          var regionHeight = region.level === "province" ? 1500000 : region.level === "city" ? 350000 : 120000;
          await flyToCoordinates([Number(center.longitude), Number(center.latitude), regionHeight], { duration_ms: 900, height: regionHeight }, { object_id: region.region_id, object_type: region.level, source: region.source });
        }
        if (region.source_confidence === "runtime_loaded_object") {
          var runtimeResolved = resolveSpatialObject(region.boundary_object_id || region.region_id);
          var runtimeHighlight = highlightResolvedEntity(runtimeResolved, payload && payload.style || {});
          if (runtimeHighlight.success) {
            var runtimeOverlay = addMapHighlightOverlay(viewer.scene && viewer.scene.canvas, region);
            return { success: true, code: "SUCCESS", matched_region: region, boundary_available: true, highlight_completed: true, object_id: region.boundary_object_id, method: runtimeHighlight.highlight_method, marker_visible: !!runtimeOverlay };
          }
        }
        var dataSource = adminBoundaryDataSources[region.region_id];
        if (!dataSource) {
          var base = options.agentBaseUrl || resolveAgentBaseUrl();
          var response = await global.fetch(base + "/api/admin-regions/" + encodeURIComponent(region.region_id) + "/boundary");
          if (!response.ok) throw new Error("boundary HTTP " + response.status);
          var feature = await response.json();
          if (!Cesium.GeoJsonDataSource || typeof Cesium.GeoJsonDataSource.load !== "function") throw new Error("Cesium.GeoJsonDataSource is unavailable");
          dataSource = await Cesium.GeoJsonDataSource.load(feature, { clampToGround: false });
          if (viewer.dataSources && typeof viewer.dataSources.add === "function") await viewer.dataSources.add(dataSource);
          dataSource.__evAgentOutlineEntities = addAdminBoundaryOutlines(viewer, Cesium, feature, region.region_id);
          dataSource.__evAgentCenterMarker = addAdminRegionCenterMarker(viewer, Cesium, region);
          adminBoundaryDataSources[region.region_id] = dataSource;
        }
        ensureArray(dataSource.entities && dataSource.entities.values).forEach(function (entity) {
          if (!entity.polygon) return;
          if (!entity._agentAdminOriginalStyle) entity._agentAdminOriginalStyle = { material: entity.polygon.material, outline: entity.polygon.outline, outlineColor: entity.polygon.outlineColor };
          entity.polygon.material = Cesium.Color && Cesium.Color.YELLOW && Cesium.Color.YELLOW.withAlpha ? Cesium.Color.YELLOW.withAlpha(0.32) : "rgba(255,255,0,.32)";
          entity.polygon.outline = true;
          entity.polygon.outlineColor = Cesium.Color && Cesium.Color.YELLOW || "yellow";
          if (entity.polygon.height !== undefined) entity.polygon.height = 1500;
        });
        var outlineSphere = dataSource.__evAgentOutlineEntities && dataSource.__evAgentOutlineEntities.boundingSphere;
        if (outlineSphere && viewer.camera && typeof viewer.camera.flyToBoundingSphere === "function") {
          var outlineOffset = Cesium.HeadingPitchRange
            ? new Cesium.HeadingPitchRange(0, -Math.PI / 2, Math.max(500000, Number(outlineSphere.radius || 0) * 2.5))
            : undefined;
          await flyCameraTo(viewer.camera, "flyToBoundingSphere", outlineSphere, { duration: Number(payload && payload.duration || 1.2), offset: outlineOffset });
        } else if (typeof viewer.flyTo === "function") {
          await maybePromise(viewer.flyTo(dataSource, { duration: Number(payload && payload.duration || 1.2) }));
        }
        if (viewer.scene && typeof viewer.scene.requestRender === "function") viewer.scene.requestRender();
        var outlineCount = ensureArray(dataSource.__evAgentOutlineEntities).length;
        var polygonCount = ensureArray(dataSource.entities && dataSource.entities.values).filter(function (entity) { return !!(entity && entity.polygon); }).length;
        if (!outlineCount && !polygonCount) throw new Error("No renderable polygon was created for the administrative boundary");
        return { success: true, code: "SUCCESS", matched_region: region, boundary_available: true, highlight_completed: true, object_id: region.boundary_object_id, method: "Cesium.GeoJsonDataSource.load", polygon_count: polygonCount, outline_count: outlineCount, marker_visible: !!dataSource.__evAgentCenterMarker };
      } catch (error) {
        return { success: false, code: "ADMIN_BOUNDARY_UNAVAILABLE", error_code: "ADMIN_BOUNDARY_UNAVAILABLE", matched_region: region, boundary_available: true, highlight_completed: false, error: error && error.message || adminRegionErrorMessage("ADMIN_BOUNDARY_UNAVAILABLE") };
      }
    }

    function addAdminBoundaryOutlines(viewer, Cesium, feature, regionId) {
      if (!viewer || !viewer.scene || !viewer.scene.primitives) return [];
      if (!Cesium.Cartesian3 || typeof Cesium.Cartesian3.fromDegreesArrayHeights !== "function") return [];
      var geometry = feature && feature.geometry || {};
      var polygons = geometry.type === "Polygon" ? [geometry.coordinates] : geometry.type === "MultiPolygon" ? geometry.coordinates : [];
      var entities = [];
      var allPositions = [];
      polygons.forEach(function (polygon, polygonIndex) {
        var outer = ensureArray(polygon && polygon[0]);
        if (outer.length < 3) return;
        var values = [];
        outer.forEach(function (point) {
          values.push(Number(point[0]), Number(point[1]), 8000);
        });
        var first = outer[0];
        var last = outer[outer.length - 1];
        if (first && last && (Number(first[0]) !== Number(last[0]) || Number(first[1]) !== Number(last[1]))) {
          values.push(Number(first[0]), Number(first[1]), 8000);
        }
        var positions = Cesium.Cartesian3.fromDegreesArrayHeights(values);
        allPositions = allPositions.concat(positions);
        if (Cesium.PolylineGeometry && Cesium.GeometryInstance && Cesium.Primitive && Cesium.PolylineColorAppearance && Cesium.ColorGeometryInstanceAttribute) {
          var geometry = new Cesium.PolylineGeometry({
            positions: positions,
            width: 7,
            vertexFormat: Cesium.PolylineColorAppearance.VERTEX_FORMAT
          });
          var instance = new Cesium.GeometryInstance({
            id: "ev-admin-outline-" + String(regionId || "region") + "-" + polygonIndex,
            geometry: geometry,
            attributes: { color: Cesium.ColorGeometryInstanceAttribute.fromColor(Cesium.Color.YELLOW) }
          });
          entities.push(viewer.scene.primitives.add(new Cesium.Primitive({
            geometryInstances: instance,
            appearance: new Cesium.PolylineColorAppearance({ translucent: false }),
            asynchronous: false
          })));
        }
      });
      if (allPositions.length && Cesium.BoundingSphere && typeof Cesium.BoundingSphere.fromPoints === "function") {
        entities.boundingSphere = Cesium.BoundingSphere.fromPoints(allPositions);
      }
      if (viewer.scene && typeof viewer.scene.requestRender === "function") viewer.scene.requestRender();
      return entities;
    }

    function addAdminRegionCenterMarker(viewer, Cesium, region) {
      var center = region && region.center || {};
      if (!viewer || !viewer.entities || typeof viewer.entities.add !== "function") return null;
      if (!Cesium.Cartesian3 || typeof Cesium.Cartesian3.fromDegrees !== "function") return null;
      if (!isFinite(Number(center.longitude)) || !isFinite(Number(center.latitude))) return null;
      var entity = viewer.entities.add({
        id: "ev-admin-marker-" + String(region.region_id || Date.now()),
        name: region.name || "Administrative region",
        position: Cesium.Cartesian3.fromDegrees(Number(center.longitude), Number(center.latitude), 12000),
        point: {
          pixelSize: 20,
          color: Cesium.Color && Cesium.Color.YELLOW,
          outlineColor: Cesium.Color && Cesium.Color.BLACK,
          outlineWidth: 4,
          disableDepthTestDistance: Number.POSITIVE_INFINITY
        },
        label: {
          text: region.name || "",
          font: "600 18px sans-serif",
          fillColor: Cesium.Color && Cesium.Color.YELLOW,
          outlineColor: Cesium.Color && Cesium.Color.BLACK,
          outlineWidth: 4,
          style: Cesium.LabelStyle && Cesium.LabelStyle.FILL_AND_OUTLINE,
          pixelOffset: Cesium.Cartesian2 ? new Cesium.Cartesian2(0, -34) : undefined,
          disableDepthTestDistance: Number.POSITIVE_INFINITY
        }
      });
      var overlay = addMapHighlightOverlay(viewer.scene && viewer.scene.canvas, region);
      if (viewer.scene && typeof viewer.scene.requestRender === "function") viewer.scene.requestRender();
      return { entity: entity, overlay: overlay };
    }

    function addMapHighlightOverlay(canvas, region) {
      if (!canvas || !global.document) return null;
      removeMapHighlightOverlay();
      var overlay = document.createElement("div");
      overlay.id = "ev-agent-map-highlight";
      overlay.setAttribute("aria-label", String(region && region.name || "目标") + "已高亮");
      overlay.style.cssText = "position:fixed;z-index:99990;pointer-events:none;display:flex;flex-direction:column;align-items:center;gap:8px;transform:translate(-50%,-50%);";
      var ring = document.createElement("span");
      ring.style.cssText = "display:block;width:54px;height:54px;border:4px solid #ffd43b;border-radius:50%;box-shadow:0 0 0 8px rgba(255,212,59,.24),0 0 24px rgba(255,212,59,.9);background:rgba(255,212,59,.12);";
      var label = document.createElement("span");
      label.textContent = String(region && region.name || "目标") + " · 已高亮";
      label.style.cssText = "display:block;padding:6px 10px;border-radius:6px;background:rgba(13,27,42,.88);color:#fff7cc;border:1px solid rgba(255,212,59,.75);font:600 14px/1.2 sans-serif;white-space:nowrap;box-shadow:0 4px 14px rgba(0,0,0,.28);";
      overlay.appendChild(ring);
      overlay.appendChild(label);
      document.body.appendChild(overlay);
      var rect = canvas.getBoundingClientRect();
      overlay.style.left = String(rect.left + rect.width / 2) + "px";
      overlay.style.top = String(rect.top + rect.height / 2) + "px";
      return overlay;
    }

    function removeMapHighlightOverlay() {
      var overlay = global.document && document.getElementById("ev-agent-map-highlight");
      if (overlay) overlay.remove();
    }

    function adminRegionErrorMessage(code) {
      return {
        ADMIN_REGION_NOT_FOUND: "当前行政区数据中未找到该地区。",
        ADMIN_REGION_AMBIGUOUS: "找到多个同名行政区，请指定上级省市。",
        ADMIN_BOUNDARY_UNAVAILABLE: "可以定位，但当前数据源没有可高亮的行政区边界。",
        ADMIN_REGION_SOURCE_UNAVAILABLE: "当前行政区数据源不可用。",
        VIEWER_NOT_READY: "WebGL 地图尚未完成初始化。"
      }[code] || "行政区操作失败。";
    }

    async function flyToObject(payload) {
      payload = payload || {};
      var objectId = payload.object_id || payload.id;
      var resolved = resolveSpatialObject(objectId);
      if (resolved && resolved.target) {
        return await flyToResolvedObject(resolved.target, payload, resolved);
      }
      var selectedObject = normalizeSelectedObject(global.__EV_AGENT_SELECTED_OBJECT__ || global.currentSelectedObject);
      if (selectedObject && (!objectId || selectedObject.object_id === objectId)) {
        var lon = pickFirst(selectedObject.longitude, selectedObject.lon, selectedObject.lng);
        var lat = pickFirst(selectedObject.latitude, selectedObject.lat);
        if (isFinite(Number(lon)) && isFinite(Number(lat))) {
          return await flyToCoordinates(
            [Number(lon), Number(lat), Number(selectedObject.height || payload.height || 1000)],
            payload,
            {
              object_id: selectedObject.object_id,
              object_type: selectedObject.object_type,
              source: selectedObject.source || "selected_object"
            }
          );
        }
      }
      return {
        success: false,
        found: false,
        object_id: objectId ? String(objectId) : null,
        object_type: null,
        source: null,
        method: null,
        completed: false,
        error_code: objectId ? "OBJECT_NOT_FOUND" : "NO_OBJECT_ID",
        error: objectId ? "Object was not found in public WebGL runtime collections." : "No object_id was provided."
      };
    }

    function readSelectedObject() {
      if (typeof options.getSelectedObject === "function") {
        return options.getSelectedObject();
      }
      var viewer = getViewer();
      if (viewer && viewer.selectedEntity) {
        return viewer.selectedEntity;
      }
      return null;
    }

    function normalizeSelectedObject(value) {
      if (!value) return null;
      var properties = readProperties(value);
      return {
        object_id: pickFirst(value.object_id, value.objectId, value.id, properties.id, properties.EVID),
        object_type: pickFirst(value.object_type, value.objectType, value.type, properties.type),
        object_name: pickFirst(value.object_name, value.objectName, value.name, properties.name, properties.NAME),
        layer_id: pickFirst(value.layer_id, value.layerId, value.layer, properties.layer_id),
        longitude: pickFirst(value.longitude, value.lon, value.lng, properties.longitude, properties.lon, properties.lng),
        latitude: pickFirst(value.latitude, value.lat, properties.latitude, properties.lat),
        height: pickFirst(value.height, value.altitude, value.alt, properties.height, properties.altitude, properties.alt),
        source: pickFirst(value.source, properties.source),
        timestamp: pickFirst(value.timestamp, properties.timestamp),
        properties: properties
      };
    }

    function collectSpatialObjectsPayload() {
      var items = [];
      spatialObjectRegistry = {};
      objectIdAliasRegistry = {};
      businessIdCollisionRegistry = {};
      var selectedObject = normalizeSelectedObject(global.__EV_AGENT_SELECTED_OBJECT__ || global.currentSelectedObject);
      if (selectedObject) pushSpatialObject(items, selectedObject, "window.__EV_AGENT_SELECTED_OBJECT__");
      var viewer = getViewer();
      collectEntityCollectionSpatialObjects(viewer && viewer.entities, "viewer.entities", items);
      collectDataSourceSpatialObjects(viewer && viewer.dataSources, items);
      collectPrimitiveSpatialObjects(viewer && viewer.scene && viewer.scene.primitives, "viewer.scene.primitives", items);
      var objects = dedupeSpatialObjects(items);
      if (!objects.length) {
        return { source: "mock", reason: "webgl_objects_unavailable" };
      }
      return {
        source: "webgl",
        reason: "SceneObjectMap unavailable; scanned public Cesium collections",
        anchor: "viewer.entities/viewer.dataSources/current selected",
        objects: objects.map(publicSpatialObject)
      };
    }

    function collectDataSourceSpatialObjects(collection, items) {
      if (!collection) return;
      var length = Number(collection.length || 0);
      for (var i = 0; i < length; i += 1) {
        var dataSource = typeof collection.get === "function" ? collection.get(i) : collection[i];
        if (dataSource && dataSource.entities) {
          collectEntityCollectionSpatialObjects(dataSource.entities, "viewer.dataSources", items, {
            data_source_name: dataSource.name || null,
            data_source_index: i
          });
        }
      }
    }

    function collectPrimitiveSpatialObjects(collection, source, items) {
      if (!collection) return;
      var length = Number(collection.length || 0);
      for (var i = 0; i < length; i += 1) {
        var primitive = typeof collection.get === "function" ? collection.get(i) : collection[i];
        var spatialObject = primitiveToSpatialObject(primitive, source);
        if (spatialObject) items.push(spatialObject);
      }
    }

    function collectEntityCollectionSpatialObjects(collection, source, items, context) {
      var entities = collection && collection.values ? collection.values : ensureArray(collection);
      ensureArray(entities).forEach(function (entity) {
        if (entity && context) {
          entity.__evAgentDataSourceName = context.data_source_name || null;
          entity.__evAgentDataSourceIndex = context.data_source_index;
        }
        var spatialObject = entityToSpatialObject(entity, source);
        if (spatialObject) items.push(spatialObject);
      });
    }

    function entityToSpatialObject(entity, source) {
      if (!entity) return null;
      var properties = readProperties(entity);
      var position = readEntityPosition(entity);
      var runtimeId = entity.id !== undefined && entity.id !== null ? String(entity.id) : null;
      var businessIdMeta = getBusinessIdMeta(properties, entity);
      var layerMeta = getLayerKeyMeta(entity, properties, source);
      var rawObjectId = pickFirst(entity.object_id, properties.object_id, properties.objectId, properties.EVID, runtimeId);
      if (!rawObjectId && !businessIdMeta.business_id) return null;
      var canonicalMeta = makeCanonicalEntityId(layerMeta.layer_key, businessIdMeta, runtimeId);
      var objectId = canonicalMeta.object_id || String(rawObjectId);
      var idMeta = classifyObjectId(objectId, properties, entity, businessIdMeta, canonicalMeta);
      var graphicsTypes = getEntityGraphicsTypes(entity);
      var spatialValidity = classifySpatialValidity(entity, properties, position, graphicsTypes);
      var boundingSphereTarget = getEntityBoundingSphereTarget(entity);
      var sourceRef = makeSourceRef(source, objectId, entity);
      var spatialObject = {
        object_id: String(objectId),
        runtime_id: runtimeId,
        business_id: businessIdMeta.business_id,
        business_id_source: businessIdMeta.business_id_source,
        object_name: nullableString(pickFirst(entity.object_name, entity.name, properties.object_name, properties.name, properties.NAME)),
        display_name_source: pickFirst(entity.object_name, entity.name, properties.object_name, properties.name, properties.NAME) ? "source" : null,
        object_type: "entity",
        business_type: inferBusinessType(properties, graphicsTypes),
        layer_id: pickFirst(entity.layer_id, entity.layerId, properties.layer_id, properties.layerId, layerMeta.layer_id),
        layer_name: layerMeta.layer_name,
        layer_key: layerMeta.layer_key,
        layer_key_source: layerMeta.layer_key_source,
        longitude: position.longitude,
        latitude: position.latitude,
        height: position.height,
        position_valid: position.position_valid,
        position_source: position.position_source,
        position_attempted_source: position.position_attempted_source || null,
        position_error: position.position_error || null,
        cartesian_valid: position.cartesian_valid,
        cartesian_magnitude: position.cartesian_magnitude,
        properties: properties,
        feature_ref: sourceRef,
        source_ref: sourceRef,
        source: source,
        data_source_name: entity && entity.__evAgentDataSourceName || null,
        data_source_index: entity && entity.__evAgentDataSourceIndex !== undefined ? entity.__evAgentDataSourceIndex : null,
        can_fly_to: canFlyToTarget(entity, position),
        can_highlight: true,
        fly_target_type: getFlyTargetType(entity, position, "entity"),
        has_business_properties: hasBusinessProperties(properties),
        spatially_valid: spatialValidity.spatially_valid,
        exclude_reason: spatialValidity.exclude_reason,
        graphics_types: graphicsTypes,
        stable_id: idMeta.stable_id,
        id_scope: idMeta.id_scope,
        collision_reason: canonicalMeta.collision_reason || null,
        runtime_object: entity,
        runtime_bounding_sphere: boundingSphereTarget.valid ? boundingSphereTarget.bounding_sphere : null,
        runtime_bounding_sphere_valid: boundingSphereTarget.valid,
        runtime_bounding_sphere_radius: boundingSphereTarget.radius,
        bounding_sphere_valid: boundingSphereTarget.valid,
        bounding_sphere_radius: boundingSphereTarget.radius,
        standard_properties: getStandardEntityProperties(entity, graphicsTypes),
        business_properties: properties,
        metadata: getEntityMetadata(entity, properties, graphicsTypes)
      };
      registerSpatialObject(spatialObject);
      return spatialObject;
    }

    function primitiveToSpatialObject(primitive, source) {
      if (!primitive) return null;
      var properties = readProperties(primitive);
      var objectId = pickFirst(primitive.object_id, primitive.id, primitive.name, properties.object_id, properties.id, properties.EVID);
      if (!objectId) return null;
      var position = coordinatesFromValue(primitive.position, "feature.metadata");
      if (!position.position_valid) {
        position = coordinatesFromBoundingSphere(primitive.boundingSphere, "primitive.bounding_sphere", true);
      }
      var idMeta = classifyObjectId(objectId, properties, primitive);
      var primitiveSpatialValidity = {
        spatially_valid: !!(position && position.position_valid),
        exclude_reason: position && position.position_valid ? null : "NO_VALID_SPATIAL_TARGET"
      };
      var spatialObject = {
        object_id: String(objectId),
        object_name: nullableString(pickFirst(primitive.object_name, primitive.name, properties.object_name, properties.name, properties.NAME)),
        display_name_source: pickFirst(primitive.object_name, primitive.name, properties.object_name, properties.name, properties.NAME) ? "source" : null,
        object_type: String(pickFirst(primitive.object_type, primitive.type, properties.object_type, properties.type, "primitive")),
        layer_id: pickFirst(primitive.layer_id, primitive.layerId, properties.layer_id, properties.layerId),
        longitude: position.longitude,
        latitude: position.latitude,
        height: position.height,
        position_valid: position.position_valid,
        position_source: position.position_source,
        position_attempted_source: position.position_attempted_source || null,
        position_error: position.position_error || null,
        cartesian_valid: position.cartesian_valid,
        cartesian_magnitude: position.cartesian_magnitude,
        properties: properties,
        feature_ref: source + ":" + String(objectId),
        source_ref: source + ":" + String(objectId),
        source: source,
        can_fly_to: canFlyToTarget(primitive, position),
        can_highlight: true,
        fly_target_type: getFlyTargetType(primitive, position, "primitive"),
        has_business_properties: hasBusinessProperties(properties),
        spatially_valid: primitiveSpatialValidity.spatially_valid,
        exclude_reason: primitiveSpatialValidity.exclude_reason,
        graphics_types: [],
        stable_id: idMeta.stable_id,
        id_scope: idMeta.id_scope,
        runtime_object: primitive,
        metadata: {
          id: objectId,
          name: primitive.name || null,
          properties: properties
        }
      };
      registerSpatialObject(spatialObject);
      return spatialObject;
    }

    function canFlyToTarget(target, position) {
      if (!target) return false;
      if (position && position.position_valid) return true;
      if (isValidBoundingSphere(target.boundingSphere, true).valid) return true;
      if (isCesiumEntityLike(target) && getEntityBoundingSphereTarget(target).valid) return true;
      return false;
    }

    function getFlyTargetType(target, position, fallbackType) {
      if (!target) return null;
      if (position && position.position_valid) {
        if (position.position_source === "entity.bounding_sphere") return "entity_bounding_sphere";
        if (position.position_source === "primitive.bounding_sphere") return "primitive_bounding_sphere";
        if (position.position_source === "tileset.bounding_sphere") return "tileset_bounding_sphere";
        return "coordinates";
      }
      if (isValidBoundingSphere(target.boundingSphere, true).valid) {
        return fallbackType === "entity" ? "entity_bounding_sphere" : fallbackType + "_bounding_sphere";
      }
      if (fallbackType === "entity" && getEntityBoundingSphereTarget(target).valid) return "entity_bounding_sphere";
      return null;
    }

    function classifySpatialValidity(entity, properties, position, graphicsTypes) {
      var hasProps = hasBusinessProperties(properties);
      var onlyPoint = graphicsTypes.length === 1 && graphicsTypes[0] === "point";
      var hasName = !!(entity && entity.name);
      var spatiallyValid =
        !!(position && position.position_valid) ||
        isValidBoundingSphere(entity && entity.boundingSphere, true).valid ||
        getEntityBoundingSphereTarget(entity).valid;
      if (!spatiallyValid && !hasName && !hasProps && onlyPoint) {
        return { spatially_valid: false, exclude_reason: "EMPTY_POINT_ENTITY" };
      }
      return {
        spatially_valid: spatiallyValid,
        exclude_reason: spatiallyValid ? null : "NO_VALID_SPATIAL_TARGET"
      };
    }

    function getBusinessIdMeta(properties, target) {
      properties = properties || {};
      var priority = ["OBJECTID", "object_id", "objectId", "FID", "fid", "ID", "id", "uuid", "code"];
      for (var i = 0; i < priority.length; i += 1) {
        var key = priority[i];
        var value = properties[key];
        if (
          value === undefined &&
          target &&
          (key === "object_id" || key === "objectId" || key === "uuid" || key === "code") &&
          target[key] !== undefined
        ) {
          value = target[key];
        }
        if (value !== undefined && value !== null && String(value) !== "") {
          return { business_id: String(value), business_id_source: key };
        }
      }
      return { business_id: null, business_id_source: null };
    }

    function getLayerKeyMeta(entity, properties, source) {
      properties = properties || {};
      var layerId = pickFirst(entity && entity.layer_id, entity && entity.layerId, properties.layer_id, properties.layerId);
      if (layerId) {
        return { layer_key: sanitizeIdToken(layerId), layer_key_source: "layer_id", layer_id: String(layerId), layer_name: nullableString(pickFirst(properties.layer_name, properties.layerName)) };
      }
      var dataSourceName = entity && entity.__evAgentDataSourceName;
      if (dataSourceName) {
        return { layer_key: sanitizeIdToken(dataSourceName), layer_key_source: "data_source_name", layer_id: null, layer_name: String(dataSourceName) };
      }
      var dataSourceIndex = entity && entity.__evAgentDataSourceIndex;
      if (dataSourceIndex !== undefined && dataSourceIndex !== null && source === "viewer.dataSources") {
        return { layer_key: "data_source_index_" + String(dataSourceIndex), layer_key_source: "data_source_index", layer_id: null, layer_name: null };
      }
      var declaredLayerName = pickFirst(properties.layer_name, properties.layerName, entity && entity.layer_name, entity && entity.layerName);
      return { layer_key: sanitizeIdToken(declaredLayerName || source || "runtime"), layer_key_source: declaredLayerName ? "layer_name" : "source", layer_id: null, layer_name: nullableString(declaredLayerName) };
    }

    function makeCanonicalEntityId(layerKey, businessIdMeta, runtimeId) {
      if (!businessIdMeta || !businessIdMeta.business_id || !businessIdMeta.business_id_source) {
        return { object_id: runtimeId ? String(runtimeId) : null, collision_reason: null };
      }
      var baseId = "entity:" + sanitizeIdToken(layerKey || "unknown") + ":" + sanitizeIdToken(businessIdMeta.business_id_source) + ":" + sanitizeIdToken(businessIdMeta.business_id);
      var existing = spatialObjectRegistry[baseId];
      if (existing && existing.runtime_id && runtimeId && String(existing.runtime_id) !== String(runtimeId)) {
        var collisionKey = baseId;
        businessIdCollisionRegistry[collisionKey] = {
          error_code: "BUSINESS_ID_COLLISION",
          object_id: baseId,
          existing_runtime_id: existing.runtime_id,
          incoming_runtime_id: runtimeId,
          business_id: businessIdMeta.business_id,
          business_id_source: businessIdMeta.business_id_source
        };
        return { object_id: runtimeId ? String(runtimeId) : baseId, collision_reason: "BUSINESS_ID_COLLISION" };
      }
      return { object_id: baseId, collision_reason: null };
    }

    function inferBusinessType(properties, graphicsTypes) {
      properties = properties || {};
      var declaredType = pickFirst(properties.object_type, properties.objectType, properties.business_type, properties.businessType, properties.type);
      if (declaredType) return String(declaredType);
      if (
        ensureArray(graphicsTypes).indexOf("polygon") >= 0 &&
        (properties.NAME || properties.name) &&
        (properties.OBJECTID !== undefined || properties.SHAPE_AREA !== undefined || properties.SHAPE_LEN !== undefined)
      ) {
        return "administrative_region";
      }
      return null;
    }

    function sanitizeIdToken(value) {
      return encodeURIComponent(String(value === undefined || value === null ? "" : value).trim()).replace(/%20/g, "_");
    }

    function classifyObjectId(objectId, properties, target, businessIdMeta, canonicalMeta) {
      properties = properties || {};
      var id = String(objectId || "");
      if (canonicalMeta && canonicalMeta.collision_reason) {
        return { stable_id: false, id_scope: "page_session" };
      }
      if (businessIdMeta && businessIdMeta.business_id) {
        return { stable_id: true, id_scope: "business" };
      }
      var hasBusinessId =
        properties.object_id !== undefined ||
        properties.objectId !== undefined ||
        properties.OBJECTID !== undefined ||
        properties.ID !== undefined ||
        properties.uuid !== undefined ||
        properties.code !== undefined ||
        properties.EVID !== undefined ||
        properties.fid !== undefined ||
        properties.FID !== undefined ||
        properties.business_id !== undefined ||
        properties.businessId !== undefined;
      if (hasBusinessId) {
        return { stable_id: true, id_scope: "business" };
      }
      if (target && (target.object_id || target.objectId || target.business_id || target.businessId)) {
        return { stable_id: true, id_scope: "business" };
      }
      if (isUuidLike(id)) {
        return { stable_id: false, id_scope: "page_session" };
      }
      return { stable_id: false, id_scope: "runtime_only" };
    }

    function isUuidLike(value) {
      return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?:_.+)?$/i.test(String(value || ""));
    }

    function makeSourceRef(source, objectId, entity) {
      var runtimeId = entity && entity.id !== undefined && entity.id !== null ? entity.id : objectId;
      if (source === "viewer.dataSources") {
        var dataSourceName = entity && entity.__evAgentDataSourceName;
        var dataSourceIndex = entity && entity.__evAgentDataSourceIndex;
        var dataSourceToken = dataSourceName || (dataSourceIndex !== undefined && dataSourceIndex !== null ? "index_" + dataSourceIndex : "unknown");
        return source + ":" + dataSourceToken + ":" + String(runtimeId);
      }
      return source + ":" + String(runtimeId);
    }

    function pushSpatialObject(items, value, source) {
      var properties = value.properties || {};
      var longitude = pickFirst(value.longitude, value.lon, value.lng, properties.longitude, properties.lon, properties.lng);
      var latitude = pickFirst(value.latitude, value.lat, properties.latitude, properties.lat);
      var objectId = pickFirst(value.object_id, value.id);
      if (!objectId) return;
      var idMeta = classifyObjectId(objectId, properties, value);
      var lon = finiteNumber(longitude);
      var lat = finiteNumber(latitude);
      var position = positionResult(lon, lat, finiteNumber(pickFirst(value.height, value.altitude, value.alt)), lon !== null && lat !== null, lon !== null && lat !== null ? "feature.metadata" : "unavailable");
      var spatialObject = {
        object_id: String(objectId),
        object_name: nullableString(pickFirst(value.object_name, value.name)),
        display_name_source: pickFirst(value.object_name, value.name) ? "source" : null,
        object_type: String(pickFirst(value.object_type, value.type, "unknown")),
        layer_id: pickFirst(value.layer_id, value.layerId),
        longitude: position.longitude,
        latitude: position.latitude,
        height: position.height,
        position_valid: position.position_valid,
        position_source: position.position_source,
        properties: properties,
        feature_ref: source + ":" + String(objectId),
        source_ref: source + ":" + String(objectId),
        source: source,
        can_fly_to: position.position_valid,
        fly_target_type: position.position_valid ? "coordinates" : null,
        has_business_properties: hasBusinessProperties(properties),
        graphics_types: [],
        stable_id: idMeta.stable_id,
        id_scope: idMeta.id_scope,
        runtime_object: value,
        metadata: { properties: properties }
      };
      registerSpatialObject(spatialObject);
      items.push(spatialObject);
    }

    function readEntityPosition(entity) {
      var value = entity && entity.position;
      var attemptedSource = value ? "entity.position" : null;
      if (value && typeof value.getValue === "function") {
        try {
          value = value.getValue(getCurrentTime());
        } catch (error) {
          try {
            value = value.getValue();
          } catch (innerError) {
            value = null;
          }
        }
      }
      var position = coordinatesFromValue(value, "entity.position");
      if (position.position_valid) return position;
      if (attemptedSource && !position.position_attempted_source) {
        position.position_attempted_source = attemptedSource;
      }
      var spherePosition = readEntityBoundingSpherePosition(entity);
      if (spherePosition.position_valid) return spherePosition;
      return position.position_error ? position : spherePosition;
    }

    function readEntityBoundingSpherePosition(entity) {
      var target = getEntityBoundingSphereTarget(entity);
      if (target.pending) return positionResult(null, null, null, false, "pending");
      if (!target.valid) {
        return positionResult(null, null, null, false, "unavailable", {
          position_attempted_source: "entity.bounding_sphere",
          position_error: target.error_code || "INVALID_BOUNDING_SPHERE",
          cartesian_valid: target.cartesian_valid,
          cartesian_magnitude: target.cartesian_magnitude
        });
      }
      return coordinatesFromBoundingSphere(target.bounding_sphere, "entity.bounding_sphere", true);
    }

    function coordinatesFromValue(value, source) {
      if (!value) return positionUnavailable();
      var lon = pickFirst(value.longitude, value.lon, value.lng);
      var lat = pickFirst(value.latitude, value.lat);
      var lonNumber = finiteNumber(lon);
      var latNumber = finiteNumber(lat);
      if (lonNumber !== null && latNumber !== null) {
        return positionResult(lonNumber, latNumber, finiteNumber(value.height), true, source || "feature.metadata", null);
      }
      var Cesium = global.Cesium || {};
      if (value.x !== undefined && value.y !== undefined && value.z !== undefined) {
        var cartesianCheck = isValidEarthFixedCartesian(value);
        if (!cartesianCheck.valid) {
          return positionResult(null, null, null, false, "unavailable", {
            position_attempted_source: source || "cartesian",
            position_error: cartesianCheck.error,
            cartesian_valid: false,
            cartesian_magnitude: cartesianCheck.magnitude
          });
        }
        if (!Cesium.Cartographic || typeof Cesium.Cartographic.fromCartesian !== "function") {
          return positionResult(null, null, null, false, "unavailable", {
            position_attempted_source: source || "cartesian",
            position_error: "CARTOGRAPHIC_UNAVAILABLE",
            cartesian_valid: true,
            cartesian_magnitude: cartesianCheck.magnitude
          });
        }
        var cartographic = Cesium.Cartographic.fromCartesian(value);
        var longitude = finiteNumber(toDegrees(cartographic.longitude, Cesium));
        var latitude = finiteNumber(toDegrees(cartographic.latitude, Cesium));
        return positionResult(longitude, latitude, finiteNumber(cartographic.height), longitude !== null && latitude !== null, source || "feature.metadata", {
          cartesian_valid: true,
          cartesian_magnitude: cartesianCheck.magnitude
        });
      }
      return positionUnavailable();
    }

    function coordinatesFromBoundingSphere(boundingSphere, source, requirePositiveRadius) {
      var sphereCheck = isValidBoundingSphere(boundingSphere, !!requirePositiveRadius);
      if (!sphereCheck.valid) {
        return positionResult(null, null, null, false, "unavailable", {
          position_attempted_source: source || "primitive.bounding_sphere",
          position_error: sphereCheck.error,
          cartesian_valid: sphereCheck.center_valid,
          cartesian_magnitude: sphereCheck.center_magnitude
        });
      }
      return coordinatesFromValue(boundingSphere.center, source || "primitive.bounding_sphere");
    }

    function isValidEarthFixedCartesian(value) {
      if (!value) {
        return { valid: false, magnitude: null, error: "MISSING_CARTESIAN" };
      }
      var x = Number(value.x);
      var y = Number(value.y);
      var z = Number(value.z);
      if (!isFinite(x) || !isFinite(y) || !isFinite(z)) {
        return { valid: false, magnitude: null, error: "NON_FINITE_CARTESIAN" };
      }
      var magnitude = Math.hypot(x, y, z);
      if (!isFinite(magnitude)) {
        return { valid: false, magnitude: null, error: "NON_FINITE_CARTESIAN" };
      }
      if (magnitude === 0) {
        return { valid: false, magnitude: 0, error: "ZERO_CARTESIAN" };
      }
      var minMagnitude = getMinimumEarthFixedMagnitude();
      if (magnitude < minMagnitude) {
        return { valid: false, magnitude: magnitude, error: "CARTESIAN_TOO_CLOSE_TO_EARTH_CENTER" };
      }
      return { valid: true, magnitude: magnitude, error: null };
    }

    function getMinimumEarthFixedMagnitude() {
      var Cesium = global.Cesium || {};
      var minimumRadius = Cesium.Ellipsoid && Cesium.Ellipsoid.WGS84 && finiteNumber(Cesium.Ellipsoid.WGS84.minimumRadius);
      if (minimumRadius !== null && minimumRadius > 0) return minimumRadius * 0.5;
      return 3000000;
    }

    function isValidBoundingSphere(boundingSphere, requirePositiveRadius) {
      if (!boundingSphere || !boundingSphere.center) {
        return {
          valid: false,
          error: "INVALID_BOUNDING_SPHERE",
          center_valid: false,
          center_magnitude: null
        };
      }
      var centerCheck = isValidEarthFixedCartesian(boundingSphere.center);
      var radius = finiteNumber(boundingSphere.radius);
      if (!centerCheck.valid || radius === null || radius < 0 || (requirePositiveRadius && radius <= 0)) {
        return {
          valid: false,
          error: "INVALID_BOUNDING_SPHERE",
          center_valid: centerCheck.valid,
          center_magnitude: centerCheck.magnitude
        };
      }
      return {
        valid: true,
        error: null,
        center_valid: true,
        center_magnitude: centerCheck.magnitude,
        radius: radius
      };
    }

    function getEntityBoundingSphereTarget(entity) {
      if (!entity) {
        return spatialTargetFailure("INVALID_BOUNDING_SPHERE");
      }
      var directCheck = isValidBoundingSphere(entity.boundingSphere, true);
      if (directCheck.valid) {
        return {
          valid: true,
          target_type: "entity_bounding_sphere",
          bounding_sphere: entity.boundingSphere,
          radius: directCheck.radius,
          cartesian_valid: true,
          cartesian_magnitude: directCheck.center_magnitude,
          error_code: null
        };
      }
      var viewer = getViewer();
      if (!viewer || !viewer.dataSourceDisplay || typeof viewer.dataSourceDisplay.getBoundingSphere !== "function") {
        return spatialTargetFailure("INVALID_BOUNDING_SPHERE");
      }
      var Cesium = global.Cesium || {};
      var boundingSphere = Cesium.BoundingSphere ? new Cesium.BoundingSphere() : { center: null, radius: 0 };
      try {
        var state = viewer.dataSourceDisplay.getBoundingSphere(entity, false, boundingSphere);
        if (String(state).toUpperCase().indexOf("PENDING") >= 0) {
          return {
            valid: false,
            pending: true,
            target_type: null,
            bounding_sphere: null,
            radius: null,
            error_code: "BOUNDING_SPHERE_PENDING"
          };
        }
        var computedCheck = isValidBoundingSphere(boundingSphere, true);
        if (!computedCheck.valid) {
          return {
            valid: false,
            target_type: null,
            bounding_sphere: null,
            radius: null,
            cartesian_valid: computedCheck.center_valid,
            cartesian_magnitude: computedCheck.center_magnitude,
            error_code: computedCheck.error || "INVALID_BOUNDING_SPHERE"
          };
        }
        return {
          valid: true,
          target_type: "entity_bounding_sphere",
          bounding_sphere: boundingSphere,
          radius: computedCheck.radius,
          cartesian_valid: true,
          cartesian_magnitude: computedCheck.center_magnitude,
          error_code: null
        };
      } catch (error) {
        return spatialTargetFailure("INVALID_BOUNDING_SPHERE");
      }
    }

    function resolveSpatialTarget(registryRecord, runtimeObject) {
      var record = registryRecord || {};
      var target = runtimeObject || record.target || record.runtime_object;
      if (record.runtime_bounding_sphere_valid && record.runtime_bounding_sphere) {
        return spatialTargetFromBoundingSphere(
          record.runtime_bounding_sphere,
          "entity_bounding_sphere",
          record.runtime_bounding_sphere_radius
        );
      }
      if (record.bounding_sphere_valid && record.runtime_bounding_sphere) {
        return spatialTargetFromBoundingSphere(
          record.runtime_bounding_sphere,
          "entity_bounding_sphere",
          record.bounding_sphere_radius
        );
      }
      if (target && (record.object_type === "entity" || isCesiumEntityLike(target))) {
        var entitySphere = getEntityBoundingSphereTarget(target);
        if (entitySphere.valid) return entitySphere;
      }
      if (target && target.boundingSphere) {
        var primitiveSphere = spatialTargetFromBoundingSphere(
          target.boundingSphere,
          record.object_type === "primitive" ? "primitive_bounding_sphere" : "tileset_bounding_sphere",
          null
        );
        if (primitiveSphere.valid) return primitiveSphere;
      }
      var lon = finiteNumber(record.longitude);
      var lat = finiteNumber(record.latitude);
      if (record.position_valid && lon !== null && lat !== null) {
        return {
          valid: true,
          target_type: target && target.position ? "entity_position" : "coordinates",
          cartesian: null,
          bounding_sphere: null,
          longitude: lon,
          latitude: lat,
          height: finiteNumber(record.height),
          radius: null,
          error_code: null
        };
      }
      return spatialTargetFailure("OBJECT_HAS_NO_VALID_SPATIAL_TARGET");
    }

    function spatialTargetFromBoundingSphere(boundingSphere, targetType, radiusOverride) {
      var check = isValidBoundingSphere(boundingSphere, true);
      if (!check.valid) return spatialTargetFailure(check.error || "INVALID_BOUNDING_SPHERE");
      var position = coordinatesFromBoundingSphere(boundingSphere, targetType, true);
      return {
        valid: true,
        target_type: targetType,
        cartesian: boundingSphere.center,
        bounding_sphere: boundingSphere,
        longitude: position.longitude,
        latitude: position.latitude,
        height: position.height,
        radius: finiteNumber(radiusOverride) !== null ? finiteNumber(radiusOverride) : check.radius,
        error_code: null
      };
    }

    function spatialTargetFailure(errorCode) {
      return {
        valid: false,
        target_type: null,
        cartesian: null,
        bounding_sphere: null,
        longitude: null,
        latitude: null,
        height: null,
        radius: null,
        error_code: errorCode || "OBJECT_HAS_NO_VALID_SPATIAL_TARGET"
      };
    }

    function positionResult(longitude, latitude, height, valid, source, diagnostics) {
      diagnostics = diagnostics || {};
      return {
        longitude: valid ? finiteNumber(longitude) : null,
        latitude: valid ? finiteNumber(latitude) : null,
        height: valid ? finiteNumber(height) : null,
        position_valid: !!valid,
        position_source: valid ? source : source || "unavailable",
        position_attempted_source: diagnostics.position_attempted_source || null,
        position_error: diagnostics.position_error || null,
        cartesian_valid: diagnostics.cartesian_valid !== undefined ? diagnostics.cartesian_valid : null,
        cartesian_magnitude: diagnostics.cartesian_magnitude !== undefined ? diagnostics.cartesian_magnitude : null
      };
    }

    function positionUnavailable() {
      return positionResult(null, null, null, false, "unavailable", null);
    }

    function positionForSpatialObject(value) {
      var valid = !!(value && value.position_valid);
      var lon = finiteNumber(value && value.longitude);
      var lat = finiteNumber(value && value.latitude);
      return {
        longitude: valid && lon !== null ? lon : null,
        latitude: valid && lat !== null ? lat : null,
        height: valid ? finiteNumber(value && value.height) : null,
        position_valid: valid && lon !== null && lat !== null,
        position_source: value && value.position_source || "unavailable",
        position_attempted_source: value && value.position_attempted_source || null,
        position_error: value && value.position_error || null,
        cartesian_valid: value && value.cartesian_valid !== undefined ? value.cartesian_valid : null,
        cartesian_magnitude: value && value.cartesian_magnitude !== undefined ? value.cartesian_magnitude : null
      };
    }

    function publicSpatialObject(item) {
      if (!item) return item;
      var result = {};
      [
        "object_id",
        "runtime_id",
        "business_id",
        "business_id_source",
        "object_name",
        "display_name_source",
        "object_type",
        "business_type",
        "layer_id",
        "layer_name",
        "layer_key",
        "layer_key_source",
        "longitude",
        "latitude",
        "height",
        "position_valid",
        "position_source",
        "position_attempted_source",
        "position_error",
        "cartesian_valid",
        "cartesian_magnitude",
        "properties",
        "feature_ref",
        "source_ref",
        "source",
        "data_source_name",
        "data_source_index",
        "can_fly_to",
        "can_highlight",
        "fly_target_type",
        "has_business_properties",
        "spatially_valid",
        "exclude_reason",
        "graphics_types",
        "stable_id",
        "id_scope",
        "collision_reason",
        "bounding_sphere_valid",
        "bounding_sphere_radius",
        "standard_properties",
        "business_properties",
        "metadata"
      ].forEach(function (key) {
        if (item[key] !== undefined) result[key] = item[key];
      });
      return result;
    }

    function registerSpatialObject(spatialObject) {
      if (!spatialObject || !spatialObject.object_id) return null;
      var id = String(spatialObject.object_id);
      var position = positionForSpatialObject(spatialObject);
      var entry = mergeObjects(spatialObject, {
        object_id: id,
        target: spatialObject.runtime_object || spatialObject.target || spatialObject,
        name: spatialObject.object_name || spatialObject.name || null,
        source_ref: spatialObject.source_ref || spatialObject.feature_ref || spatialObject.source + ":" + id,
        metadata: spatialObject.metadata || {},
        properties: spatialObject.properties || {},
        graphics_types: spatialObject.graphics_types || [],
        runtime_bounding_sphere: spatialObject.runtime_bounding_sphere || null,
        runtime_bounding_sphere_valid: spatialObject.runtime_bounding_sphere_valid === true,
        runtime_bounding_sphere_radius: spatialObject.runtime_bounding_sphere_radius !== undefined ? spatialObject.runtime_bounding_sphere_radius : null,
        bounding_sphere_valid: spatialObject.bounding_sphere_valid === true,
        bounding_sphere_radius: spatialObject.bounding_sphere_radius !== undefined ? spatialObject.bounding_sphere_radius : null,
        longitude: position.longitude,
        latitude: position.latitude,
        height: position.height,
        position_valid: position.position_valid,
        position_source: position.position_source,
        position_attempted_source: position.position_attempted_source,
        position_error: position.position_error,
        cartesian_valid: position.cartesian_valid,
        cartesian_magnitude: position.cartesian_magnitude,
        position: position
      });
      spatialObjectRegistry[id] = entry;
      registerObjectAlias(id, id);
      registerObjectAlias(spatialObject.runtime_id, id);
      registerObjectAlias(spatialObject.source_ref, id);
      registerObjectAlias(spatialObject.feature_ref, id);
      if (entry.target && entry.target.id !== undefined) registerObjectAlias(entry.target.id, id);
      return entry;
    }

    function registerObjectAlias(alias, canonicalId) {
      if (alias === undefined || alias === null || canonicalId === undefined || canonicalId === null) return;
      objectIdAliasRegistry[String(alias)] = String(canonicalId);
    }

    function canonicalObjectId(objectId) {
      if (objectId === undefined || objectId === null) return null;
      var key = String(objectId);
      return objectIdAliasRegistry[key] || (spatialObjectRegistry[key] ? key : null);
    }

    function registerResolvedObject(target, source, sourceRef, resolver) {
      var spatialObject = spatialObjectFromTarget(target, source, sourceRef);
      if (!spatialObject) return null;
      if (resolver) spatialObject.resolver = resolver;
      return registerSpatialObject(spatialObject);
    }

    function spatialObjectFromTarget(target, source, sourceRef) {
      if (!target) return null;
      if (target.object_id && target.source && target.source_ref && target.runtime_object) {
        return target;
      }
      if (isCesiumEntityLike(target)) {
        return entityToSpatialObjectWithRef(target, source || "viewer.entities", sourceRef);
      }
      if (target.object_id || target.longitude !== undefined || target.lon !== undefined) {
        var items = [];
        pushSpatialObject(items, target, source || "selected_object");
        if (items[0]) {
          items[0].source_ref = sourceRef || items[0].source_ref;
          items[0].feature_ref = items[0].source_ref;
          return items[0];
        }
      }
      var primitive = primitiveToSpatialObject(target, source || "viewer.scene.primitives");
      if (primitive && sourceRef) {
        primitive.source_ref = sourceRef;
        primitive.feature_ref = sourceRef;
      }
      return primitive;
    }

    function entityToSpatialObjectWithRef(entity, source, sourceRef) {
      var spatialObject = entityToSpatialObject(entity, source);
      if (spatialObject && sourceRef) {
        spatialObject.source_ref = sourceRef;
        spatialObject.feature_ref = sourceRef;
      }
      return spatialObject;
    }

    function isCesiumEntityLike(value) {
      if (!value) return false;
      return !!(
        value.position ||
        value.polygon ||
        value.polyline ||
        value.point ||
        value.billboard ||
        value.label ||
        value.model ||
        value.rectangle ||
        value.ellipse ||
        value.wall ||
        value.corridor ||
        value.cylinder ||
        value.properties
      );
    }

    function inferRuntimeSource(target, objectId) {
      var id = String(objectId || target && target.id || "");
      var viewer = getViewer();
      if (viewer && viewer.entities && typeof viewer.entities.getById === "function" && viewer.entities.getById(id) === target) {
        return { source: "viewer.entities", source_ref: "viewer.entities:" + id };
      }
      var dataSourceEntity = resolveDataSourceEntity(viewer && viewer.dataSources, id);
      if (dataSourceEntity && dataSourceEntity === target) {
        return { source: "viewer.dataSources", source_ref: makeSourceRef("viewer.dataSources", id, target) };
      }
      var primitive = resolvePrimitive(viewer && viewer.scene && viewer.scene.primitives, id);
      if (primitive && primitive === target) {
        return { source: "viewer.scene.primitives", source_ref: "viewer.scene.primitives:" + id };
      }
      var selectedObject = normalizeSelectedObject(global.__EV_AGENT_SELECTED_OBJECT__ || global.currentSelectedObject);
      if (selectedObject && String(selectedObject.object_id || "") === id) {
        return { source: "selected_object", source_ref: "selected_object:" + id };
      }
      return { source: "runtime_object", source_ref: "runtime_object:" + id };
    }

    function getCurrentTime() {
      var viewer = getViewer();
      if (viewer && viewer.clock && viewer.clock.currentTime) return viewer.clock.currentTime;
      var Cesium = global.Cesium || {};
      if (Cesium.JulianDate && typeof Cesium.JulianDate.now === "function") return Cesium.JulianDate.now();
      return undefined;
    }

    function getEntityGraphicsTypes(entity) {
      if (!entity) return [];
      return [
        "point",
        "billboard",
        "label",
        "model",
        "polygon",
        "polyline",
        "rectangle",
        "ellipse",
        "wall",
        "corridor",
        "cylinder"
      ].filter(function (key) {
        return !!entity[key];
      });
    }

    function getEntityMetadata(entity, properties, graphicsTypes) {
      entity = entity || {};
      return {
        id: entity.id !== undefined && entity.id !== null ? String(entity.id) : null,
        name: entity.name || null,
        show: readMaybeValue(entity.show),
        availability: readMaybeValue(entity.availability) || null,
        parent_id: entity.parent && entity.parent.id ? String(entity.parent.id) : null,
        graphics_types: graphicsTypes || getEntityGraphicsTypes(entity),
        description: readMaybeValue(entity.description) || null
      };
    }

    function getStandardEntityProperties(entity, graphicsTypes) {
      entity = entity || {};
      return {
        id: entity.id !== undefined && entity.id !== null ? String(entity.id) : null,
        name: entity.name || null,
        show: readMaybeValue(entity.show),
        graphics_types: graphicsTypes || getEntityGraphicsTypes(entity),
        description: readMaybeValue(entity.description) || null
      };
    }

    function hasBusinessProperties(properties) {
      return Object.keys(properties || {}).some(function (key) {
        return [
          "object_id",
          "objectId",
          "EVID",
          "fid",
          "FID",
          "business_id",
          "businessId",
          "name",
          "NAME",
          "object_name",
          "layer_id",
          "layerId"
        ].indexOf(key) >= 0;
      });
    }

    function nullableString(value) {
      if (value === undefined || value === null || value === "") return null;
      return String(value);
    }

    function dedupeSpatialObjects(items) {
      var seen = {};
      return items.filter(function (item) {
        var key = item.object_id || (item.longitude + ":" + item.latitude + ":" + item.object_name);
        if (seen[key]) return false;
        seen[key] = true;
        return true;
      });
    }

    function readMapState() {
      var viewer = getViewer();
      var camera = viewer && viewer.camera;
      var cameraState = {};
      var center = null;
      var bounds = null;
      if (camera) {
        cameraState = {
          heading: readMaybeValue(camera.heading),
          pitch: readMaybeValue(camera.pitch),
          roll: readMaybeValue(camera.roll),
          position: readCartesian(camera.position)
        };
        center = readCameraCenter(viewer, camera);
        bounds = readBounds(viewer);
      }
      return {
        center: center,
        zoom: readZoom(camera),
        camera: cameraState,
        bounds: bounds
      };
    }

    function readActiveLayers() {
      if (typeof options.getActiveLayers === "function") {
        return ensureArray(options.getActiveLayers());
      }
      var manager = getLayerManager();
      var candidates = manager && (manager.layers || manager._layers || manager.layerList || manager._layerList);
      if (!candidates) return [];
      return ensureArray(candidates)
        .filter(function (layer) {
          return layer && layer.show !== false && layer.visible !== false;
        })
        .map(function (layer) {
          return String(layer.id || layer.layer_id || layer.name || layer.customTag && layer.customTag.serviceName || "");
        })
        .filter(Boolean);
    }

    function resolveObject(objectId) {
      if (!objectId) return null;
      if (typeof options.resolveObject === "function") {
        return options.resolveObject(objectId);
      }
      var selectedObject = normalizeSelectedObject(global.__EV_AGENT_SELECTED_OBJECT__ || global.currentSelectedObject);
      if (selectedObject && selectedObject.object_id === objectId) {
        return selectedObject;
      }
      var viewer = getViewer();
      if (viewer && viewer.entities && typeof viewer.entities.getById === "function") {
        var entity = viewer.entities.getById(objectId);
        if (entity) return entity;
      }
      var dataSourceEntity = resolveDataSourceEntity(viewer && viewer.dataSources, objectId);
      if (dataSourceEntity) return dataSourceEntity;
      var primitive = resolvePrimitive(viewer && viewer.scene && viewer.scene.primitives, objectId);
      if (primitive) return primitive;
      return null;
    }

    function resolveSpatialObject(objectId) {
      if (!objectId) return null;
      var requestedId = String(objectId);
      if (!spatialObjectRegistry[requestedId] && !objectIdAliasRegistry[requestedId]) {
        collectSpatialObjectsPayload();
      }
      var canonicalId = objectIdAliasRegistry[requestedId] || requestedId;
      var registered = spatialObjectRegistry[canonicalId];
      if (registered) {
        return registered;
      }
      var selectedObject = normalizeSelectedObject(global.__EV_AGENT_SELECTED_OBJECT__ || global.currentSelectedObject);
      if (selectedObject && String(selectedObject.object_id) === String(objectId)) {
        return registerResolvedObject(selectedObject, "selected_object", "selected_object:" + String(objectId), null);
      }
      var viewer = getViewer();
      if (viewer && viewer.entities && typeof viewer.entities.getById === "function") {
        var entity = viewer.entities.getById(objectId);
        if (entity) return registerResolvedObject(entity, "viewer.entities", "viewer.entities:" + String(objectId), null);
      }
      var dataSourceEntity = resolveDataSourceEntity(viewer && viewer.dataSources, objectId);
      if (dataSourceEntity) return registerResolvedObject(dataSourceEntity, "viewer.dataSources", makeSourceRef("viewer.dataSources", objectId, dataSourceEntity), null);
      var primitive = resolvePrimitive(viewer && viewer.scene && viewer.scene.primitives, objectId);
      if (primitive) return registerResolvedObject(primitive, "viewer.scene.primitives", "viewer.scene.primitives:" + String(objectId), null);
      if (typeof options.resolveObject === "function") {
        var optionTarget = options.resolveObject(objectId);
        if (optionTarget) {
          var sourceInfo = inferRuntimeSource(optionTarget, objectId);
          return registerResolvedObject(optionTarget, sourceInfo.source, sourceInfo.source_ref, "options.resolveObject");
        }
      }
      return null;
    }

    function resolveDataSourceEntity(collection, objectId) {
      if (!collection) return null;
      var length = Number(collection.length || 0);
      for (var i = 0; i < length; i += 1) {
        var dataSource = typeof collection.get === "function" ? collection.get(i) : collection[i];
        if (dataSource && dataSource.entities && typeof dataSource.entities.getById === "function") {
          var entity = dataSource.entities.getById(objectId);
          if (entity) {
            entity.__evAgentDataSourceName = dataSource.name || null;
            entity.__evAgentDataSourceIndex = i;
            return entity;
          }
        }
        var entities = dataSource && dataSource.entities && dataSource.entities.values;
        var match = ensureArray(entities).filter(function (entity) {
          return entity && String(entity.id || "") === String(objectId);
        })[0];
        if (match) {
          match.__evAgentDataSourceName = dataSource && dataSource.name || null;
          match.__evAgentDataSourceIndex = i;
          return match;
        }
      }
      return null;
    }

    function resolvePrimitive(collection, objectId) {
      if (!collection) return null;
      var length = Number(collection.length || 0);
      for (var i = 0; i < length; i += 1) {
        var primitive = typeof collection.get === "function" ? collection.get(i) : collection[i];
        if (primitive && String(primitive.id || primitive.name || "") === String(objectId)) {
          return primitive;
        }
      }
      return null;
    }

    async function flyToCoordinates(coordinates, payload, meta) {
      var Cesium = global.Cesium || {};
      var viewer = getViewer();
      var lon = Number(coordinates[0]);
      var lat = Number(coordinates[1]);
      var height = Number(coordinates[2] || payload.height || 800);
      if (!isFinite(lon) || !isFinite(lat)) {
        return failureResult("INVALID_COORDINATES", "Cannot fly because longitude/latitude are invalid.", meta);
      }
      if (!viewer || !viewer.camera || typeof viewer.camera.flyTo !== "function") {
        return failureResult("VIEWER_UNAVAILABLE", "window.viewer.camera.flyTo is unavailable.", meta);
      }
      if (Cesium.Cartesian3 && typeof Cesium.Cartesian3.fromDegrees === "function") {
        var destination = Cesium.Cartesian3.fromDegrees(lon, lat, height);
        await flyCameraTo(viewer.camera, "flyTo", {
          destination: destination,
          duration: Number(payload.duration_ms || 1200) / 1000
        });
        var actual = cameraCoordinates(viewer.camera, Cesium);
        var fallbackUsed = false;
        if (!cameraReachedTarget(actual, lon, lat)) {
          if (typeof viewer.camera.setView !== "function") {
            return failureResult("CAMERA_POSITION_UNCHANGED", "The WebGL camera did not move to the requested location.", meta);
          }
          viewer.camera.setView({ destination: destination });
          if (viewer.scene && typeof viewer.scene.requestRender === "function") viewer.scene.requestRender();
          actual = cameraCoordinates(viewer.camera, Cesium);
          fallbackUsed = true;
        }
        if (!cameraReachedTarget(actual, lon, lat)) {
          return failureResult("CAMERA_POSITION_UNCHANGED", "The WebGL camera did not move to the requested location.", meta);
        }
        return {
          success: true,
          found: meta && meta.object_id !== undefined ? true : null,
          object_id: meta && meta.object_id || null,
          object_type: meta && meta.object_type || null,
          source: meta && meta.source || "coordinates",
          method: fallbackUsed ? "camera.setView" : "camera.flyTo",
          completed: true,
          longitude: actual.longitude,
          latitude: actual.latitude,
          height: actual.height,
          fallback_used: fallbackUsed
        };
      }
      warn("Cesium.Cartesian3.fromDegrees is not available.", payload);
      return failureResult("CESIUM_UNAVAILABLE", "Cesium.Cartesian3.fromDegrees is unavailable.", meta);
    }

    function pickNumber() {
      for (var i = 0; i < arguments.length; i += 1) {
        var value = Number(arguments[i]);
        if (isFinite(value)) return value;
      }
      return NaN;
    }

    async function flyToResolvedObject(target, payload, resolved) {
      if (!target) return failureResult("OBJECT_NOT_FOUND", "Object target is empty.", resolved);
      var viewer = getViewer();
      var summary = resolvedObjectSummary(resolved, target);
      if (!viewer || !viewer.camera) return failureResult("VIEWER_UNAVAILABLE", "window.viewer.camera is unavailable.", summary);
      var cameraBefore = cameraCartesianSnapshot(viewer.camera);
      var spatialTarget = resolveSpatialTarget(resolved, target);
      if (!spatialTarget.valid) {
        return failureResult(
          spatialTarget.error_code || "OBJECT_HAS_NO_VALID_SPATIAL_TARGET",
          "The object has no valid position or bounding volume.",
          summary
        );
      }
      if (spatialTarget.bounding_sphere && spatialTarget.target_type) {
        var sphereRange = resolveFlyRange(payload, spatialTarget.bounding_sphere);
        var sphereOptions = buildFlyToOptions(payload, sphereRange);
        if (typeof viewer.camera.flyToBoundingSphere === "function") {
          await flyCameraTo(viewer.camera, "flyToBoundingSphere", spatialTarget.bounding_sphere, sphereOptions);
          var flyMethod = "camera.flyToBoundingSphere";
          var fallbackUsed = false;
          if (!cameraCartesianChanged(cameraBefore, cameraCartesianSnapshot(viewer.camera)) && typeof viewer.camera.viewBoundingSphere === "function") {
            viewer.camera.viewBoundingSphere(spatialTarget.bounding_sphere, sphereOptions.offset);
            if (global.Cesium && global.Cesium.Matrix4 && global.Cesium.Matrix4.IDENTITY && typeof viewer.camera.lookAtTransform === "function") {
              viewer.camera.lookAtTransform(global.Cesium.Matrix4.IDENTITY);
            }
            if (viewer.scene && typeof viewer.scene.requestRender === "function") viewer.scene.requestRender();
            flyMethod = "camera.viewBoundingSphere";
            fallbackUsed = true;
          }
          if (!cameraCartesianChanged(cameraBefore, cameraCartesianSnapshot(viewer.camera))) {
            return failureResult("CAMERA_POSITION_UNCHANGED", "The WebGL camera did not move to the requested object.", summary);
          }
          return flySuccess(mergeObjects(summary, {
            range: sphereRange,
            bounding_sphere_radius: spatialTarget.radius,
            fly_target_type: spatialTarget.target_type,
            fallback_used: fallbackUsed
          }), flyMethod);
        }
        if (typeof viewer.flyTo === "function" && target) {
          await maybePromise(viewer.flyTo(target, sphereOptions));
          if (!cameraCartesianChanged(cameraBefore, cameraCartesianSnapshot(viewer.camera))) {
            return failureResult("CAMERA_POSITION_UNCHANGED", "The WebGL camera did not move to the requested object.", summary);
          }
          return flySuccess(mergeObjects(summary, {
            range: sphereRange,
            bounding_sphere_radius: spatialTarget.radius,
            fly_target_type: spatialTarget.target_type
          }), "viewer.flyTo");
        }
      }
      if (spatialTarget.target_type === "entity_position" && typeof viewer.flyTo === "function") {
        var entityRange = resolveFlyRange(payload, null);
        await maybePromise(viewer.flyTo(target, buildFlyToOptions(payload, entityRange)));
        if (!cameraCartesianChanged(cameraBefore, cameraCartesianSnapshot(viewer.camera))) {
          return failureResult("CAMERA_POSITION_UNCHANGED", "The WebGL camera did not move to the requested object.", summary);
        }
        return flySuccess(mergeObjects(summary, {
          range: entityRange,
          fly_target_type: "entity_position"
        }), "viewer.flyTo");
      }
      if (spatialTarget.target_type === "coordinates") {
        var safeHeight = Math.max(0, finiteNumber(spatialTarget.height) || 0);
        return await flyToCoordinates([spatialTarget.longitude, spatialTarget.latitude, safeHeight], payload, summary);
      }
      if (resolved && resolved.position && resolved.position.position_valid && target.position && typeof viewer.flyTo === "function") {
        var entityRange = resolveFlyRange(payload, target.boundingSphere || null);
        await maybePromise(viewer.flyTo(target, buildFlyToOptions(payload, entityRange)));
        if (!cameraCartesianChanged(cameraBefore, cameraCartesianSnapshot(viewer.camera))) {
          return failureResult("CAMERA_POSITION_UNCHANGED", "The WebGL camera did not move to the requested object.", summary);
        }
        return flySuccess(mergeObjects(summary, { range: entityRange }), "viewer.flyTo");
      }
      if (target.longitude !== undefined && target.latitude !== undefined) {
        return await flyToCoordinates([target.longitude, target.latitude, Math.max(0, finiteNumber(target.height) || finiteNumber(payload.height) || 1000)], payload, summary);
      }
      if (target.coordinates) {
        return await flyToCoordinates(target.coordinates, payload, summary);
      }
      return failureResult(
        "OBJECT_HAS_NO_VALID_SPATIAL_TARGET",
        "The object has no valid position or bounding volume.",
        summary
      );
    }

    function cameraCoordinates(camera, Cesium) {
      var position = camera && camera.positionCartographic;
      var math = Cesium && Cesium.Math;
      if (!position || !math || typeof math.toDegrees !== "function") return null;
      return {
        longitude: Number(math.toDegrees(position.longitude)),
        latitude: Number(math.toDegrees(position.latitude)),
        height: Number(position.height)
      };
    }

    function cameraCartesianSnapshot(camera) {
      var value = camera && (camera.positionWC || camera.position);
      var x = value && Number(value.x);
      var y = value && Number(value.y);
      var z = value && Number(value.z);
      return isFinite(x) && isFinite(y) && isFinite(z) ? { x: x, y: y, z: z } : null;
    }

    function cameraCartesianChanged(before, after) {
      if (!before || !after) return true;
      var dx = after.x - before.x;
      var dy = after.y - before.y;
      var dz = after.z - before.z;
      return dx * dx + dy * dy + dz * dz > 0.25;
    }

    function cameraReachedTarget(actual, longitude, latitude) {
      if (!actual || !isFinite(actual.longitude) || !isFinite(actual.latitude)) return false;
      return Math.abs(actual.longitude - longitude) <= 0.25 && Math.abs(actual.latitude - latitude) <= 0.25;
    }

    function resolvedObjectSummary(resolved, target) {
      var fallback = summarizeObjectTarget(target, resolved && resolved.source);
      return {
        object_id: resolved && resolved.object_id || fallback.object_id || null,
        runtime_id: resolved && resolved.runtime_id || target && target.id || null,
        business_id: resolved && resolved.business_id || null,
        business_id_source: resolved && resolved.business_id_source || null,
        object_type: resolved && resolved.object_type || fallback.object_type || null,
        business_type: resolved && resolved.business_type || null,
        source: resolved && resolved.source || fallback.source || null,
        source_ref: resolved && resolved.source_ref || null,
        data_source_name: resolved && resolved.data_source_name || null,
        data_source_index: resolved && resolved.data_source_index !== undefined ? resolved.data_source_index : null,
        stable_id: resolved && resolved.stable_id !== undefined ? !!resolved.stable_id : null,
        id_scope: resolved && resolved.id_scope || null,
        resolver: resolved && resolved.resolver || null,
        bounding_sphere_radius: resolved && resolved.bounding_sphere_radius !== undefined ? resolved.bounding_sphere_radius : null,
        fly_target_type: resolved && resolved.fly_target_type || getFlyTargetType(target, resolved && resolved.position, "entity")
      };
    }

    function resolveFlyRange(payload, boundingSphere) {
      payload = payload || {};
      var explicitRange = finiteNumber(pickFirst(payload.range, payload.height_offset));
      if (explicitRange !== null && explicitRange > 0) return Math.max(explicitRange, 100);
      var radius = boundingSphere && finiteNumber(boundingSphere.radius);
      if (radius !== null && radius > 0) return Math.max(radius * 2.5, 100);
      return 100;
    }

    function buildFlyToOptions(payload, range) {
      var options = {
        duration: Number(payload.duration || payload.duration_s || payload.duration_ms && Number(payload.duration_ms) / 1000 || 1.5)
      };
      var offset = buildHeadingPitchRange(range);
      if (offset) options.offset = offset;
      return options;
    }

    function buildHeadingPitchRange(range) {
      var Cesium = global.Cesium || {};
      if (Cesium.HeadingPitchRange) {
        return new Cesium.HeadingPitchRange(0, -0.5, range);
      }
      return { heading: 0, pitch: -0.5, range: range };
    }

    function setLayerVisibilityByManager(payload) {
      var manager = getLayerManager();
      if (!manager || !payload.layer_id) return false;
      if (typeof manager.getLayerById === "function") {
        return setLayerVisible(manager.getLayerById(payload.layer_id), payload.visible);
      }
      if (typeof manager.getLayer === "function") {
        return setLayerVisible(manager.getLayer(payload.layer_id), payload.visible);
      }
      var layers = ensureArray(manager.layers || manager._layers || manager.layerList || manager._layerList);
      for (var i = 0; i < layers.length; i += 1) {
        if (matchesLayer(layers[i], payload.layer_id)) {
          return setLayerVisible(layers[i], payload.visible);
        }
      }
      return false;
    }

    function collectLayerList() {
      var items = [];
      var manager = getLayerManager();
      var candidates = manager && (manager.layers || manager._layers || manager.layerList || manager._layerList);
      ensureArray(candidates).forEach(function (layer) {
        if (!layer) return;
        items.push(layerSummary(layer, "window.LayerManager"));
      });
      var viewer = getViewer();
      collectLayerCollection(viewer && viewer.imageryLayers, "viewer.imageryLayers", items);
      collectLayerCollection(viewer && viewer.dataSources, "viewer.dataSources", items);
      return { items: dedupeLayers(items), fallback_used: !candidates };
    }

    function collectLayerTree() {
      var items = [];
      var manager = getLayerManager();
      var candidates = manager && (manager.layers || manager._layers || manager.layerList || manager._layerList || manager.children);
      appendLayerTreeItems(candidates, "window.LayerManager", items);
      var viewer = getViewer();
      collectLayerCollection(viewer && viewer.imageryLayers, "viewer.imageryLayers", items);
      collectLayerCollection(viewer && viewer.dataSources, "viewer.dataSources", items);
      return dedupeLayers(items);
    }

    function collectPanelState() {
      var knownPanels = [
        { id: "layer_tree", name: "图层管理", source: "ApplicationVue menu-bar showLayerTree" },
        { id: "atmosphere", name: "自然天气", source: "ApplicationVue menu-bar showAtmosphere" },
        { id: "visualization", name: "可视化", source: "ApplicationVue menu-bar showVisualization" },
        { id: "measure_analyze", name: "测量分析工具", source: "ApplicationVue menu-bar showMeasureAnalyze" },
        { id: "draw_tool", name: "新增标绘", source: "ApplicationVue menu-bar showDrawTool" },
        { id: "particle_system", name: "粒子系统", source: "ApplicationVue menu-bar particleSystem" },
        { id: "video_fusion", name: "新增视频融合", source: "ApplicationVue menu-bar showVideoFusion" },
        { id: "other_tools", name: "工具", source: "ApplicationVue menu-bar showOtherTools" },
        { id: "scene_setting", name: "基本属性", source: "ApplicationVue menu-bar showSceneSetting" },
        { id: "agent_debug_panel", name: "EV代理", source: "frontend/webgl_agent_bootstrap.js" },
        { id: "open_panel_basic", name: "open_panel_basic", source: "frontend/webgl_agent_bootstrap.js" }
      ];
      var domPanels = collectDomPanels();
      return {
        source: domPanels.length ? "webgl_dom" : "partial",
        reason: domPanels.length ? null : "ApplicationVue panel boolean state is inside the Vue bundle closure unless an official source anchor is exposed.",
        known_panels: knownPanels.map(function (panel) {
          var domPanel = findDomPanelByName(domPanels, panel.name, panel.id);
          return mergeObjects(panel, {
            visible: domPanel ? domPanel.visible : undefined,
            dom_ref: domPanel ? domPanel.dom_ref : undefined,
            can_open_safely: panel.id === "agent_debug_panel" || panel.id === "open_panel_basic"
          });
        }),
        dom_panels: domPanels
      };
    }

    function collectDomPanels() {
      if (!global.document || typeof global.document.querySelectorAll !== "function") return [];
      var selectors = [
        ".ant-modal",
        ".ant-drawer",
        "#ev-agent-debug-panel",
        "#ev-agent-open-panel-basic"
      ];
      var nodes = [];
      selectors.forEach(function (selector) {
        var nodeList = global.document.querySelectorAll(selector);
        for (var i = 0; i < nodeList.length; i += 1) {
          if (nodes.indexOf(nodeList[i]) < 0) nodes.push(nodeList[i]);
        }
      });
      return nodes.map(function (node, index) {
        var titleNode = node.querySelector && node.querySelector(".ant-modal-title, .ant-drawer-title, [data-title]");
        var text = titleNode && titleNode.textContent ? titleNode.textContent.trim() : "";
        if (!text && node.id) text = node.id;
        return {
          id: node.id || null,
          name: text || "panel_" + index,
          visible: isElementVisible(node),
          dom_ref: node.id ? "#" + node.id : node.className || node.tagName,
          source: "document.querySelectorAll"
        };
      });
    }

    function findDomPanelByName(domPanels, name, id) {
      var normalizedName = String(name || "").toLowerCase();
      var normalizedId = String(id || "").toLowerCase();
      return ensureArray(domPanels).filter(function (panel) {
        var panelName = String(panel.name || "").toLowerCase();
        var panelId = String(panel.id || "").toLowerCase();
        return (
          panelId === normalizedId ||
          panelName.indexOf(normalizedName) >= 0 ||
          normalizedName.indexOf(panelName) >= 0
        );
      })[0] || null;
    }

    function isElementVisible(node) {
      if (!node) return false;
      var style = global.getComputedStyle ? global.getComputedStyle(node) : null;
      if (style && (style.display === "none" || style.visibility === "hidden" || style.opacity === "0")) return false;
      return node.offsetParent !== null || node.id === "ev-agent-debug-panel" || node.id === "ev-agent-open-panel-basic";
    }

    function hideElementById(id) {
      if (!global.document) return false;
      var node = global.document.getElementById(id);
      if (!node) return false;
      node.style.display = "none";
      return true;
    }

    function appendLayerTreeItems(candidates, source, items) {
      ensureArray(candidates).forEach(function (layer) {
        if (!layer) return;
        items.push(layerSummary(layer, source));
        appendLayerTreeItems(layer.children || layer.layers || layer._layers, source, items);
      });
    }

    function flattenLayerTree(value) {
      var items = [];
      appendLayerTreeItems(value, "EVWebGLSourceAnchors.getLayerTree", items);
      return dedupeLayers(items);
    }

    function collectLayerCollection(collection, source, items) {
      if (!collection) return;
      var length = Number(collection.length || 0);
      for (var i = 0; i < length; i += 1) {
        var item = typeof collection.get === "function" ? collection.get(i) : collection[i];
        if (item) items.push(layerSummary(item, source));
      }
    }

    function layerSummary(layer, source) {
      return {
        id: String(layer.id || layer.layer_id || layer.name || layer.customTag && layer.customTag.serviceName || ""),
        layer_id: String(layer.id || layer.layer_id || layer.name || layer.customTag && layer.customTag.serviceName || ""),
        name: String(layer.name || layer.id || layer.customTag && layer.customTag.serviceName || source),
        type: String(layer.type || layer.datatype || layer.layerType || layer.tileType || source),
        visible: layer.show !== false && layer.visible !== false,
        parent: layer.parent && (layer.parent.id || layer.parent.name) || layer.parent_id || null,
        children: ensureArray(layer.children || layer.layers || layer._layers).map(function (child) { return String(child && (child.id || child.layer_id || child.name) || ""); }).filter(Boolean),
        source: source
      };
    }

    function dedupeLayers(items) {
      var seen = {};
      return items.filter(function (item) {
        var key = item.source + ":" + item.id + ":" + item.name;
        if (seen[key]) return false;
        seen[key] = true;
        return true;
      });
    }

    function setLayerVisibilityByCollection(collection, payload) {
      if (!collection || !payload.layer_id) return false;
      var length = Number(collection.length || 0);
      for (var i = 0; i < length; i += 1) {
        var item = typeof collection.get === "function" ? collection.get(i) : collection[i];
        if (matchesLayer(item, payload.layer_id)) {
          return setLayerVisible(item, payload.visible);
        }
      }
      return false;
    }

    function setLayerVisible(layer, visible) {
      if (!layer) return false;
      layer.show = visible !== false;
      layer.visible = visible !== false;
      return true;
    }

    function handleCreateBuffer(payload) { return createBuffer(unwrapPayload(payload)); }
    function handleGetBufferState() { return getBufferState(); }
    function handleGetBufferResult(payload) { return getBufferResult(unwrapPayload(payload)); }
    function handleFlyToBuffer(payload) { return flyToBuffer(unwrapPayload(payload)); }
    function handleClearBuffer(payload) { return clearBuffer(unwrapPayload(payload)); }
    function handleClearAllBuffers() { return clearAllBuffers(); }
    function handleQueryObjectsInBuffer(payload) { return queryObjectsInBuffer(unwrapPayload(payload)); }
    function handleHighlightBufferQueryResults(payload) { return highlightBufferQueryResults(unwrapPayload(payload)); }
    function handleClearBufferQueryHighlight(payload) { return clearBufferQueryHighlight(unwrapPayload(payload)); }

    async function createBuffer(payload) {
      payload = payload || {};
      var viewer = getViewer();
      if (!viewer) return bufferError("VIEWER_NOT_READY", "WebGL viewer is not ready.");
      var Cesium = global.Cesium;
      if (!Cesium || typeof Cesium.EV_DrawBuffer !== "function") return bufferError("BUFFER_SOURCE_UNAVAILABLE", "Cesium.EV_DrawBuffer is unavailable.");
      var distance = Number(payload.distance_m || 0);
      if (!isFinite(distance) || distance <= 0) return bufferError("BUFFER_DISTANCE_INVALID", "Buffer distance must be greater than zero.");
      if (distance > 1000000) return bufferError("BUFFER_DISTANCE_OUT_OF_RANGE", "Buffer distance exceeds the one-million-meter safety limit.");
      var target = await resolveBufferTarget(payload.target || payload);
      if (!target.success) return target;
      if (!target.geometry_available) return bufferError("BUFFER_GEOMETRY_UNAVAILABLE", "The target has no usable geometry or center.", { target: target });
      var bufferId = "ev-buffer-" + Date.now() + "-" + (nextBufferSequence++);
      var drawTool;
      try {
        var entitiesBefore = viewer.entities && viewer.entities.values ? viewer.entities.values.slice() : [];
        drawTool = new Cesium.EV_DrawBuffer(viewer, {
          units: "kilometers",
          material: Cesium.Color && Cesium.Color.CYAN ? Cesium.Color.CYAN.withAlpha(0.3) : undefined,
          width: 2
        });
        var nativeResult = await invokeNativeBuffer(drawTool, target, distance / 1000);
        var geometry = normalizeBufferGeometry(nativeResult);
        var nativeEntities = viewer.entities && viewer.entities.values ? viewer.entities.values.filter(function (entity) { return entitiesBefore.indexOf(entity) < 0; }) : [];
        nativeEntities.forEach(function (entity) { if (viewer.entities && typeof viewer.entities.remove === "function") viewer.entities.remove(entity); });
        var renderedEntities = renderAgentBufferEntity(viewer, Cesium, bufferId, target, distance, geometry);
        var visibility = validateRenderedBuffer(viewer, renderedEntities.fill, geometry);
        if (!visibility.visible) throw new Error("Buffer entity failed visibility validation: " + visibility.reason);
        var record = {
          buffer_id: bufferId,
          owner: "ev-agent-buffer",
          target: publicBufferTarget(target),
          distance_m: distance,
          display_value: payload.display_value || (distance >= 1000 ? (distance / 1000) + " km" : distance + " m"),
          input_geometry_type: target.geometry_type,
          buffer_geometry_type: geometry.type || "Polygon",
          center_only: target.center_only === true,
          calculation_method: "Cesium.EV_DrawBuffer/Turf geodesic buffer",
          calculation_crs: "WGS84 GeoJSON (EPSG:4326)",
          vertex_count: countGeometryVertices(geometry),
          rendered: visibility.visible,
          visibility: visibility,
          render_source: "Agent-owned Cesium Entity from EV_DrawBuffer geometry callback",
          geometry: geometry,
          source: target.source,
          status: "rendered",
          created_at: new Date().toISOString(),
          cleared_at: null,
          _drawTool: drawTool,
          _entity: renderedEntities.fill,
          _outlineEntity: renderedEntities.outline
        };
        bufferRegistry[bufferId] = record;
        bufferOrder.push(bufferId);
        while (bufferOrder.length > 20) clearBuffer({ buffer_id: bufferOrder[0] });
        var result = publicBufferRecord(record, "BUFFER_CREATED");
        if (payload.zoom_to_result === true) result.fly_to_result = flyToBuffer({ buffer_id: bufferId, duration: payload.duration });
        else result.visibility_hint = bufferVisibilityHint(record);
        return result;
      } catch (error) {
        if (drawTool && typeof drawTool.removeDrawTool === "function") try { drawTool.removeDrawTool(); } catch (ignore) {}
        return bufferError("BUFFER_CALCULATION_FAILED", error && error.message || String(error), { target: publicBufferTarget(target), distance_m: distance });
      }
    }

    async function resolveBufferTarget(spec) {
      spec = spec || {};
      var lon = finiteNumber(spec.longitude);
      var lat = finiteNumber(spec.latitude);
      if (lon !== null && lat !== null) return bufferPointTarget(lon, lat, spec.height, "explicit_coordinates", null, "显式坐标");
      rebuildSpatialObjectGeometryIndex();
      var resolved = null;
      var indexed = findIndexedBufferTarget(spec.object_id, spec.object_name);
      if (indexed && indexed.ambiguous) return bufferError("BUFFER_TARGET_AMBIGUOUS", "Multiple buffer targets matched.", { candidates: indexed.candidates });
      if (indexed && indexed.record && indexed.record.geometry_confidence === "exact") return bufferTargetFromGeometryRecord(indexed.record, "SpatialObjectGeometryIndex");
      if (spec.object_id) resolved = resolveSpatialObject(spec.object_id);
      if (!resolved && spec.object_name) {
        var matches = findBusinessObjectsByName(spec.object_name, { exact: false });
        if (matches.length > 1) return bufferError("BUFFER_TARGET_AMBIGUOUS", "Multiple buffer targets matched.", { candidates: matches.map(publicSpatialObject) });
        if (matches.length === 1) resolved = resolveSpatialObject(matches[0].object_id);
        if (!resolved && !spec.admin_region_name && !spec.adcode) return bufferError("BUFFER_TARGET_NOT_FOUND", "Buffer target was not found.");
      }
      if (spec.admin_region_name || spec.adcode) {
        var admin = await resolveAdminRegion({ name: spec.admin_region_name, adcode: spec.adcode });
        if (!admin.success) return bufferError(admin.code || "BUFFER_TARGET_NOT_FOUND", admin.error || "Administrative region was not found.", admin);
        var region = admin.matched_region || admin.region;
        var adminIndexed = findIndexedBufferTarget(region && (region.boundary_object_id || region.region_id), region && region.name);
        if (adminIndexed && adminIndexed.record && adminIndexed.record.geometry_confidence === "exact") return bufferTargetFromGeometryRecord(adminIndexed.record, "SpatialObjectGeometryIndex");
        var providerBoundary = await loadAdminBoundaryBufferTarget(region);
        if (providerBoundary) return providerBoundary;
        if (region.boundary_object_id) resolved = resolveSpatialObject(region.boundary_object_id);
        if (!resolved && region.center) return bufferPointTarget(region.center.longitude, region.center.latitude, region.center.height, "admin_region_center", region.region_id, region.name, true, "derived_center");
      }
      if (!resolved && indexed && indexed.record) return bufferTargetFromGeometryRecord(indexed.record, "SpatialObjectGeometryIndex");
      var contextCandidates = {
        selected_object: { value: global.__EV_AGENT_SELECTED_OBJECT__ || global.currentSelectedObject, source: "selected_object" },
        last_context_object: { value: global.__EV_AGENT_CURRENT_OBJECT__, source: "last_context_object" },
        last_admin_region: { value: global.__EV_AGENT_LAST_ADMIN_REGION__, source: "last_admin_region" },
        test_object: { value: global.__EV_AGENT_TEST_OBJECT__, source: "test_object" }
      };
      var candidates = resolved ? [{ value: resolved.target || resolved, source: "explicit_object" }] : spec.context && contextCandidates[spec.context] ? [contextCandidates[spec.context]] : [
        { value: global.__EV_AGENT_SELECTED_OBJECT__ || global.currentSelectedObject, source: "selected_object" },
        { value: global.__EV_AGENT_CURRENT_OBJECT__, source: "last_context_object" },
        { value: global.__EV_AGENT_LAST_ADMIN_REGION__, source: "last_admin_region" },
        { value: global.__EV_AGENT_TEST_OBJECT__, source: "test_object" }
      ];
      for (var i = 0; i < candidates.length; i += 1) {
        var candidateValue = candidates[i].value;
        var candidateIndexed = findIndexedBufferTarget(candidateValue && (candidateValue.object_id || candidateValue.id), candidateValue && (candidateValue.name || candidateValue.object_name));
        if (candidateIndexed && candidateIndexed.record && candidateIndexed.record.geometry_confidence === "exact") return bufferTargetFromGeometryRecord(candidateIndexed.record, "SpatialObjectGeometryIndex");
        var target = bufferTargetFromValue(candidates[i].value, candidates[i].source);
        if (target) return target;
      }
      return bufferError("BUFFER_TARGET_REQUIRED", "Please select, locate, or explicitly identify a buffer target.");
    }

    function findIndexedBufferTarget(objectId, objectName) {
      var records = Object.keys(spatialGeometryIndex).map(function (id) { return spatialGeometryIndex[id]; });
      if (objectId && spatialGeometryIndex[String(objectId)]) return { record: spatialGeometryIndex[String(objectId)] };
      if (!objectName) return null;
      var wanted = String(objectName).replace(/\s+/g, "").toLowerCase();
      var matches = records.filter(function (record) {
        var names = [record.name].concat(record.aliases || []).filter(Boolean).map(function (value) { return String(value).replace(/\s+/g, "").toLowerCase(); });
        return names.indexOf(wanted) >= 0;
      });
      if (matches.length === 1) return { record: matches[0] };
      if (matches.length > 1) return { ambiguous: true, candidates: matches.map(publicGeometryRecord) };
      return null;
    }

    function bufferTargetFromGeometryRecord(record, source) {
      var geometry = record && record.geometry;
      if (!geometry || !Array.isArray(geometry.coordinates)) return null;
      var coords;
      if (geometry.type === "Point") coords = [{ longitude: Number(geometry.coordinates[0]), latitude: Number(geometry.coordinates[1]), height: Number(record.center && record.center.height || 0) }];
      else {
        var points = geometry.type === "LineString" ? geometry.coordinates : geometryPoints(geometry);
        coords = points.map(function (point) { return { longitude: Number(point[0]), latitude: Number(point[1]), height: 0 }; });
      }
      return { success: true, target_id: record.object_id, target_name: record.name, target_type: record.object_type, geometry_type: geometry.type === "MultiPolygon" ? "Polygon" : geometry.type, coordinates: coords, center: record.center || geometryCenter(geometry), source: source, geometry_available: true, center_only: record.geometry_confidence !== "exact", geometry_confidence: record.geometry_confidence, matched_by: "spatial_geometry_index" };
    }

    async function loadAdminBoundaryBufferTarget(region) {
      if (!region || !region.region_id || !global.fetch) return null;
      try {
        var base = options.agentBaseUrl || resolveAgentBaseUrl();
        var response = await global.fetch(base + "/api/admin-regions/" + encodeURIComponent(region.region_id) + "/boundary");
        if (!response.ok) return null;
        var feature = await response.json();
        var geometry = feature && (feature.geometry || feature);
        if (!geometry || ["Polygon", "MultiPolygon"].indexOf(geometry.type) < 0) return null;
        return bufferTargetFromGeometryRecord({ object_id: region.boundary_object_id || region.region_id, name: region.name, object_type: region.level, geometry: geometry, center: geometryCenter(geometry), geometry_confidence: "exact" }, "AdminRegionProvider.boundary");
      } catch (ignore) { return null; }
    }

    function bufferTargetFromValue(value, source) {
      if (!value) return null;
      var raw = value.raw || value.entity || value;
      var time = global.Cesium && global.Cesium.JulianDate && global.Cesium.JulianDate.now ? global.Cesium.JulianDate.now() : undefined;
      function read(prop) { try { return prop && typeof prop.getValue === "function" ? prop.getValue(time) : prop; } catch (error) { return prop; } }
      var polygon = raw.polygon && read(raw.polygon.hierarchy);
      var polygonPositions = polygon && (polygon.positions || polygon);
      if (Array.isArray(polygonPositions) && polygonPositions.length >= 3) return bufferCartesianTarget(polygonPositions, "Polygon", source, value);
      var linePositions = raw.polyline && read(raw.polyline.positions);
      if (Array.isArray(linePositions) && linePositions.length >= 2) return bufferCartesianTarget(linePositions, "LineString", source, value);
      var position = read(raw.position) || value.position;
      var cartographic = cartographicFromValue(position);
      if (cartographic) return bufferPointTarget(cartographic.longitude, cartographic.latitude, cartographic.height, source, value.object_id || value.id, value.name || value.object_name);
      var center = centerFromValue(value, source, "center_only");
      if (center) return bufferPointTarget(center.longitude, center.latitude, center.height, center.source || source, center.anchor_id, center.anchor_name, true);
      return null;
    }

    function bufferCartesianTarget(positions, geometryType, source, value) {
      var coords = positions.map(cartographicFromValue).filter(Boolean);
      if (coords.length < (geometryType === "Polygon" ? 3 : 2)) return null;
      return { success: true, target_id: value.object_id || value.id || null, target_name: value.name || value.object_name || null, target_type: value.object_type || null, geometry_type: geometryType, coordinates: coords, source: source, geometry_available: true, center_only: false, geometry_confidence: "exact", matched_by: source };
    }

    function bufferPointTarget(lon, lat, height, source, id, name, centerOnly, confidence) {
      return { success: true, target_id: id || null, target_name: name || null, target_type: "point", geometry_type: "Point", coordinates: [{ longitude: Number(lon), latitude: Number(lat), height: Number(height || 0) }], center: { longitude: Number(lon), latitude: Number(lat), height: Number(height || 0) }, source: source, geometry_available: true, center_only: centerOnly === true, geometry_confidence: confidence || (centerOnly ? (String(source).indexOf("bounding") >= 0 ? "bounding_only" : "derived_center") : "exact"), matched_by: source };
    }

    function cartographicFromValue(value) {
      if (!value) return null;
      var lon = finiteNumber(value.longitude), lat = finiteNumber(value.latitude);
      if (lon !== null && lat !== null && Math.abs(lon) <= 180 && Math.abs(lat) <= 90) return { longitude: lon, latitude: lat, height: finiteNumber(value.height) || 0 };
      var Cesium = global.Cesium;
      if (Cesium && Cesium.Cartographic && typeof Cesium.Cartographic.fromCartesian === "function" && isFinite(Number(value.x)) && isFinite(Number(value.y)) && isFinite(Number(value.z))) {
        try { var c = Cesium.Cartographic.fromCartesian(value); return { longitude: Cesium.Math.toDegrees(c.longitude), latitude: Cesium.Math.toDegrees(c.latitude), height: c.height || 0 }; } catch (error) {}
      }
      return null;
    }

    function invokeNativeBuffer(tool, target, radiusKm) {
      return new Promise(function (resolve, reject) {
        var finished = false;
        var timer = setTimeout(function () { if (!finished) { finished = true; reject(new Error("Native buffer callback timed out.")); } }, 10000);
        function done(result) { if (finished) return; finished = true; clearTimeout(timer); resolve(result || {}); }
        try {
          var values = target.coordinates.map(function (c) { return global.Cesium.Cartographic.fromDegrees(c.longitude, c.latitude, c.height || 0); });
          if (target.geometry_type === "Point") tool.pointBuffer(radiusKm, values[0], done);
          else if (target.geometry_type === "LineString") tool.lineBuffer(radiusKm, values, done);
          else tool.polygonBuffer(radiusKm, values, done);
        } catch (error) { clearTimeout(timer); reject(error); }
      });
    }

    function normalizeBufferGeometry(result) {
      var geometry = result && (result.geometry || result.buffer || result.geojson || result);
      if (geometry && geometry.type === "Feature") geometry = geometry.geometry;
      if (!geometry || !geometry.type || !geometry.coordinates) return { type: "Polygon", coordinates: [] };
      return { type: geometry.type, coordinates: JSON.parse(JSON.stringify(geometry.coordinates)) };
    }
    function renderAgentBufferEntity(viewer, Cesium, bufferId, target, distance, geometry) {
      if (!viewer.entities || typeof viewer.entities.add !== "function" || !Cesium.Cartesian3 || typeof Cesium.Cartesian3.fromDegreesArray !== "function") return null;
      var rings = geometry && geometry.coordinates || [];
      while (Array.isArray(rings) && rings.length && Array.isArray(rings[0]) && Array.isArray(rings[0][0])) rings = rings[0];
      if (!Array.isArray(rings) || rings.length < 3) throw new Error("Native buffer returned no renderable polygon ring.");
      var flat = [];
      rings.forEach(function (point) { if (Array.isArray(point) && point.length >= 2) { flat.push(Number(point[0])); flat.push(Number(point[1])); } });
      var heightOffset = 30;
      var hierarchy = Cesium.Cartesian3.fromDegreesArrayHeights ? Cesium.Cartesian3.fromDegreesArrayHeights(rings.reduce(function (values, point) { values.push(Number(point[0]), Number(point[1]), heightOffset); return values; }, [])) : Cesium.Cartesian3.fromDegreesArray(flat);
      var fill = viewer.entities.add({
        id: bufferId,
        name: "EV Agent Buffer",
        show: true,
        properties: { owner: "ev-agent-buffer", buffer_id: bufferId, target_id: target.target_id || null, distance_m: distance, calculation_method: "Cesium.EV_DrawBuffer/Turf" },
        polygon: {
          show: true,
          hierarchy: hierarchy,
          material: Cesium.Color && Cesium.Color.CYAN ? Cesium.Color.CYAN.withAlpha(0.38) : "rgba(0,255,255,.38)",
          outline: false,
          perPositionHeight: !!Cesium.Cartesian3.fromDegreesArrayHeights,
          zIndex: 1000,
          classificationType: Cesium.ClassificationType && Cesium.ClassificationType.BOTH
        }
      });
      var closedFlat = flat.slice();
      if (rings.length && (rings[0][0] !== rings[rings.length - 1][0] || rings[0][1] !== rings[rings.length - 1][1])) closedFlat.push(Number(rings[0][0]), Number(rings[0][1]));
      var outlinePositions = Cesium.Cartesian3.fromDegreesArrayHeights ? Cesium.Cartesian3.fromDegreesArrayHeights(closedFlat.reduce(function (values, value, index) { if (index % 2 === 0) values.push(value, closedFlat[index + 1], heightOffset + 2); return values; }, [])) : Cesium.Cartesian3.fromDegreesArray(closedFlat);
      var outline = viewer.entities.add({ id: bufferId + "-outline", name: "EV Agent Buffer Outline", show: true, properties: { owner: "ev-agent-buffer", buffer_id: bufferId, role: "outline" }, polyline: { show: true, positions: outlinePositions, width: 4, clampToGround: false, material: Cesium.Color && (Cesium.Color.YELLOW || Cesium.Color.CYAN) || "yellow" } });
      return { fill: fill, outline: outline };
    }
    function validateRenderedBuffer(viewer, entity, geometry) {
      var hierarchy = entity && entity.polygon && getPropertyValue(entity.polygon.hierarchy);
      var exists = !!(entity && viewer.entities && ensureArray(viewer.entities.values).indexOf(entity) >= 0);
      var bbox = geometryBbox(geometry);
      var validBbox = !!(bbox && bbox.every(function (value) { return isFinite(Number(value)); }) && bbox[0] <= bbox[2] && bbox[1] <= bbox[3]);
      var visible = !!(exists && entity.show !== false && entity.polygon && entity.polygon.show !== false && hierarchy && entity.polygon.material && validBbox);
      return { visible: visible, entity_present: exists, entity_show: !!(entity && entity.show !== false), polygon_show: !!(entity && entity.polygon && entity.polygon.show !== false), hierarchy_available: !!hierarchy, material_available: !!(entity && entity.polygon && entity.polygon.material), bbox: bbox, bbox_valid: validBbox, reason: visible ? null : "ENTITY_VISIBILITY_CHECK_FAILED" };
    }
    function bufferVisibilityHint(record) {
      var viewer = getViewer(); var bbox = geometryBbox(record.geometry); var cameraHeight = finiteNumber(viewer && viewer.camera && viewer.camera.positionCartographic && viewer.camera.positionCartographic.height);
      if (!bbox || cameraHeight === null) return null;
      var widthM = Math.max(Math.abs(bbox[2] - bbox[0]) * 111320 * Math.cos((bbox[1] + bbox[3]) / 2 * Math.PI / 180), Math.abs(bbox[3] - bbox[1]) * 111320);
      return cameraHeight > Math.max(5000, widthM * 50) ? "缓冲区已生成，但当前视角比例较小。可执行‘查看刚才的缓冲区’定位到结果。" : null;
    }
    function countGeometryVertices(geometry) { var count = 0; (function walk(v) { if (!Array.isArray(v)) return; if (v.length >= 2 && typeof v[0] === "number" && typeof v[1] === "number") count += 1; else v.forEach(walk); })(geometry && geometry.coordinates); return count; }
    function publicBufferTarget(target) { return { id: target.target_id || null, name: target.target_name || null, type: target.target_type || null, source: target.source, geometry_type: target.geometry_type, geometry_confidence: target.geometry_confidence || null, center: target.center || null, center_only: target.center_only === true, matched_by: target.matched_by }; }
    function publicBufferRecord(record, code) { return { success: true, code: code || record.status, buffer_id: record.buffer_id, target: record.target, distance_m: record.distance_m, display_value: record.display_value, input_geometry_type: record.input_geometry_type, buffer_geometry_type: record.buffer_geometry_type, center_only: record.center_only, calculation_method: record.calculation_method, calculation_crs: record.calculation_crs, vertex_count: record.vertex_count, rendered: record.rendered, visibility: record.visibility || null, render_source: record.render_source, source: record.source, status: record.status, created_at: record.created_at, cleared_at: record.cleared_at }; }
    function bufferError(code, message, extra) { var result = { success: false, code: code, error_code: code, error: message }; Object.keys(extra || {}).forEach(function (key) { result[key] = extra[key]; }); return result; }
    function getBufferState() { var active = bufferOrder.filter(function (id) { return bufferRegistry[id] && bufferRegistry[id].status === "rendered"; }); return { success: true, code: "SUCCESS", state: active.length ? "rendered" : "idle", active_buffer_id: active.length ? active[active.length - 1] : null, buffer_count: active.length, buffers: active.map(function (id) { return publicBufferRecord(bufferRegistry[id]); }) }; }
    function getBufferResult(payload) { var id = payload && payload.buffer_id; if (!id || id === "latest") id = bufferOrder.length ? bufferOrder[bufferOrder.length - 1] : null; var record = id && bufferRegistry[id]; return record ? publicBufferRecord(record, "SUCCESS") : bufferError("BUFFER_NOT_FOUND", "No matching buffer exists."); }
    function flyToBuffer(payload) { var id = payload && payload.buffer_id; if (!id || id === "latest") id = bufferOrder.length ? bufferOrder[bufferOrder.length - 1] : null; var record = id && bufferRegistry[id]; if (!record || record.status !== "rendered") return bufferError("BUFFER_NOT_FOUND", "No rendered buffer is available to view."); var viewer = getViewer(); if (!viewer) return bufferError("VIEWER_NOT_READY", "WebGL viewer is not ready."); try { if (typeof viewer.flyTo === "function") { var promise = viewer.flyTo(record._entity, { duration: Number(payload && payload.duration || 1.5) }); return { success: true, code: "BUFFER_FLY_COMPLETED", buffer_id: id, fly_completed: true, fly_method: "viewer.flyTo(buffer_entity)", pending: !!(promise && typeof promise.then === "function") }; } var bbox = geometryBbox(record.geometry); if (!bbox || !viewer.camera || typeof viewer.camera.flyTo !== "function") return bufferError("BUFFER_RENDER_FAILED", "Camera cannot fly to the buffer result."); var centerLon = (bbox[0] + bbox[2]) / 2, centerLat = (bbox[1] + bbox[3]) / 2; var spanM = Math.max(Math.abs(bbox[2] - bbox[0]) * 111320, Math.abs(bbox[3] - bbox[1]) * 111320); viewer.camera.flyTo({ destination: global.Cesium.Cartesian3.fromDegrees(centerLon, centerLat, Math.max(2000, spanM * 2)), duration: Number(payload && payload.duration || 1.5) }); return { success: true, code: "BUFFER_FLY_COMPLETED", buffer_id: id, fly_completed: true, fly_method: "camera.flyTo(buffer_bbox)" }; } catch (error) { return bufferError("BUFFER_RENDER_FAILED", error.message || String(error)); } }
    function clearBuffer(payload) { var id = payload && payload.buffer_id; if (!id || id === "latest") id = bufferOrder.length ? bufferOrder[bufferOrder.length - 1] : null; var record = id && bufferRegistry[id]; if (!record) return bufferError("BUFFER_NOT_FOUND", "No matching buffer exists."); try { var viewer = getViewer(); if (record._entity && viewer && viewer.entities && typeof viewer.entities.remove === "function") viewer.entities.remove(record._entity); if (record._outlineEntity && viewer && viewer.entities && typeof viewer.entities.remove === "function") viewer.entities.remove(record._outlineEntity); if (record._drawTool && typeof record._drawTool.removeDrawTool === "function") record._drawTool.removeDrawTool(); } catch (error) { return bufferError("BUFFER_RENDER_FAILED", error.message || String(error)); } record.status = "cleared"; record.rendered = false; record.cleared_at = new Date().toISOString(); bufferOrder = bufferOrder.filter(function (item) { return item !== id; }); return publicBufferRecord(record, "BUFFER_CLEARED"); }
    function clearAllBuffers() { var ids = bufferOrder.slice(); var cleared = 0; ids.forEach(function (id) { if (clearBuffer({ buffer_id: id }).success) cleared += 1; }); return { success: true, code: "BUFFERS_CLEARED", cleared_count: cleared, buffer_count: 0 }; }

    function getPropertyValue(value) {
      if (value && typeof value.getValue === "function") {
        try { return value.getValue(getCurrentTime()); } catch (error) { try { return value.getValue(); } catch (ignore) { return null; } }
      }
      return value;
    }

    function rebuildSpatialObjectGeometryIndex() {
      if (!getViewer()) return bufferError("VIEWER_NOT_READY", "WebGL viewer is not ready.");
      collectSpatialObjectsPayload();
      var records = {};
      var skipped = {};
      Object.keys(spatialObjectRegistry).forEach(function (id) {
        var spatial = spatialObjectRegistry[id];
        var geometryRecord = geometryRecordFromSpatialObject(spatial);
        if (!geometryRecord || isAgentOwnedRuntimeObject(spatial && spatial.target)) return;
        if (!geometryRecord.queryable) skipped[geometryRecord.skip_reason || "GEOMETRY_UNAVAILABLE"] = (skipped[geometryRecord.skip_reason || "GEOMETRY_UNAVAILABLE"] || 0) + 1;
        records[id] = geometryRecord;
      });
      spatialGeometryIndex = records;
      spatialGeometryIndexBuiltAt = new Date().toISOString();
      return getSpatialObjectGeometryIndex(skipped);
    }

    function getSpatialObjectGeometryIndex(skippedReasons) {
      var objects = Object.keys(spatialGeometryIndex).map(function (id) { return spatialGeometryIndex[id]; });
      return {
        success: true,
        code: objects.length ? "SUCCESS" : "SPATIAL_OBJECT_INDEX_EMPTY",
        built_at: spatialGeometryIndexBuiltAt,
        total_count: objects.length,
        queryable_count: objects.filter(function (item) { return item.queryable; }).length,
        exact_geometry_count: objects.filter(function (item) { return item.geometry_confidence === "exact"; }).length,
        derived_center_count: objects.filter(function (item) { return item.geometry_confidence === "derived_center" || item.geometry_confidence === "bounding_only"; }).length,
        skipped_reasons: skippedReasons || summarizeGeometrySkips(objects),
        objects: objects.map(publicGeometryRecord)
      };
    }

    function geometryRecordFromSpatialObject(spatial) {
      if (!spatial || !spatial.object_id) return null;
      var target = spatial.target || spatial.runtime_object || spatial;
      var geometry = extractRuntimeGeometry(target, spatial);
      var confidence = geometry.confidence;
      var bbox = geometry.geometry ? geometryBbox(geometry.geometry) : null;
      var center = geometry.center || geometryCenter(geometry.geometry);
      return {
        object_id: String(spatial.object_id),
        name: spatial.object_name || spatial.name || null,
        aliases: [],
        object_type: spatial.business_type || spatial.object_type || "unknown",
        layer_id: spatial.layer_id || null,
        layer_name: spatial.layer_name || spatial.data_source_name || null,
        source: spatial.source || null,
        geometry_type: geometry.geometry && geometry.geometry.type || null,
        geometry: geometry.geometry,
        center: center,
        bbox: bbox,
        bounding_sphere: spatial.bounding_sphere_valid ? { center: center, radius: spatial.bounding_sphere_radius } : null,
        geometry_confidence: confidence,
        properties: sanitizeGeometryProperties(spatial.business_properties || spatial.properties || {}),
        visible: target && target.show !== false,
        queryable: !!geometry.geometry,
        skip_reason: geometry.geometry ? null : geometry.reason || "GEOMETRY_UNAVAILABLE"
      };
    }

    function extractRuntimeGeometry(target, spatial) {
      if (!target) return { geometry: null, confidence: "unavailable", reason: "RUNTIME_OBJECT_UNAVAILABLE" };
      var direct = getPropertyValue(target.geometry || target.geojson);
      if (direct && direct.type === "Feature") direct = direct.geometry;
      if (direct && ["Point", "LineString", "Polygon", "MultiPolygon"].indexOf(direct.type) >= 0 && Array.isArray(direct.coordinates)) {
        return { geometry: cloneGeometry(direct), confidence: "exact", center: geometryCenter(direct) };
      }
      var polygon = target.polygon && getPropertyValue(target.polygon.hierarchy);
      var polygonPositions = polygon && (polygon.positions || polygon);
      if (Array.isArray(polygonPositions) && polygonPositions.length >= 3) {
        var polygonCoords = polygonPositions.map(cartographicFromValue).filter(Boolean).map(lonLatPair);
        if (polygonCoords.length >= 3) { closeRing(polygonCoords); return { geometry: { type: "Polygon", coordinates: [polygonCoords] }, confidence: "exact" }; }
      }
      var line = target.polyline && getPropertyValue(target.polyline.positions);
      if (Array.isArray(line) && line.length >= 2) {
        var lineCoords = line.map(cartographicFromValue).filter(Boolean).map(lonLatPair);
        if (lineCoords.length >= 2) return { geometry: { type: "LineString", coordinates: lineCoords }, confidence: "exact" };
      }
      var rectangle = target.rectangle && getPropertyValue(target.rectangle.coordinates);
      if (rectangle && [rectangle.west, rectangle.south, rectangle.east, rectangle.north].every(function (v) { return isFinite(Number(v)); })) {
        var C = global.Cesium || {}; var deg = C.Math && C.Math.toDegrees || function (v) { return Number(v) * 180 / Math.PI; };
        var west = deg(rectangle.west), south = deg(rectangle.south), east = deg(rectangle.east), north = deg(rectangle.north);
        return { geometry: { type: "Polygon", coordinates: [[[west, south], [east, south], [east, north], [west, north], [west, south]]] }, confidence: "exact" };
      }
      var ellipse = target.ellipse;
      if (ellipse) {
        var ellipseCenter = cartographicFromValue(getPropertyValue(target.position));
        var major = finiteNumber(getPropertyValue(ellipse.semiMajorAxis));
        var minor = finiteNumber(getPropertyValue(ellipse.semiMinorAxis));
        if (ellipseCenter && major && minor) return { geometry: sampledEllipseGeometry(ellipseCenter, major, minor, 64), confidence: "exact" };
      }
      var position = cartographicFromValue(getPropertyValue(target.position));
      if (position) return { geometry: { type: "Point", coordinates: lonLatPair(position) }, center: coordinateCenter(position), confidence: "exact" };
      if (spatial && spatial.position_valid && isFinite(Number(spatial.longitude)) && isFinite(Number(spatial.latitude))) {
        var basis = String(spatial.position_source || "");
        var bounding = basis.indexOf("bounding_sphere") >= 0;
        return { geometry: { type: "Point", coordinates: [Number(spatial.longitude), Number(spatial.latitude)] }, center: { longitude: Number(spatial.longitude), latitude: Number(spatial.latitude), height: Number(spatial.height || 0) }, confidence: bounding ? "bounding_only" : "derived_center" };
      }
      return { geometry: null, confidence: "unavailable", reason: spatial && spatial.position_error || "GEOMETRY_UNAVAILABLE" };
    }

    function isAgentOwnedRuntimeObject(target) {
      var properties = target && readProperties(target) || {};
      return properties.owner === "ev-agent-buffer" || properties.owner === "ev-agent-buffer-query-highlight" || target && /^ev-buffer-/.test(String(target.id || ""));
    }
    function lonLatPair(value) { return [Number(value.longitude), Number(value.latitude)]; }
    function coordinateCenter(value) { return { longitude: Number(value.longitude), latitude: Number(value.latitude), height: Number(value.height || 0) }; }
    function closeRing(ring) { if (ring.length && (ring[0][0] !== ring[ring.length - 1][0] || ring[0][1] !== ring[ring.length - 1][1])) ring.push(ring[0].slice()); return ring; }
    function cloneGeometry(geometry) { return { type: geometry.type, coordinates: JSON.parse(JSON.stringify(geometry.coordinates)) }; }
    function sanitizeGeometryProperties(properties) { var result = {}; Object.keys(properties || {}).slice(0, 100).forEach(function (key) { var value = properties[key]; if (value === null || ["string", "number", "boolean"].indexOf(typeof value) >= 0) result[key] = value; }); return result; }
    function sampledEllipseGeometry(center, majorM, minorM, steps) { var ring = []; var latScale = 111320; var lonScale = Math.max(1000, latScale * Math.cos(center.latitude * Math.PI / 180)); for (var i = 0; i <= steps; i += 1) { var angle = i / steps * Math.PI * 2; ring.push([center.longitude + Math.cos(angle) * majorM / lonScale, center.latitude + Math.sin(angle) * minorM / latScale]); } return { type: "Polygon", coordinates: [ring] }; }

    function geometryBbox(geometry) { var points = geometryPoints(geometry); if (!points.length) return null; return points.reduce(function (bbox, point) { bbox[0] = Math.min(bbox[0], point[0]); bbox[1] = Math.min(bbox[1], point[1]); bbox[2] = Math.max(bbox[2], point[0]); bbox[3] = Math.max(bbox[3], point[1]); return bbox; }, [Infinity, Infinity, -Infinity, -Infinity]); }
    function geometryPoints(geometry) { var points = []; (function walk(value) { if (!Array.isArray(value)) return; if (value.length >= 2 && isFinite(Number(value[0])) && isFinite(Number(value[1])) && !Array.isArray(value[0])) points.push([Number(value[0]), Number(value[1])]); else value.forEach(walk); })(geometry && geometry.coordinates); return points; }
    function geometryCenter(geometry) { var bbox = geometryBbox(geometry); return bbox ? { longitude: (bbox[0] + bbox[2]) / 2, latitude: (bbox[1] + bbox[3]) / 2, height: 0 } : null; }
    function bboxIntersects(a, b) { return !!(a && b && a[0] <= b[2] && a[2] >= b[0] && a[1] <= b[3] && a[3] >= b[1]); }
    function summarizeGeometrySkips(objects) { var result = {}; (objects || []).filter(function (item) { return !item.queryable; }).forEach(function (item) { var key = item.skip_reason || "GEOMETRY_UNAVAILABLE"; result[key] = (result[key] || 0) + 1; }); return result; }
    function publicGeometryRecord(record) { return JSON.parse(JSON.stringify(record)); }

    function queryObjectsInBuffer(payload) {
      payload = payload || {};
      if (!getViewer()) return bufferError("VIEWER_NOT_READY", "WebGL viewer is not ready.");
      var bufferId = payload.buffer_id;
      if (!bufferId || bufferId === "latest") bufferId = bufferOrder.length ? bufferOrder[bufferOrder.length - 1] : null;
      var buffer = bufferId && bufferRegistry[bufferId];
      if (!buffer) return bufferError("BUFFER_NOT_FOUND", "No matching buffer exists.");
      if (!buffer.rendered || buffer.status !== "rendered") return bufferError("BUFFER_NOT_RENDERED", "The requested buffer is not rendered.");
      var index = rebuildSpatialObjectGeometryIndex();
      if (!index.success || !index.total_count) return bufferError("SPATIAL_OBJECT_INDEX_EMPTY", "No runtime spatial objects were indexed.");
      var all = Object.keys(spatialGeometryIndex).map(function (id) { return spatialGeometryIndex[id]; });
      var filtered = all.filter(function (item) { return matchesGeometryFilters(item, payload); });
      var queryable = filtered.filter(function (item) { return item.queryable && item.geometry; });
      if (!filtered.length) return storeEmptyBufferQuery(bufferId, buffer, payload, 0, 0, 0, ["No runtime objects matched the supplied attribute filters."]);
      if (!queryable.length) return bufferError("NO_QUERYABLE_OBJECTS", "No filtered runtime objects have queryable geometry.", { candidate_count: filtered.length, skipped_count: filtered.length });
      var bufferBbox = geometryBbox(buffer.geometry);
      var limit = Math.max(1, Math.min(500, Number(payload.limit || 100)));
      var matchedAll = [];
      var calculationFailed = 0;
      var calculationStartedAt = Date.now();
      var calculationTimedOut = false;
      var candidateLimited = queryable.length > 5000;
      var calculationCandidates = queryable.slice(0, 5000);
      for (var candidateIndex = 0; candidateIndex < calculationCandidates.length; candidateIndex += 1) {
        if (Date.now() - calculationStartedAt > 120) { calculationTimedOut = true; break; }
        var item = calculationCandidates[candidateIndex];
        if (!bboxIntersects(bufferBbox, item.bbox)) continue;
        try { var match = geometryRelationToPolygon(item.geometry, buffer.geometry); if (match.matched) matchedAll.push(bufferQueryMatch(item, match, buffer)); } catch (error) { calculationFailed += 1; }
      }
      var queryId = "buffer-query-" + Date.now() + "-" + (nextBufferQuerySequence++);
      var limited = matchedAll.length > limit || candidateLimited || calculationTimedOut;
      var matches = matchedAll.slice(0, limit);
      var result = {
        success: true,
        code: matchedAll.length ? (limited ? "BUFFER_QUERY_RESULT_LIMITED" : "BUFFER_OBJECT_QUERY_SUCCESS") : "BUFFER_OBJECT_QUERY_EMPTY",
        query_id: queryId,
        buffer_id: bufferId,
        buffer_target: buffer.target,
        buffer_distance_m: buffer.distance_m,
        relation: payload.relation || "intersects",
        filters: { query: payload.query || null, object_type: payload.object_type || null, layer_id: payload.layer_id || null, layer_name: payload.layer_name || null, visible_only: payload.visible_only !== false },
        candidate_count: filtered.length,
        queryable_count: queryable.length,
        matched_count: matchedAll.length,
        returned_count: matches.length,
        skipped_count: filtered.length - queryable.length + calculationFailed,
        matches: matches,
        calculation_method: global.turf ? "bbox prefilter + Turf 7.3.0 boolean spatial predicates" : "bbox prefilter + built-in exact GeoJSON predicates",
        limitations: geometryQueryLimitations(filtered, matchedAll.length > limit, limit, candidateLimited, calculationTimedOut),
        created_at: new Date().toISOString(),
        status: "completed"
      };
      bufferQueryRegistry[queryId] = result;
      bufferQueryOrder.push(queryId);
      if (bufferQueryOrder.length > 100) delete bufferQueryRegistry[bufferQueryOrder.shift()];
      return result;
    }

    function storeEmptyBufferQuery(bufferId, buffer, payload, candidateCount, queryableCount, skippedCount, limitations) {
      var queryId = "buffer-query-" + Date.now() + "-" + (nextBufferQuerySequence++);
      var result = {
        success: true,
        code: "BUFFER_OBJECT_QUERY_EMPTY",
        query_id: queryId,
        buffer_id: bufferId,
        buffer_target: buffer.target,
        buffer_distance_m: buffer.distance_m,
        relation: payload.relation || "intersects",
        filters: { query: payload.query || null, object_type: payload.object_type || null, layer_id: payload.layer_id || null, layer_name: payload.layer_name || null, visible_only: payload.visible_only !== false },
        candidate_count: candidateCount,
        queryable_count: queryableCount,
        matched_count: 0,
        returned_count: 0,
        skipped_count: skippedCount,
        matches: [],
        calculation_method: global.turf ? "bbox prefilter + Turf 7.3.0 boolean spatial predicates" : "bbox prefilter + built-in exact GeoJSON predicates",
        limitations: limitations || [],
        created_at: new Date().toISOString(),
        status: "completed"
      };
      bufferQueryRegistry[queryId] = result;
      bufferQueryOrder.push(queryId);
      if (bufferQueryOrder.length > 100) delete bufferQueryRegistry[bufferQueryOrder.shift()];
      return result;
    }

    function matchesGeometryFilters(item, payload) { var keyword = String(payload.query || "").replace(/(?:查询|查找|查看|当前|刚才|缓冲区|里面|内部|其中|对象|哪些|包含|显示|高亮)/g, "").trim().toLowerCase(); if (payload.visible_only !== false && item.visible === false) return false; if (payload.object_type && String(item.object_type || "").toLowerCase().indexOf(String(payload.object_type).toLowerCase()) < 0) return false; if (payload.layer_id && String(item.layer_id || "") !== String(payload.layer_id)) return false; if (payload.layer_name && String(item.layer_name || "").toLowerCase().indexOf(String(payload.layer_name).toLowerCase()) < 0) return false; if (keyword && [item.name, item.object_type, item.layer_name].join(" ").toLowerCase().indexOf(keyword) < 0) return false; return true; }
    function bufferQueryMatch(item, relation, buffer) { var center = item.center || geometryCenter(item.geometry); var bufferCenter = geometryCenter(buffer.geometry); return { object_id: item.object_id, name: item.name, object_type: item.object_type, layer_name: item.layer_name, geometry_type: item.geometry_type, match_relation: relation.relation, match_basis: item.geometry_confidence === "bounding_only" ? "bounding_center" : item.geometry_confidence === "derived_center" ? "representative_center" : "exact_geometry", geometry_confidence: item.geometry_confidence, distance_to_buffer_center_m: center && bufferCenter ? haversineMeters(bufferCenter.longitude, bufferCenter.latitude, center.longitude, center.latitude) : null, properties_preview: item.properties }; }
    function geometryQueryLimitations(items, resultLimited, limit, candidateLimited, timedOut) { var result = []; var degraded = items.filter(function (item) { return item.geometry_confidence === "bounding_only" || item.geometry_confidence === "derived_center"; }).length; if (degraded) result.push(degraded + " object(s) use representative centers; a hit does not prove the complete object geometry intersects."); if (resultLimited) result.push("Results truncated to " + limit + "."); if (candidateLimited) result.push("Spatial calculation candidates were capped at 5000."); if (timedOut) result.push("Spatial calculation stopped after the 120 ms main-thread budget."); return result; }

    function geometryRelationToPolygon(geometry, polygon) {
      var turf = global.turf;
      if (turf && typeof turf.feature === "function") {
        var subjectFeature = turf.feature(cloneGeometry(geometry));
        var bufferFeature = turf.feature(cloneGeometry(polygon));
        if (geometry.type === "Point" && typeof turf.booleanPointInPolygon === "function") {
          return { matched: turf.booleanPointInPolygon(subjectFeature, bufferFeature, { ignoreBoundary: false }), relation: turf.booleanPointInPolygon(subjectFeature, bufferFeature, { ignoreBoundary: true }) ? "within" : "touches" };
        }
        if (typeof turf.booleanIntersects === "function") {
          var intersects = turf.booleanIntersects(subjectFeature, bufferFeature);
          var within = intersects && typeof turf.booleanWithin === "function" && turf.booleanWithin(subjectFeature, bufferFeature);
          return { matched: intersects, relation: within ? "within" : "intersects" };
        }
      }
      var polygons = polygonRings(polygon);
      if (!polygons.length) throw new Error("Invalid buffer polygon");
      if (geometry.type === "Point") return pointRelation(geometry.coordinates, polygons);
      if (geometry.type === "LineString") return lineRelation(geometry.coordinates, polygons);
      if (geometry.type === "Polygon" || geometry.type === "MultiPolygon") return polygonRelation(polygonRings(geometry), polygons);
      return { matched: false, relation: "unsupported" };
    }
    function polygonRings(geometry) { if (!geometry || !Array.isArray(geometry.coordinates)) return []; if (geometry.type === "Polygon") return geometry.coordinates.length ? [geometry.coordinates[0]] : []; if (geometry.type === "MultiPolygon") return geometry.coordinates.map(function (polygon) { return polygon && polygon[0]; }).filter(Boolean); return []; }
    function pointRelation(point, polygons) { for (var i = 0; i < polygons.length; i += 1) { var state = pointInRing(point, polygons[i]); if (state >= 0) return { matched: true, relation: state === 0 ? "touches" : "within" }; } return { matched: false, relation: "disjoint" }; }
    function pointInRing(point, ring) { var inside = false; for (var i = 0, j = ring.length - 1; i < ring.length; j = i++) { var a = ring[j], b = ring[i]; if (pointOnSegment(point, a, b)) return 0; if (((b[1] > point[1]) !== (a[1] > point[1])) && point[0] < (a[0] - b[0]) * (point[1] - b[1]) / (a[1] - b[1]) + b[0]) inside = !inside; } return inside ? 1 : -1; }
    function pointOnSegment(p, a, b) { var cross = (p[1] - a[1]) * (b[0] - a[0]) - (p[0] - a[0]) * (b[1] - a[1]); if (Math.abs(cross) > 1e-10) return false; return p[0] >= Math.min(a[0], b[0]) - 1e-10 && p[0] <= Math.max(a[0], b[0]) + 1e-10 && p[1] >= Math.min(a[1], b[1]) - 1e-10 && p[1] <= Math.max(a[1], b[1]) + 1e-10; }
    function segmentsIntersect(a, b, c, d) { function orient(p, q, r) { return (q[1] - p[1]) * (r[0] - q[0]) - (q[0] - p[0]) * (r[1] - q[1]); } var o1 = orient(a, b, c), o2 = orient(a, b, d), o3 = orient(c, d, a), o4 = orient(c, d, b); if (o1 * o2 < 0 && o3 * o4 < 0) return true; return pointOnSegment(c, a, b) || pointOnSegment(d, a, b) || pointOnSegment(a, c, d) || pointOnSegment(b, c, d); }
    function lineIntersectsRing(line, ring) { for (var i = 1; i < line.length; i += 1) for (var j = 1; j < ring.length; j += 1) if (segmentsIntersect(line[i - 1], line[i], ring[j - 1], ring[j])) return true; return false; }
    function lineRelation(line, polygons) { for (var i = 0; i < polygons.length; i += 1) { if (lineIntersectsRing(line, polygons[i])) return { matched: true, relation: "intersects" }; if (line.length && pointInRing(line[0], polygons[i]) >= 0) return { matched: true, relation: "within" }; } return { matched: false, relation: "disjoint" }; }
    function polygonRelation(subjects, buffers) { for (var i = 0; i < subjects.length; i += 1) for (var j = 0; j < buffers.length; j += 1) { if (lineIntersectsRing(subjects[i], buffers[j])) return { matched: true, relation: "intersects" }; if (subjects[i].length && pointInRing(subjects[i][0], buffers[j]) >= 0) return { matched: true, relation: "within" }; if (buffers[j].length && pointInRing(buffers[j][0], subjects[i]) >= 0) return { matched: true, relation: "contains" }; } return { matched: false, relation: "disjoint" }; }

    function highlightBufferQueryResults(payload) { payload = payload || {}; var queryId = payload.query_id || (bufferQueryOrder.length ? bufferQueryOrder[bufferQueryOrder.length - 1] : null); var query = queryId && bufferQueryRegistry[queryId]; if (!query) return bufferError("BUFFER_QUERY_RESULT_REQUIRED", "请先查询缓冲区内对象，再高亮查询结果。"); clearBufferQueryHighlight({ query_id: queryId }); var viewer = getViewer(); var Cesium = global.Cesium || {}; if (!viewer || !viewer.entities || typeof viewer.entities.add !== "function") return bufferError("BUFFER_QUERY_HIGHLIGHT_UNSUPPORTED", "Viewer Entity overlay is unavailable."); var overlays = []; var unsupported = []; query.matches.forEach(function (match) { var record = spatialGeometryIndex[match.object_id]; if (!record || !record.geometry || record.geometry_confidence === "bounding_only") { unsupported.push(match.object_id); return; } var entity = bufferQueryOverlayEntity(Cesium, queryId, record); if (entity) overlays.push(viewer.entities.add(entity)); else unsupported.push(match.object_id); }); bufferQueryHighlightRegistry[queryId] = overlays; return { success: overlays.length > 0, code: overlays.length ? "SUCCESS" : "BUFFER_QUERY_HIGHLIGHT_UNSUPPORTED", query_id: queryId, highlighted_count: overlays.length, unsupported_count: unsupported.length, unsupported_object_ids: unsupported, owner: "ev-agent-buffer-query-highlight" }; }
    function bufferQueryOverlayEntity(Cesium, queryId, record) { var geometry = record.geometry; var base = { id: "ev-buffer-query-highlight-" + queryId + "-" + encodeURIComponent(record.object_id), name: "EV Buffer Query Highlight", properties: { owner: "ev-agent-buffer-query-highlight", query_id: queryId, object_id: record.object_id } }; if (geometry.type === "Point" && Cesium.Cartesian3 && Cesium.Cartesian3.fromDegrees) { base.position = Cesium.Cartesian3.fromDegrees(geometry.coordinates[0], geometry.coordinates[1], 10); base.point = { pixelSize: 14, color: Cesium.Color && Cesium.Color.LIME, outlineColor: Cesium.Color && Cesium.Color.BLACK, outlineWidth: 2, disableDepthTestDistance: Infinity }; return base; } if (geometry.type === "LineString" && Cesium.Cartesian3 && Cesium.Cartesian3.fromDegreesArray) { base.polyline = { positions: Cesium.Cartesian3.fromDegreesArray(flatCoordinates(geometry.coordinates)), width: 6, material: Cesium.Color && Cesium.Color.LIME }; return base; } var rings = polygonRings(geometry); if (rings.length && Cesium.Cartesian3 && Cesium.Cartesian3.fromDegreesArray) { base.polygon = { hierarchy: Cesium.Cartesian3.fromDegreesArray(flatCoordinates(rings[0])), material: Cesium.Color && Cesium.Color.LIME && Cesium.Color.LIME.withAlpha ? Cesium.Color.LIME.withAlpha(0.35) : undefined, outline: true, outlineColor: Cesium.Color && Cesium.Color.LIME }; return base; } return null; }
    function flatCoordinates(points) { var result = []; (points || []).forEach(function (point) { result.push(Number(point[0]), Number(point[1])); }); return result; }
    function clearBufferQueryHighlight(payload) { payload = payload || {}; var queryId = payload.query_id; var ids = queryId ? [queryId] : Object.keys(bufferQueryHighlightRegistry); var viewer = getViewer(); var cleared = 0; ids.forEach(function (id) { ensureArray(bufferQueryHighlightRegistry[id]).forEach(function (entity) { if (viewer && viewer.entities && typeof viewer.entities.remove === "function" && viewer.entities.remove(entity)) cleared += 1; }); delete bufferQueryHighlightRegistry[id]; }); return { success: true, code: "SUCCESS", cleared_count: cleared, buffer_polygon_preserved: true }; }

    function getViewer() {
      return options.viewer || global.viewer || null;
    }

    function getLayerManager() {
      return options.layerManager || global.LayerManager || global.evLayerManager || global.EV_LayerManager || null;
    }

    function getBridge() {
      if (!global.WebGLAgentBridge) {
        warn("window.WebGLAgentBridge is required. Load webgl_agent_bridge.js before webgl_ev_cesium_adapter.js.");
        return null;
      }
      return global.WebGLAgentBridge;
    }

    function resolveAgentBaseUrl() {
      if (global.EV_AGENT_CONFIG && global.EV_AGENT_CONFIG.agentBaseUrl) {
        return String(global.EV_AGENT_CONFIG.agentBaseUrl || "").replace(/\/+$/, "");
      }
      var marker = global.document && document.querySelector && document.querySelector('meta[name="ev-agent-base-url"]');
      if (marker && marker.getAttribute("content")) return String(marker.getAttribute("content") || "").replace(/\/+$/, "");
      var env = global.EV_AGENT_CONFIG && (global.EV_AGENT_CONFIG.environment || global.EV_AGENT_CONFIG.mode);
      env = String(env || "development").toLowerCase();
      if (env === "production") {
        return global.location && global.location.origin ? String(global.location.origin).replace(/\/+$/, "") : "";
      }
      return DEVELOPMENT_AGENT_BASE_URL;
    }

    function safeHandler(handler) {
      return function (eventOrPayload) {
        try {
          return handler(eventOrPayload);
        } catch (error) {
          warn("EV adapter handler failed", error);
          if (typeof options.onError === "function") options.onError(error);
          return {
            success: false,
            completed: false,
            error_code: "ADAPTER_HANDLER_FAILED",
            error: error && error.message ? error.message : String(error)
          };
        }
      };
    }

    function unwrapPayload(eventOrPayload) {
      return eventOrPayload && eventOrPayload.payload ? eventOrPayload.payload : eventOrPayload || {};
    }

    function warn(message, detail) {
      if (global.console && console.warn) {
        console.warn("[EVWebGLAgentAdapter] " + message, detail || "");
      }
    }

    function logDebug(message, detail) {
      if (options.debug && global.console && console.log) {
        console.log("[EVWebGLAgentAdapter] " + message, detail || "");
      }
    }

    return adapter;
  }

  function readProperties(value) {
    var result = {};
    if (!value) return result;
    if (typeof value.getPropertyNames === "function") {
      ensureArray(value.getPropertyNames()).forEach(function (key) {
        if (!isBusinessPropertyKey(key) || result[key] !== undefined) return;
        try {
          result[key] = sanitizePropertyValue(readMaybeValue(value.getProperty(key)));
        } catch (error) {
          result[key] = undefined;
        }
      });
    }
    if (value.properties && typeof value.properties === "object") {
      var propertyBag = value.properties;
      var usedPropertyBagNames = false;
      ensureArray(propertyBag.propertyNames).forEach(function (key) {
        if (!isBusinessPropertyKey(key) || result[key] !== undefined) return;
        usedPropertyBagNames = true;
        try {
          var property = typeof propertyBag.getProperty === "function" ? propertyBag.getProperty(key) : propertyBag[key];
          result[key] = sanitizePropertyValue(readMaybeValue(property));
        } catch (error) {
          result[key] = undefined;
        }
      });
      if (typeof propertyBag.getValue === "function") {
        try {
          var bagValue = propertyBag.getValue(getGlobalCurrentTime());
          Object.keys(bagValue || {}).forEach(function (key) {
            if (!isBusinessPropertyKey(key) || result[key] !== undefined) return;
            result[key] = sanitizePropertyValue(readMaybeValue(bagValue[key]));
          });
        } catch (error) {
          // Continue with propertyNames/object-key paths below.
        }
      }
      Object.keys(propertyBag).forEach(function (key) {
        if (usedPropertyBagNames) return;
        if (key === "propertyNames" || result[key] !== undefined) return;
        if (!isBusinessPropertyKey(key)) return;
        if (typeof propertyBag[key] === "function") return;
        result[key] = sanitizePropertyValue(readMaybeValue(propertyBag[key]));
      });
    }
    ["id", "ID", "EVID", "object_id", "objectId", "name", "NAME", "object_name", "type", "object_type", "layer_id", "layerId"].forEach(function (key) {
      if (typeof value.getProperty === "function") {
        var propertyValue = value.getProperty(key);
        if (propertyValue !== undefined && propertyValue !== null && isBusinessPropertyKey(key)) {
          result[key] = sanitizePropertyValue(propertyValue);
        }
      }
    });
    return result;
  }

  function isBusinessPropertyKey(key) {
    if (!key) return false;
    var text = String(key);
    if (text.charAt(0) === "_") return false;
    if (text === "definitionChanged" || text === "propertyNames") return false;
    if (/event|listener|subscription/i.test(text)) return false;
    return true;
  }

  function sanitizePropertyValue(value, seen) {
    if (value === undefined || value === null) return value;
    if (typeof value === "function") return undefined;
    if (typeof value !== "object") return value;
    seen = seen || [];
    if (seen.indexOf(value) >= 0) return undefined;
    if (isCesiumInternalObject(value)) return undefined;
    var nextSeen = seen.concat([value]);
    if (Array.isArray(value)) {
      return value.map(function (item) {
        return sanitizePropertyValue(item, nextSeen);
      }).filter(function (item) {
        return item !== undefined;
      });
    }
    var result = {};
    Object.keys(value).forEach(function (key) {
      if (!isBusinessPropertyKey(key)) return;
      var item = sanitizePropertyValue(value[key], nextSeen);
      if (item !== undefined) result[key] = item;
    });
    return result;
  }

  function isCesiumInternalObject(value) {
    if (!value || typeof value !== "object") return false;
    if (value._listeners || value._scopes || value._subscriptions) return true;
    if (value.constructor && /Event|PropertyBag|CallbackProperty|ConstantProperty/.test(value.constructor.name || "")) return true;
    return false;
  }

  function getGlobalCurrentTime() {
    if (global.viewer && global.viewer.clock && global.viewer.clock.currentTime) {
      return global.viewer.clock.currentTime;
    }
    var Cesium = global.Cesium || {};
    if (Cesium.JulianDate && typeof Cesium.JulianDate.now === "function") return Cesium.JulianDate.now();
    return undefined;
  }

  function tryHighlightEntity(entity, style) {
    if (!entity) return false;
    entity._agentOriginalStyle = entity._agentOriginalStyle || {};
    if (entity.billboard) {
      entity._agentOriginalStyle.billboardColor = entity.billboard.color;
      entity.billboard.color = style.color || entity.billboard.color;
      return true;
    }
    if (entity.point) {
      entity._agentOriginalStyle.pointColor = entity.point.color;
      entity.point.color = style.color || entity.point.color;
      return true;
    }
    if (entity.polygon) {
      entity._agentOriginalStyle.polygonMaterial = entity.polygon.material;
      entity.polygon.material = style.material || style.color || entity.polygon.material;
      return true;
    }
    return false;
  }

  function tryRestoreHighlight(entity) {
    if (!entity || !entity._agentOriginalStyle) return;
    if (entity.billboard && entity._agentOriginalStyle.billboardColor) {
      entity.billboard.color = entity._agentOriginalStyle.billboardColor;
    }
    if (entity.point && entity._agentOriginalStyle.pointColor) {
      entity.point.color = entity._agentOriginalStyle.pointColor;
    }
    if (entity.polygon && entity._agentOriginalStyle.polygonMaterial) {
      entity.polygon.material = entity._agentOriginalStyle.polygonMaterial;
    }
  }

  function readCameraCenter(viewer, camera) {
    var Cesium = global.Cesium || {};
    if (
      viewer &&
      viewer.scene &&
      viewer.scene.globe &&
      Cesium.Cartesian2 &&
      typeof camera.pickEllipsoid === "function"
    ) {
      var canvas = viewer.scene.canvas;
      var center = camera.pickEllipsoid(
        new Cesium.Cartesian2(canvas.clientWidth / 2, canvas.clientHeight / 2),
        viewer.scene.globe.ellipsoid
      );
      if (center && Cesium.Cartographic && typeof Cesium.Cartographic.fromCartesian === "function") {
        var cartographic = Cesium.Cartographic.fromCartesian(center);
        return [
          toDegrees(cartographic.longitude, Cesium),
          toDegrees(cartographic.latitude, Cesium)
        ];
      }
    }
    return null;
  }

  function readBounds(viewer) {
    if (!viewer || !viewer.camera || typeof viewer.camera.computeViewRectangle !== "function") return null;
    var Cesium = global.Cesium || {};
    var rectangle = viewer.camera.computeViewRectangle(viewer.scene && viewer.scene.globe && viewer.scene.globe.ellipsoid);
    if (!rectangle) return null;
    return {
      west: toDegrees(rectangle.west, Cesium),
      south: toDegrees(rectangle.south, Cesium),
      east: toDegrees(rectangle.east, Cesium),
      north: toDegrees(rectangle.north, Cesium)
    };
  }

  function readZoom(camera) {
    if (!camera || !camera.positionCartographic) return null;
    var height = Number(camera.positionCartographic.height);
    if (!isFinite(height) || height <= 0) return null;
    return Math.round(Math.log(40075016.686 / height) / Math.LN2);
  }

  function readCartesian(value) {
    if (!value) return null;
    return {
      x: readMaybeValue(value.x),
      y: readMaybeValue(value.y),
      z: readMaybeValue(value.z)
    };
  }

  function readMaybeValue(value) {
    if (value && typeof value.getValue === "function") {
      try {
        return value.getValue(getGlobalCurrentTime());
      } catch (error) {
        return value.getValue();
      }
    }
    return value;
  }

  function readStoreValue(key, options) {
    var store = options && options.store;
    if (!store) return null;
    if (store.state && store.state[key] !== undefined) return store.state[key];
    if (store[key] !== undefined) return store[key];
    if (store.getters && store.getters[key] !== undefined) return store.getters[key];
    return null;
  }

  function readWebGLVersion() {
    return global.EV_GLOBE_VERSION || global.EVGlobeVersion || "EV-Globe-WebGL";
  }

  function pickFirst() {
    for (var i = 0; i < arguments.length; i += 1) {
      if (arguments[i] !== undefined && arguments[i] !== null && arguments[i] !== "") return arguments[i];
    }
    return null;
  }

  function finiteNumber(value) {
    if (value === undefined || value === null || value === "") return null;
    var number = Number(value);
    return isFinite(number) ? number : null;
  }

  function normalizeCenter(center) {
    if (!Array.isArray(center) || center.length < 2) return null;
    var lon = finiteNumber(center[0]);
    var lat = finiteNumber(center[1]);
    if (lon === null || lat === null) return null;
    return [lon, lat];
  }

  function maybePromise(value) {
    if (value && typeof value.then === "function") return value;
    return Promise.resolve(value);
  }

  function flyCameraTo(camera, method) {
    var args = Array.prototype.slice.call(arguments, 2);
    if (!camera || typeof camera[method] !== "function") {
      return Promise.reject(new Error("Camera method unavailable: " + method));
    }
    return new Promise(function (resolve, reject) {
      var settled = false;
      function complete() {
        if (settled) return;
        settled = true;
        resolve();
      }
      function cancel() {
        if (settled) return;
        settled = true;
        reject(new Error("CAMERA_FLIGHT_CANCELLED"));
      }
      var last = args[args.length - 1];
      if (!last || typeof last !== "object") {
        last = {};
        args.push(last);
      }
      var userComplete = last.complete;
      var userCancel = last.cancel;
      last.complete = function () {
        if (typeof userComplete === "function") userComplete.apply(this, arguments);
        complete();
      };
      last.cancel = function () {
        if (typeof userCancel === "function") userCancel.apply(this, arguments);
        cancel();
      };
      try {
        var returned = camera[method].apply(camera, args);
        var fallbackMs = Math.max(0, Number(last.duration || 0) * 1000) + 50;
        var fallbackTimer = setTimeout(complete, fallbackMs || 50);
        if (returned && typeof returned.then === "function") {
          returned.then(function () {
            clearTimeout(fallbackTimer);
            complete();
          }).catch(function (error) {
            clearTimeout(fallbackTimer);
            reject(error);
          });
        } else if (Number(last.duration || 0) === 0) {
          clearTimeout(fallbackTimer);
          setTimeout(complete, 0);
        }
      } catch (error) {
        reject(error);
      }
    });
  }

  function failureResult(errorCode, message, meta) {
    return {
      success: false,
      found: meta && meta.object_id ? true : false,
      object_id: meta && meta.object_id || null,
      object_type: meta && meta.object_type || null,
      source: meta && meta.source || null,
      source_ref: meta && meta.source_ref || null,
      stable_id: meta && meta.stable_id !== undefined ? !!meta.stable_id : null,
      id_scope: meta && meta.id_scope || null,
      resolver: meta && meta.resolver || null,
      method: null,
      completed: false,
      error_code: errorCode,
      error: message
    };
  }

  function flySuccess(meta, method) {
      return {
        success: true,
        found: true,
        object_id: meta && meta.object_id || null,
        runtime_id: meta && meta.runtime_id || null,
        business_id: meta && meta.business_id || null,
        business_id_source: meta && meta.business_id_source || null,
        object_type: meta && meta.object_type || null,
        business_type: meta && meta.business_type || null,
        source: meta && meta.source || null,
      source_ref: meta && meta.source_ref || null,
      data_source_name: meta && meta.data_source_name || null,
      data_source_index: meta && meta.data_source_index !== undefined ? meta.data_source_index : null,
      stable_id: meta && meta.stable_id !== undefined ? !!meta.stable_id : null,
      id_scope: meta && meta.id_scope || null,
      resolver: meta && meta.resolver || null,
      fly_target_type: meta && meta.fly_target_type || null,
      bounding_sphere_radius: meta && meta.bounding_sphere_radius !== undefined ? meta.bounding_sphere_radius : null,
      range: meta && meta.range !== undefined ? meta.range : null,
      method: method,
      completed: true
    };
  }

  function summarizeObjectTarget(target, source) {
    var properties = readProperties(target);
    return {
      object_id: String(pickFirst(target && target.object_id, target && target.objectId, target && target.id, properties.object_id, properties.objectId, properties.id, properties.EVID, "")),
      object_type: String(pickFirst(target && target.object_type, target && target.objectType, target && target.type, properties.object_type, properties.objectType, properties.type, "unknown")),
      source: source || target && target.source || "runtime"
    };
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

  function ensureArray(value) {
    if (!value) return [];
    if (Array.isArray(value)) return value;
    if (typeof value.values === "function") return Array.prototype.slice.call(value.values());
    if (typeof value.length === "number") return Array.prototype.slice.call(value);
    return [value];
  }

    function matchesLayer(layer, layerId) {
      if (!layer || !layerId) return false;
    var key = String(layer.id || layer.layer_id || layer.name || layer.customTag && layer.customTag.serviceName || "");
    var label = String(layer.name || layer.id || layer.customTag && layer.customTag.serviceName || "");
    var query = String(layerId);
    return key === query || label === query || key.indexOf(query) > -1 || label.indexOf(query) > -1;
  }

  function toDegrees(value, Cesium) {
    if (Cesium.Math && typeof Cesium.Math.toDegrees === "function") {
      return Cesium.Math.toDegrees(value);
    }
    return value * 180 / Math.PI;
  }

  function sanitizeRequest(payload) {
    if (!payload) return payload;
    return {
      session_id: payload.session_id,
      user_id: payload.user_id,
      role: payload.role,
      query: payload.query,
      gis_context: payload.gis_context
    };
  }

  function agentLog(message, detail) {
    if (global.console && console.log) {
      console.log("[WebGLAgent] " + message, detail || "");
    }
  }

  function warnSourceFallback(message, detail) {
    if (global.console && console.warn) {
      console.warn("[WebGLAgent] fallback used: " + message, detail || "");
    }
  }

  function warnSourceFailed(type, detail) {
    if (global.console && console.warn) {
      console.warn("[WebGLAgent] source anchor failed: " + type, detail || "");
    }
  }

  global.createEVWebGLAgentAdapter = createEVWebGLAgentAdapter;
})(window);
