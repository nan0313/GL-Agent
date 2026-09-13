(function (global) {
  "use strict";

  var PATCH_VERSION = "0.1.0";
  var PATCH_FLAG = "__EV_AGENT_PICK_PATCHED__";
  var ORIGINAL_START = "__EV_AGENT_ORIGINAL_START__";
  var PATCH_SOURCE = "EVPickTool.start.callback";

  function start() {
    if (global.__EV_AGENT_DISABLE_PICK_PATCH__) return;
    schedulePatch();
  }

  function schedulePatch() {
    [0, 500, 1500, 3000, 6000].forEach(function (delay) {
      setTimeout(patchEVPickToolStart, delay);
    });
  }

  function patchEVPickToolStart() {
    if (global.__EV_AGENT_DISABLE_PICK_PATCH__) return false;
    var EVGlobe = global.EVGlobe;
    var EVPickTool = EVGlobe && EVGlobe.EVPickTool;
    var prototype = EVPickTool && EVPickTool.prototype;
    if (!prototype || typeof prototype.start !== "function") return false;
    if (prototype.start[PATCH_FLAG]) return true;

    var originalStart = prototype.start;
    var patchedStart = function () {
      var args = Array.prototype.slice.call(arguments);
      for (var i = 0; i < args.length; i += 1) {
        if (typeof args[i] === "function") {
          args[i] = wrapCallback(args[i]);
        }
      }
      return originalStart.apply(this, args);
    };
    patchedStart[PATCH_FLAG] = true;
    patchedStart[ORIGINAL_START] = originalStart;
    prototype.start = patchedStart;
    console.log("[WebGLAgent] EVPickTool.start patched");
    return true;
  }

  function wrapCallback(callback) {
    if (callback && callback.__EV_AGENT_PICK_CALLBACK_WRAPPED__) return callback;
    var wrapped = function () {
      var callbackArgs = arguments;
      try {
        debugPickResult(callbackArgs[0]);
        var normalized = normalizePickResult(callbackArgs[0], callbackArgs);
        if (normalized) {
          updateSelectedObject(normalized);
        }
      } catch (error) {
        console.warn("[WebGLAgent] selected object normalize failed", error);
      }
      return callback.apply(this, callbackArgs);
    };
    wrapped.__EV_AGENT_PICK_CALLBACK_WRAPPED__ = true;
    return wrapped;
  }

  function normalizePickResult(result, callbackArgs) {
    callbackArgs = callbackArgs || [];
    if (!result) return null;
    if (result.feature) return normalizePickResult(result.feature, callbackArgs);
    if (result.features && result.features.length) {
      return normalizeGeoJsonFeature(result.features[0], callbackArgs);
    }
    if (Array.isArray(result)) {
      if (!result.length) return null;
      return normalizeArrayItem(result[0], callbackArgs);
    }
    if (typeof result.getProperty === "function") {
      return normalizeFeature(result, callbackArgs);
    }
    if (isCesiumEntity(result)) {
      return normalizeEntity(result, callbackArgs);
    }
    if (result.tileset || result.content) {
      return normalizeModel(result, callbackArgs);
    }
    if (result.properties) {
      return normalizeProperties(result.properties, callbackArgs, inferPlainObjectType(result), result);
    }
    return normalizeProperties(result, callbackArgs, inferPlainObjectType(result), result);
  }

  function normalizeArrayItem(item, callbackArgs) {
    if (!item) return null;
    if (item.feature) return normalizePickResult(item.feature, callbackArgs);
    if (typeof item.getProperty === "function") return normalizeFeature(item, callbackArgs);
    if (isCesiumEntity(item)) return normalizeEntity(item, callbackArgs);
    if (item.features && item.features.length) return normalizeGeoJsonFeature(item.features[0], callbackArgs);
    if (item.properties) return normalizeProperties(item.properties, callbackArgs, inferPlainObjectType(item), item);
    return normalizeProperties(item, callbackArgs, inferPlainObjectType(item), item);
  }

  function normalizeGeoJsonFeature(feature, callbackArgs) {
    var properties = feature && feature.properties || {};
    return normalizeProperties(properties, callbackArgs, "geojson", feature);
  }

  function normalizeFeature(feature, callbackArgs) {
    var properties = readFeatureProperties(feature);
    var layerId = pickFirst(
      properties.layer_id,
      properties.layerId,
      properties.serviceName,
      feature.tileset && feature.tileset.EVDataSetName,
      feature.tileset && feature.tileset.resource && feature.tileset.resource.queryParameters && feature.tileset.resource.queryParameters.serviceName
    );
    return {
      object_id: pickFirst(properties.object_id, properties.objectId, properties.EVID, properties.id, properties.ID, properties.OBJECTID),
      object_type: pickFirst(properties.object_type, properties.objectType, properties.type, "modelFeature"),
      object_name: pickFirst(properties.object_name, properties.objectName, properties.name, properties.NAME),
      layer_id: layerId,
      longitude: pickFirst(readCoordinate(callbackArgs, "longitude"), readGeometryCenter(properties).longitude),
      latitude: pickFirst(readCoordinate(callbackArgs, "latitude"), readGeometryCenter(properties).latitude),
      height: pickFirst(readCoordinate(callbackArgs, "height"), readGeometryCenter(properties).height),
      properties: properties,
      source: PATCH_SOURCE
    };
  }

  function normalizeEntity(entity, callbackArgs) {
    var properties = mergeObjects(readEntityProperties(entity), copyProperties(entity.properties || {}));
    var context = {
      id: readMaybeValue(entity.id),
      name: readMaybeValue(entity.name),
      geometry: entity.geometry,
      position: entity.position,
      polygon: entity.polygon,
      polyline: entity.polyline,
      rectangle: entity.rectangle,
      point: entity.point,
      billboard: entity.billboard
    };
    return normalizeProperties(properties, callbackArgs, inferEntityType(entity), context);
  }

  function normalizeModel(model, callbackArgs) {
    var query = model.tileset && model.tileset.resource && model.tileset.resource.queryParameters || {};
    var content = model.content || {};
    var modelId = content._model && content._model.id;
    var properties = {
      id: pickFirst(model.id, modelId),
      name: pickFirst(model.name, query.serviceName),
      serviceName: query.serviceName
    };
    return {
      object_id: pickFirst(properties.id, properties.serviceName),
      object_type: "model",
      object_name: pickFirst(properties.name, properties.id),
      layer_id: properties.serviceName,
      longitude: readCoordinate(callbackArgs, "longitude"),
      latitude: readCoordinate(callbackArgs, "latitude"),
      height: readCoordinate(callbackArgs, "height"),
      properties: properties,
      source: PATCH_SOURCE
    };
  }

  function normalizeProperties(properties, callbackArgs, objectType, context) {
    properties = copyProperties(properties || {});
    context = context || {};
    var geometryCenter = readGeometryCenter(context.geometry || properties.geometry);
    var position = readPosition(context.position || properties.position);
    var id = pickFirst(
      context.id,
      properties.id,
      properties.object_id,
      properties.objectId,
      properties.fid,
      properties.FID,
      properties.EVID,
      properties.ID,
      properties.OBJECTID,
      properties.uuid,
      properties.guid,
      properties.NAME
    );
    var name = pickFirst(
      context.name,
      properties.name,
      properties.object_name,
      properties.objectName,
      properties.label,
      properties.LABEL,
      properties.NAME,
      properties.title,
      properties.id,
      properties.EVID
    );
    return {
      object_id: id,
      object_type: pickFirst(properties.object_type, properties.objectType, properties.type, objectType),
      object_name: name,
      layer_id: pickFirst(properties.layer_id, properties.layerId, properties.layer, properties.serviceName),
      longitude: pickFirst(properties.longitude, properties.lon, properties.lng, readCoordinate(callbackArgs, "longitude"), geometryCenter.longitude, position.longitude),
      latitude: pickFirst(properties.latitude, properties.lat, readCoordinate(callbackArgs, "latitude"), geometryCenter.latitude, position.latitude),
      height: pickFirst(properties.height, properties.altitude, properties.alt, readCoordinate(callbackArgs, "height"), geometryCenter.height, position.height),
      properties: properties,
      source: PATCH_SOURCE
    };
  }

  function debugPickResult(result) {
    if (global.__EV_AGENT_PICK_DEBUG__ === false) return;
    var summary = {
      result_type: describeResultType(result),
      keys: safeKeys(result),
      properties: result && result.properties,
      features: result && result.features,
      geometry: result && result.geometry,
      id: result && result.id,
      name: result && result.name
    };
    if (Array.isArray(result) && result.length) {
      summary.first_item_type = describeResultType(result[0]);
      summary.first_item_keys = safeKeys(result[0]);
      summary.first_item_properties = result[0] && result[0].properties;
      summary.first_item_geometry = result[0] && result[0].geometry;
      summary.first_item_id = result[0] && result[0].id;
      summary.first_item_name = result[0] && result[0].name;
    }
    console.log("[WebGLAgent] EVPickTool callback debug", summary);
  }

  function describeResultType(value) {
    if (value === null) return "null";
    if (Array.isArray(value)) return "array";
    if (value && typeof value.getProperty === "function") return "feature";
    if (isCesiumEntity(value)) return "entity";
    if (value && value.features) return "geojson";
    if (value && (value.tileset || value.content)) return "model";
    return typeof value;
  }

  function safeKeys(value) {
    if (!value || typeof value !== "object") return [];
    try {
      return Object.keys(value);
    } catch (error) {
      return [];
    }
  }

  function isCesiumEntity(value) {
    return !!(
      value &&
      typeof value === "object" &&
      (value.position || value.properties || value.polygon || value.polyline || value.rectangle || value.point || value.billboard) &&
      (value.id !== undefined || value.name !== undefined || value.properties)
    );
  }

  function inferPlainObjectType(value) {
    if (!value) return "unknown";
    if (value.geometry || value.properties && value.properties.geometry) return "shp";
    if (value.features) return "geojson";
    if (value.properties && (value.properties.SHAPE_AREA || value.properties.SHAPE_LEN || value.properties.OBJECTID)) return "shp";
    if (value.polygon || value.polyline || value.rectangle) return "shp";
    if (value.position || value.point || value.billboard) return "entity";
    return "unknown";
  }

  function inferEntityType(entity) {
    if (entity && (entity.geometry || entity.polygon || entity.polyline || entity.rectangle)) return "shp";
    return "entity";
  }

  function readFeatureProperties(feature) {
    var result = {};
    var names = [];
    if (typeof feature.getPropertyNames === "function") {
      try {
        names = feature.getPropertyNames();
      } catch (error) {
        names = [];
      }
    }
    [
      "EVID", "id", "ID", "OBJECTID", "name", "NAME", "type", "layer_id",
      "layerId", "serviceName", "powerGridGuid", "EVDataSetName"
    ].forEach(function (name) {
      if (names.indexOf(name) < 0) names.push(name);
    });
    names.forEach(function (name) {
      var value = readFeatureProperty(feature, name);
      if (value !== undefined && value !== null && value !== "") result[name] = value;
    });
    result.EVDataSetName = pickFirst(result.EVDataSetName, feature.tileset && feature.tileset.EVDataSetName);
    return result;
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

  function readEntityProperties(entity) {
    var result = {};
    var properties = entity && entity.properties;
    if (!properties) return result;
    if (Array.isArray(properties.propertyNames)) {
      properties.propertyNames.forEach(function (name) {
        result[name] = readMaybeValue(properties[name]);
      });
      return result;
    }
    Object.keys(properties).forEach(function (name) {
      if (typeof properties[name] !== "function") result[name] = readMaybeValue(properties[name]);
    });
    return result;
  }

  function readCoordinate(callbackArgs, field) {
    var position = callbackArgs && callbackArgs.length > 2 ? callbackArgs[2] : null;
    if (!position) return undefined;
    if (field === "longitude") return pickFirst(position.longitude, position.lon, position.lng);
    if (field === "latitude") return pickFirst(position.latitude, position.lat);
    if (field === "height") return pickFirst(position.height, position.altitude, position.alt);
    return undefined;
  }

  function readGeometryCenter(geometry) {
    var points = [];
    collectGeometryPoints(geometry, points);
    if (!points.length) return {};
    var minLon = Infinity;
    var maxLon = -Infinity;
    var minLat = Infinity;
    var maxLat = -Infinity;
    var minHeight = Infinity;
    var maxHeight = -Infinity;
    var hasHeight = false;
    points.forEach(function (point) {
      var lon = Number(point[0]);
      var lat = Number(point[1]);
      if (!isFinite(lon) || !isFinite(lat)) return;
      minLon = Math.min(minLon, lon);
      maxLon = Math.max(maxLon, lon);
      minLat = Math.min(minLat, lat);
      maxLat = Math.max(maxLat, lat);
      if (point.length > 2 && isFinite(Number(point[2]))) {
        hasHeight = true;
        minHeight = Math.min(minHeight, Number(point[2]));
        maxHeight = Math.max(maxHeight, Number(point[2]));
      }
    });
    if (!isFinite(minLon) || !isFinite(minLat)) return {};
    return {
      longitude: (minLon + maxLon) / 2,
      latitude: (minLat + maxLat) / 2,
      height: hasHeight ? (minHeight + maxHeight) / 2 : undefined
    };
  }

  function collectGeometryPoints(value, output) {
    if (!value) return;
    if (value.coordinates) {
      collectGeometryPoints(value.coordinates, output);
      return;
    }
    if (Array.isArray(value)) {
      if (typeof value[0] === "number" && typeof value[1] === "number") {
        output.push(value);
        return;
      }
      value.forEach(function (item) {
        collectGeometryPoints(item, output);
      });
    }
  }

  function readPosition(position) {
    position = readMaybeValue(position);
    if (!position) return {};
    if (position.longitude !== undefined || position.lon !== undefined || position.lng !== undefined) {
      return {
        longitude: pickFirst(position.longitude, position.lon, position.lng),
        latitude: pickFirst(position.latitude, position.lat),
        height: pickFirst(position.height, position.altitude, position.alt)
      };
    }
    var Cesium = global.Cesium || {};
    if (position.x !== undefined && Cesium.Cartographic && typeof Cesium.Cartographic.fromCartesian === "function") {
      try {
        var cartographic = Cesium.Cartographic.fromCartesian(position);
        return {
          longitude: Cesium.Math && Cesium.Math.toDegrees ? Cesium.Math.toDegrees(cartographic.longitude) : cartographic.longitude,
          latitude: Cesium.Math && Cesium.Math.toDegrees ? Cesium.Math.toDegrees(cartographic.latitude) : cartographic.latitude,
          height: cartographic.height
        };
      } catch (error) {
        return {};
      }
    }
    return {};
  }

  function updateSelectedObject(selected) {
    selected.source = PATCH_SOURCE;
    selected.timestamp = selected.timestamp || Date.now();
    selected.properties = selected.properties || {};
    selected.properties.source = PATCH_SOURCE;
    selected.properties.timestamp = selected.timestamp;
    var bridge = global.EVWebGLSelectedObjectBridge;
    if (bridge && typeof bridge.setSelectedObject === "function") {
      bridge.setSelectedObject(selected, { source: PATCH_SOURCE });
      return;
    }
    global.__EV_AGENT_SELECTED_OBJECT__ = selected;
    console.log("[WebGLAgent] selected object updated", selected);
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

  function copyProperties(properties) {
    var result = {};
    Object.keys(properties || {}).forEach(function (key) {
      if (typeof properties[key] !== "function") result[key] = readMaybeValue(properties[key]);
    });
    return result;
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

  global.EVWebGLEvPickPatch = {
    version: PATCH_VERSION,
    patch: patchEVPickToolStart,
    normalizePickResult: normalizePickResult
  };

  start();
})(window);
