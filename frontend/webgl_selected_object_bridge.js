(function (global) {
  "use strict";

  var BRIDGE_VERSION = "0.1.0";
  var ATTACH_FLAG = "__EV_AGENT_SELECTED_OBJECT_ATTACHED__";
  var state = {
    lastObject: null,
    attachReports: []
  };

  if (typeof global.__EV_AGENT_SELECTED_OBJECT__ === "undefined") {
    global.__EV_AGENT_SELECTED_OBJECT__ = null;
  }

  function attach() {
    ensureSourceAnchor();
    state.attachReports = [];
    attachLayerManager(global.LayerManager, "window.LayerManager");
    attachLayerManager(global.evLayerManager, "window.evLayerManager");
    attachLayerManager(global.EV_LayerManager, "window.EV_LayerManager");
    attachE3MLayer(global.e3mLayer, "window.e3mLayer");
    attachE3MLayers(global.e3mLayers, "window.e3mLayers");
    attachViewerSelectedEntity(global.viewer, "window.viewer");
    logAttachReport();
    return state.attachReports.slice();
  }

  function ensureSourceAnchor() {
    global.EVWebGLSourceAnchors = global.EVWebGLSourceAnchors || {};
    var previous = global.EVWebGLSourceAnchors.getSelectedObject;
    global.EVWebGLSourceAnchors.getSelectedObjectAge = getSelectedObjectAge;
    if (previous && previous.__evAgentSelectedObjectBridge) return;
    var sourceAnchor = function () {
      if (state.lastObject) return state.lastObject;
      if (global.__EV_AGENT_SELECTED_OBJECT__) return global.__EV_AGENT_SELECTED_OBJECT__;
      if (typeof previous === "function") {
        try {
          return normalizeSelectedObject(previous(), { source: "previous_source_anchor" });
        } catch (error) {
          warn("previous getSelectedObject failed", error);
        }
      }
      return normalizeSelectedObject(global.viewer && global.viewer.selectedEntity, {
        source: "viewer.selectedEntity"
      });
    };
    sourceAnchor.__evAgentSelectedObjectBridge = true;
    global.EVWebGLSourceAnchors.getSelectedObject = sourceAnchor;
  }

  function attachLayerManager(manager, label) {
    if (!manager || !manager.selectionChanged) return report(label, "missing");
    if (manager.selectionChanged[ATTACH_FLAG]) return report(label, "already_attached");
    if (typeof manager.selectionChanged.addEventListener !== "function") {
      return report(label + ".selectionChanged", "not_event");
    }
    manager.selectionChanged.addEventListener(function (layer, feature) {
      updateSelectedObject({ layer: layer, feature: feature }, {
        source: label + ".selectionChanged"
      });
    });
    manager.selectionChanged[ATTACH_FLAG] = true;
    return report(label + ".selectionChanged", "attached");
  }

  function attachE3MLayer(layer, label) {
    if (!layer || !layer.selectionChanged) return report(label, "missing");
    if (layer.selectionChanged[ATTACH_FLAG]) return report(label, "already_attached");
    if (typeof layer.selectionChanged.addEventListener !== "function") {
      return report(label + ".selectionChanged", "not_event");
    }
    layer.selectionChanged.addEventListener(function (feature) {
      updateSelectedObject({ layer: layer, feature: feature }, {
        source: label + ".selectionChanged"
      });
    });
    layer.selectionChanged[ATTACH_FLAG] = true;
    return report(label + ".selectionChanged", "attached");
  }

  function attachE3MLayers(layers, label) {
    if (!layers) return report(label, "missing");
    var list = toArray(layers);
    if (!list.length) return report(label, "empty");
    for (var i = 0; i < list.length; i += 1) {
      attachE3MLayer(list[i], label + "[" + i + "]");
    }
  }

  function attachViewerSelectedEntity(viewer, label) {
    if (!viewer) return report(label, "missing");
    if (!viewer.selectedEntityChanged) return report(label + ".selectedEntityChanged", "missing");
    if (viewer.selectedEntityChanged[ATTACH_FLAG]) return report(label + ".selectedEntityChanged", "already_attached");
    if (typeof viewer.selectedEntityChanged.addEventListener !== "function") {
      return report(label + ".selectedEntityChanged", "not_event");
    }
    viewer.selectedEntityChanged.addEventListener(function (entity) {
      updateSelectedObject(entity, { source: label + ".selectedEntityChanged" });
    });
    viewer.selectedEntityChanged[ATTACH_FLAG] = true;
    return report(label + ".selectedEntityChanged", "attached");
  }

  function updateSelectedObject(value, context) {
    var selected = normalizeSelectedObject(value, context || {});
    state.lastObject = selected;
    global.__EV_AGENT_SELECTED_OBJECT__ = selected;
    if (selected) {
      console.log("[WebGLAgent] selected object updated", selected);
    }
    dispatchSelectedObjectChanged(selected);
    return selected;
  }

  function normalizeSelectedObject(value, context) {
    if (!value) return null;
    context = context || {};
    var layer = value.layer || context.layer || null;
    var feature = value.feature || value.entity || value.pickedObject || value;
    var properties = mergeObjects(readLayerProperties(layer), readProperties(feature), readProperties(value));
    var position = readPosition(value, feature, properties);
    var selected = {
      object_id: pickFirst(
        value.object_id,
        value.objectId,
        value.id,
        properties.object_id,
        properties.objectId,
        properties.EVID,
        properties.id,
        properties.ID,
        properties.OBJECTID,
        readFeatureProperty(feature, "EVID"),
        readFeatureProperty(feature, "id")
      ),
      object_type: pickFirst(
        value.object_type,
        value.objectType,
        value.type,
        properties.object_type,
        properties.objectType,
        properties.type,
        inferObjectType(feature, properties)
      ),
      object_name: pickFirst(
        value.object_name,
        value.objectName,
        value.name,
        properties.object_name,
        properties.objectName,
        properties.name,
        properties.NAME,
        readFeatureProperty(feature, "name"),
        readFeatureProperty(feature, "NAME")
      ),
      layer_id: pickFirst(
        value.layer_id,
        value.layerId,
        properties.layer_id,
        properties.layerId,
        readLayerId(layer),
        readFeatureProperty(feature, "layer_id")
      ),
      longitude: pickFirst(value.longitude, value.lon, value.lng, properties.longitude, properties.lon, properties.lng, position.longitude),
      latitude: pickFirst(value.latitude, value.lat, properties.latitude, properties.lat, position.latitude),
      height: pickFirst(value.height, value.altitude, value.alt, properties.height, properties.altitude, properties.alt, position.height),
      properties: properties,
      source: pickFirst(value.source, context.source, properties.source, "unknown"),
      timestamp: pickFirst(value.timestamp, properties.timestamp, Date.now())
    };
    selected.properties.source = selected.source;
    selected.properties.timestamp = selected.timestamp;
    if (selected.longitude !== undefined) selected.properties.longitude = selected.longitude;
    if (selected.latitude !== undefined) selected.properties.latitude = selected.latitude;
    if (selected.height !== undefined) selected.properties.height = selected.height;
    return selected;
  }

  function getSelectedObjectAge() {
    var selected = state.lastObject || global.__EV_AGENT_SELECTED_OBJECT__;
    if (!selected || !selected.timestamp) return null;
    var timestamp = Number(selected.timestamp);
    if (!isFinite(timestamp)) return null;
    return Date.now() - timestamp;
  }

  function readProperties(source) {
    var result = {};
    if (!source) return result;
    readPropertyBag(source.properties, result);
    if (typeof source.getPropertyNames === "function") {
      try {
        source.getPropertyNames().forEach(function (name) {
          result[name] = readMaybeValue(source.getProperty(name));
        });
      } catch (error) {
        warn("getPropertyNames failed", error);
      }
    }
    [
      "EVID", "id", "ID", "OBJECTID", "name", "NAME", "type", "layer_id",
      "layerId", "serviceName", "powerGridGuid", "EVDataSetName"
    ].forEach(function (name) {
      var value = readFeatureProperty(source, name);
      if (value !== undefined && result[name] === undefined) result[name] = value;
    });
    return result;
  }

  function readPropertyBag(properties, result) {
    if (!properties) return;
    if (Array.isArray(properties.propertyNames)) {
      properties.propertyNames.forEach(function (name) {
        result[name] = readMaybeValue(properties[name]);
      });
      return;
    }
    Object.keys(properties).forEach(function (key) {
      if (typeof properties[key] !== "function") {
        result[key] = readMaybeValue(properties[key]);
      }
    });
  }

  function readFeatureProperty(feature, name) {
    if (!feature) return undefined;
    if (typeof feature.getProperty === "function") {
      try {
        return readMaybeValue(feature.getProperty(name));
      } catch (error) {
        return undefined;
      }
    }
    if (feature.properties && feature.properties[name] !== undefined) {
      return readMaybeValue(feature.properties[name]);
    }
    if (feature[name] !== undefined) return readMaybeValue(feature[name]);
    return undefined;
  }

  function readLayerProperties(layer) {
    var result = {};
    if (!layer) return result;
    var tag = layer._customTag || layer.customTag || {};
    result.layer_id = readLayerId(layer);
    result.layer_name = pickFirst(layer.name, layer.layerName, tag.serviceName, tag.name);
    result.serviceName = tag.serviceName;
    result.powerGridGuid = tag.powerGridGuid;
    return result;
  }

  function readLayerId(layer) {
    if (!layer) return undefined;
    var tag = layer._customTag || layer.customTag || {};
    return pickFirst(layer.id, layer.layer_id, layer.layerId, layer.name, tag.serviceName, tag.powerGridGuid);
  }

  function readPosition(value, feature, properties) {
    var position = {};
    var candidate = value.position || feature && feature.position || properties.position;
    candidate = readMaybeValue(candidate);
    if (!candidate) return position;
    var Cesium = global.Cesium || {};
    if (Cesium.Cartographic && typeof Cesium.Cartographic.fromCartesian === "function" && candidate.x !== undefined) {
      try {
        var cartographic = Cesium.Cartographic.fromCartesian(candidate);
        position.longitude = Cesium.Math && Cesium.Math.toDegrees ? Cesium.Math.toDegrees(cartographic.longitude) : cartographic.longitude;
        position.latitude = Cesium.Math && Cesium.Math.toDegrees ? Cesium.Math.toDegrees(cartographic.latitude) : cartographic.latitude;
        position.height = cartographic.height;
      } catch (error) {
        warn("position conversion failed", error);
      }
    }
    return position;
  }

  function readMaybeValue(value) {
    if (value && typeof value.getValue === "function") {
      try {
        return readMaybeValue(value.getValue(global.viewer && global.viewer.clock && global.viewer.clock.currentTime));
      } catch (error) {
        try {
          return readMaybeValue(value.getValue());
        } catch (ignored) {
          return undefined;
        }
      }
    }
    if (value && Object.prototype.hasOwnProperty.call(value, "_value")) {
      return readMaybeValue(value._value);
    }
    return value;
  }

  function inferObjectType(feature, properties) {
    if (properties && (properties.SHAPE || properties.SHAPE_Area || properties.OBJECTID)) return "shp_feature";
    if (feature && typeof feature.getProperty === "function") return "e3m_feature";
    if (feature && feature.properties) return "entity";
    return "unknown";
  }

  function mergeObjects() {
    var result = {};
    for (var i = 0; i < arguments.length; i += 1) {
      var source = arguments[i] || {};
      Object.keys(source).forEach(function (key) {
        if (source[key] !== undefined && source[key] !== null && source[key] !== "") {
          result[key] = source[key];
        }
      });
    }
    return result;
  }

  function pickFirst() {
    for (var i = 0; i < arguments.length; i += 1) {
      if (arguments[i] !== undefined && arguments[i] !== null && arguments[i] !== "") return arguments[i];
    }
    return undefined;
  }

  function toArray(value) {
    if (!value) return [];
    if (Array.isArray(value)) return value;
    if (typeof value.length === "number") return Array.prototype.slice.call(value);
    if (typeof value.values === "function") return Array.prototype.slice.call(value.values());
    return [value];
  }

  function report(target, status) {
    state.attachReports.push({ target: target, status: status });
  }

  function logAttachReport() {
    if (global.console && console.log) {
      console.log("[WebGLAgent] selected object bridge attach report", state.attachReports);
    }
  }

  function dispatchSelectedObjectChanged(selected) {
    if (!global.dispatchEvent || typeof global.CustomEvent !== "function") return;
    global.dispatchEvent(new CustomEvent("ev-agent:selected-object-changed", {
      detail: { selected_object: selected }
    }));
  }

  function warn(message, detail) {
    if (global.console && console.warn) {
      console.warn("[WebGLAgent] selected object bridge:", message, detail || "");
    }
  }

  function scheduleAttach() {
    [0, 500, 1500, 3000, 6000].forEach(function (delay) {
      setTimeout(attach, delay);
    });
  }

  global.EVWebGLSelectedObjectBridge = {
    version: BRIDGE_VERSION,
    attach: attach,
    normalizeSelectedObject: normalizeSelectedObject,
    setSelectedObject: updateSelectedObject,
    getSelectedObject: function () {
      ensureSourceAnchor();
      return global.EVWebGLSourceAnchors.getSelectedObject();
    },
    getSelectedObjectAge: getSelectedObjectAge,
    getAttachReport: function () {
      return state.attachReports.slice();
    }
  };

  scheduleAttach();
  if (global.document && document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", attach);
  }
})(window);
