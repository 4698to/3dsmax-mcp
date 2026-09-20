"""Shared scene query implementations for query_scene and legacy tool wrappers."""

from __future__ import annotations

import json

from ..helpers.maxscript import safe_string
from ..helpers.scene_reads import (
    fetch_class_instances_native,
    fetch_scene_snapshot,
    scene_info_summary_from_snapshot,
)
from ..max_client import MaxClient

_previous_snapshot: dict | None = None

VALID_ACTIONS = frozenset({"overview", "filter", "class", "property", "selection", "delta", "unhidden_objects"})


def normalize_action(action: str) -> str:
    return (action or "").strip().lower()


def dispatch_query_scene(client: MaxClient, action: str, **params: object) -> str:
    act = normalize_action(action)
    if act not in VALID_ACTIONS:
        return json.dumps({
            "error": f"Unknown action: {action}. Use overview, filter, class, property, selection, or delta.",
        })

    if act == "overview":
        return run_overview(client, int(params.get("max_roots", 50)))
    if act == "filter":
        return run_filter(
            client,
            class_name=str(params.get("class_name", "") or ""),
            pattern=str(params.get("pattern", "") or ""),
            layer=str(params.get("layer", "") or ""),
            limit=int(params.get("limit", 100)),
            offset=int(params.get("offset", 0)),
            roots_only=bool(params.get("roots_only", False)),
        )
    if act == "class":
        return run_class_instances(
            client,
            class_name=str(params.get("class_name", "") or ""),
            superclass=str(params.get("superclass", "") or ""),
            scope=str(params.get("scope", "auto") or "auto"),
            limit=int(params.get("limit", 100)),
        )
    if act == "property":
        return run_find_by_property(
            client,
            property_name=str(params.get("property_name", "") or ""),
            property_value=str(params.get("property_value", "") or ""),
            class_filter=str(params.get("class_filter", "") or ""),
        )
    if act == "unhidden_objects":
        return run_unhidden_objects(client)
    if act == "selection":
        return run_selection(
            client,
            detail=str(params.get("detail", "full") or "full"),
            max_items=int(params.get("max_items", 50)),
        )
    return run_delta(
        client,
        capture=bool(params.get("capture", False)),
        unchanged_since=int(params.get("unchanged_since", 0) or 0),
    )


def run_overview(client: MaxClient, max_roots: int = 50) -> str:
    if client.native_available:
        try:
            payload: dict[str, object] = {}
            if max_roots != 50:
                payload["max_roots"] = max_roots
            response = client.send_command(
                json.dumps(payload) if payload else "",
                cmd_type="native:scene_snapshot",
            )
            return response.get("result", "{}")
        except RuntimeError:
            pass

    maxscript = _overview_maxscript(max_roots)
    response = client.send_command(maxscript)
    return response.get("result", "{}")


def run_filter(
    client: MaxClient,
    class_name: str = "",
    pattern: str = "",
    layer: str = "",
    limit: int = 100,
    offset: int = 0,
    roots_only: bool = False,
) -> str:
    if client.native_available:
        try:
            has_filter = class_name or pattern or layer or roots_only or offset
            if not has_filter:
                snapshot = fetch_scene_snapshot(client)
                return json.dumps(scene_info_summary_from_snapshot(snapshot))

            params: dict[str, object] = {}
            if class_name:
                params["class_name"] = class_name
            if pattern:
                params["pattern"] = pattern
            if layer:
                params["layer"] = layer
            if roots_only:
                params["roots_only"] = True
            if limit != 100:
                params["limit"] = limit
            if offset:
                params["offset"] = offset
            response = client.send_command(json.dumps(params), cmd_type="native:scene_info")
            return response.get("result", "{}")
        except RuntimeError:
            pass

    has_filter = class_name or pattern or layer or roots_only
    if not has_filter and offset == 0:
        response = client.send_command(_filter_summary_maxscript())
        return response.get("result", "{}")

    try:
        response = client.send_command(
            _filter_list_maxscript(class_name, pattern, layer, roots_only, limit, offset)
        )
    except RuntimeError as exc:
        return json.dumps({
            "error": "MCP_SceneManage.filterObjects() failed — 3ds Max MCP server is incomplete (mcp_SceneManage.ms not loaded)",
            "detail": str(exc),
        })
    return response.get("result", '{"totalMatched":0,"objects":[]}')


def run_unhidden_objects(client: MaxClient) -> str:
    """List every unhidden scene node grouped by MAXScript class.

    Delegates to the Max-side struct method ``MCP_SceneManage.getSceneUnhiddenObjects()``
    (defined in maxscript/mcp/mcp_SceneManage.ms). It walks ``objects where not isHidden``,
    groups node names by ``(classOf o) as string`` (first-seen order), and returns::

        {"type_count": 2, "total": 5,
         "types": {"Editable_Mesh": {"count": 3, "objects": ["Box01", ...]},
                   "Biped_Object": {"count": 2, "objects": ["Bip01", ...]}}}
    """
    try:
        response = client.send_command("MCP_SceneManage.getSceneUnhiddenObjects()")
    except RuntimeError as exc:
        return json.dumps({
            "error": "MCP_SceneManage.getSceneUnhiddenObjects() failed — 3ds Max MCP server is incomplete (mcp_SceneManage.ms not loaded)",
            "detail": str(exc),
        })
    return response.get("result", "{}")


def run_selection(client: MaxClient, detail: str = "full", max_items: int = 50) -> str:
    normalized = (detail or "full").strip().lower()
    if normalized in {"compact", "lite", "basic"}:
        if client.native_available:
            try:
                response = client.send_command("", cmd_type="native:selection")
                return response.get("result", "[]")
            except RuntimeError:
                pass
        # MCP_SceneManage is loaded by mcp_autostart.ms; if it is missing the
        # Max-side MCP scripts are incomplete — surface that instead of silently
        # falling back to an inline copy of the logic.
        try:
            response = client.send_command(_selection_compact_maxscript())
        except RuntimeError as exc:
            return json.dumps({
                "error": "MCP_SceneManage.getSelectionCompact() failed — 3ds Max MCP server is incomplete (mcp_SceneManage.ms not loaded)",
                "detail": str(exc),
            })
        return response.get("result", "[]")

    if client.native_available:
        try:
            payload: dict[str, object] = {}
            if max_items != 50:
                payload["max_items"] = max_items
            response = client.send_command(
                json.dumps(payload) if payload else "",
                cmd_type="native:selection_snapshot",
            )
            return response.get("result", "{}")
        except RuntimeError:
            pass

    # MCP_SceneManage is loaded by mcp_autostart.ms; if it is missing the
    # Max-side MCP scripts are incomplete — surface that to the user.
    try:
        response = client.send_command(_selection_full_maxscript(max_items))
    except RuntimeError as exc:
        return json.dumps({
            "error": "MCP_SceneManage.getSelectionFull() failed — 3ds Max MCP server is incomplete (mcp_SceneManage.ms not loaded)",
            "detail": str(exc),
        })
    return response.get("result", "{}")


def run_delta(client: MaxClient, capture: bool = False, unchanged_since: int = 0) -> str:
    if client.native_available:
        try:
            payload: dict[str, object] = {}
            if capture:
                payload["capture"] = True
            if unchanged_since > 0:
                payload["unchanged_since"] = unchanged_since
            response = client.send_command(
                json.dumps(payload) if payload else "",
                cmd_type="native:scene_delta",
            )
            return response.get("result", "{}")
        except RuntimeError:
            pass

    global _previous_snapshot
    current = _capture_scene_state(client)

    if _previous_snapshot is None or capture:
        _previous_snapshot = current
        return json.dumps({"baseline": True, "objectCount": len(current)})

    prev_handles = set(_previous_snapshot.keys())
    curr_handles = set(current.keys())

    added = sorted(
        ({"name": current[h]["name"], "class": current[h]["c"]} for h in curr_handles - prev_handles),
        key=lambda o: o["name"],
    )
    removed = sorted(
        ({"name": _previous_snapshot[h]["name"], "class": _previous_snapshot[h]["c"]} for h in prev_handles - curr_handles),
        key=lambda o: o["name"],
    )

    modified = []
    for h in curr_handles & prev_handles:
        changes = _diff_objects(_previous_snapshot[h], current[h])
        if changes:
            modified.append({"name": current[h]["name"], **changes})
    modified.sort(key=lambda o: o["name"])

    _previous_snapshot = current

    return json.dumps({
        "added": added,
        "removed": removed,
        "modified": modified,
        "counts": {
            "added": len(added),
            "removed": len(removed),
            "modified": len(modified),
            "total": len(current),
        },
    })


def run_class_instances(
    client: MaxClient,
    class_name: str,
    superclass: str = "",
    scope: str = "auto",
    limit: int = 100,
) -> str:
    if not class_name and not superclass:
        return json.dumps({"error": "class_name is required unless superclass is set"})

    normalized_scope = (scope or "auto").strip().lower()
    if normalized_scope not in {"auto", "nodes", "refs"}:
        return json.dumps({"error": "scope must be auto, nodes, or refs"})

    if superclass:
        normalized_scope = "refs"

    if normalized_scope in {"auto", "nodes"} and client.native_available and not superclass:
        try:
            native = fetch_class_instances_native(client, class_name, limit=limit)
            total = int(native.get("totalFound", 0))
            if normalized_scope == "nodes" or total > 0:
                return json.dumps(native)
        except RuntimeError:
            if normalized_scope == "nodes":
                raise

    if normalized_scope == "nodes":
        return json.dumps({
            "className": class_name,
            "totalFound": 0,
            "instances": [],
            "hint": "No scene nodes matched. Retry with scope='refs' for materials/maps/modifiers.",
        })

    response = client.send_command(
        _class_instances_refs_maxscript(class_name, superclass, limit)
    )
    return response.get("result", "")


def run_find_by_property(
    client: MaxClient,
    property_name: str,
    property_value: str = "",
    class_filter: str = "",
) -> str:
    if not property_name:
        return json.dumps({"error": "property_name is required"})

    if client.native_available:
        try:
            payload = {
                "property_name": property_name,
                "property_value": property_value,
                "class_filter": class_filter,
            }
            response = client.send_command(
                json.dumps(payload),
                cmd_type="native:find_objects_by_property",
            )
            return response.get("result", "[]")
        except RuntimeError:
            pass

    response = client.send_command(
        _find_by_property_maxscript(property_name, property_value, class_filter)
    )
    return response.get("result", "[]")


def _overview_maxscript(max_roots: int) -> str:
    return r"""(
        local esc = MCP_Server.escapeJsonString
        local totalCount = objects.count
        local hiddenCount = 0
        local frozenCount = 0
        local classNames = #()
        local classCounts = #()
        local matNames = #()
        local matCounts = #()
        local modNames = #()
        local modCounts = #()
        local rootNames = #()
        local layerNames = #()

        for obj in objects do (
            if obj.isHidden do hiddenCount += 1
            if obj.isFrozen do frozenCount += 1

            local cn = (classOf obj) as string
            local cidx = findItem classNames cn
            if cidx == 0 then (append classNames cn; append classCounts 1)
            else classCounts[cidx] += 1

            if obj.material != undefined do (
                local mn = obj.material.name
                local midx = findItem matNames mn
                if midx == 0 then (append matNames mn; append matCounts 1)
                else matCounts[midx] += 1
            )

            for m = 1 to obj.modifiers.count do (
                local modCls = (classOf obj.modifiers[m]) as string
                local modIdx = findItem modNames modCls
                if modIdx == 0 then (append modNames modCls; append modCounts 1)
                else modCounts[modIdx] += 1
            )

            if obj.parent == undefined do append rootNames obj.name

            local ln = obj.layer.name
            if (findItem layerNames ln) == 0 do append layerNames ln
        )

        local classPairs = ""
        for i = 1 to classNames.count do (
            if i > 1 do classPairs += ","
            classPairs += "\"" + (esc classNames[i]) + "\":" + (classCounts[i] as string)
        )

        local matPairs = ""
        for i = 1 to matNames.count do (
            if i > 1 do matPairs += ","
            matPairs += "\"" + (esc matNames[i]) + "\":" + (matCounts[i] as string)
        )

        local modPairs = ""
        for i = 1 to modNames.count do (
            if i > 1 do modPairs += ","
            modPairs += "\"" + (esc modNames[i]) + "\":" + (modCounts[i] as string)
        )

        local rootArr = ""
        local rootCap = amin #(rootNames.count, """ + str(max_roots) + r""")
        for i = 1 to rootCap do (
            if i > 1 do rootArr += ","
            rootArr += "\"" + (esc rootNames[i]) + "\""
        )

        local layerArr = ""
        for i = 1 to layerNames.count do (
            if i > 1 do layerArr += ","
            layerArr += "\"" + (esc layerNames[i]) + "\""
        )

        "{\"objectCount\":" + (totalCount as string) + \
        ",\"classCounts\":{" + classPairs + "}" + \
        ",\"materials\":{" + matPairs + "}" + \
        ",\"modifiers\":{" + modPairs + "}" + \
        ",\"layers\":[" + layerArr + "]" + \
        ",\"hiddenCount\":" + (hiddenCount as string) + \
        ",\"frozenCount\":" + (frozenCount as string) + \
        ",\"roots\":[" + rootArr + "]" + \
        ",\"rootCount\":" + (rootNames.count as string) + \
        "}"
    )"""


def _filter_summary_maxscript() -> str:
    return r"""(
        local esc = MCP_Server.escapeJsonString
        local totalCount = objects.count
        local hiddenCount = 0
        local frozenCount = 0
        local classMap = #()
        local classNames = #()
        local layerNames = #()
        for obj in objects do (
            if obj.isHidden do hiddenCount += 1
            if obj.isFrozen do frozenCount += 1
            local cn = (classOf obj) as string
            local idx = findItem classNames cn
            if idx == 0 then (
                append classNames cn
                append classMap 1
            ) else (
                classMap[idx] += 1
            )
            local ln = obj.layer.name
            if (findItem layerNames ln) == 0 do append layerNames ln
        )
        local classPairs = ""
        for i = 1 to classNames.count do (
            if i > 1 do classPairs += ","
            classPairs += "\"" + (esc classNames[i]) + "\":" + (classMap[i] as string)
        )
        local layerList = ""
        for i = 1 to layerNames.count do (
            if i > 1 do layerList += ","
            layerList += "\"" + (esc layerNames[i]) + "\""
        )
        "{\"totalObjects\":" + (totalCount as string) + \
        ",\"classCounts\":{" + classPairs + "}" + \
        ",\"layers\":[" + layerList + "]" + \
        ",\"hiddenCount\":" + (hiddenCount as string) + \
        ",\"frozenCount\":" + (frozenCount as string) + "}"
    )"""


def _filter_list_maxscript(
    class_name: str,
    pattern: str,
    layer: str,
    roots_only: bool,
    limit: int,
    offset: int,
) -> str:
    """MAXScript call that lists scene nodes filtered by class/name/layer.

    Delegates to the Max-side struct method ``MCP_SceneManage.filterObjects``
    (defined in maxscript/mcp/mcp_SceneManage.ms). It scans the whole scene
    (``objects``) applying, in order:

    - class_name: exact match on ``(classOf obj) as string``
    - pattern:    ``matchPattern obj.name pattern:<pattern>``
    - layer:      exact match on ``obj.layer.name``
    - roots_only: keep only nodes with ``obj.parent == undefined``

    then pages results with ``offset``/``limit`` and emits per node:
    name/class/position/parent/numChildren/isHidden/isFrozen/layer. The
    position uses point3ToJson with a safe fallback for ``Biped_Object``.

    Returns a JSON object string::

        {"totalMatched": 2, "objects": [{"name": "...", "class": "...",
         "position": [0,1,2], "parent": null, "numChildren": 0,
         "isHidden": false, "isFrozen": false, "layer": "Layer0"}]}
    """

    def _ms_str(s: str) -> str:
        # Escape for a double-quoted MAXScript string literal.
        return s.replace("\\", "\\\\").replace('"', '\\"')

    return (
        "MCP_SceneManage.filterObjects "
        f'class_name:"{_ms_str(class_name)}" '
        f'pattern:"{_ms_str(pattern)}" '
        f'layer:"{_ms_str(layer)}" '
        f"roots_only:{'true' if roots_only else 'false'} "
        f"limit:{int(limit)} offset:{int(offset)}"
    )


def _selection_compact_maxscript() -> str:
    """MAXScript call that dumps the current scene selection as a compact JSON array.

    Delegates to the Max-side struct method ``MCP_SceneManage.getSelectionCompact()``
    (defined in maxscript/mcp/mcp_SceneManage.ms). For every node currently in the
    scene selection (``selection``) it reads:

    - name:      node name (``obj.name``)
    - class:     MAXScript class name (``classOf obj``)
    - position:  world-space position [x, y, z] (``obj.pos``; falls back to
                 ``obj.position`` / [0,0,0] for nodes such as ``Biped_Object``
                 that do not expose a ``.pos`` property)
    - wirecolor: wireframe color [r, g, b] (``obj.wirecolor``)

    Returns a JSON array string, e.g.::

        [{"name":"Box01","class":"Editable_Mesh","position":[0,1,2],"wirecolor":[128,128,128]}]
    """
    return "MCP_SceneManage.getSelectionCompact()"


def _selection_full_maxscript(max_items: int) -> str:
    """MAXScript call that dumps the current scene selection as a full JSON object.

    Delegates to the Max-side struct method ``MCP_SceneManage.getSelectionFull max_items:``
    (defined in maxscript/mcp/mcp_SceneManage.ms). For up to ``max_items`` nodes
    currently in the scene selection (``selection``) it reads:

    - name:      node name (``obj.name``)
    - class:     MAXScript class name (``classOf obj``)
    - parent:    parent node name, or null for roots (``obj.parent.name``)
    - material:  material name, or null when unassigned (``obj.material.name``)
    - modifiers: class names of all modifiers (``obj.modifiers``)
    - pos:       world-space position [x, y, z] (``obj.pos``; falls back to
                 ``obj.position`` / [0,0,0] for nodes such as ``Biped_Object``
                 that do not expose a ``.pos`` property)
    - bbox:      world-space bounding box [[minX,minY,minZ],[maxX,maxY,maxZ]]
                 (``nodeGetBoundingBox obj (matrix3 1)``)

    Returns a JSON object string::

        {"selected": 2, "objects": [{"name": "...", "class": "...", "parent": null,
         "material": null, "modifiers": [...], "pos": [0,1,2], "bbox": [[..],[..]]}]}
    """
    return f"MCP_SceneManage.getSelectionFull max_items:{int(max_items)}"


def _capture_scene_state(client: MaxClient) -> dict:
    """Capture a JSON map of every scene node keyed by AnimHandle, for delta diffing.

    Delegates to the Max-side struct method ``MCP_SceneManage.captureSceneState()``
    (defined in maxscript/mcp/mcp_SceneManage.ms). For every node in the scene
    (``objects``) it reads:

    - name:  node name (``obj.name``)
    - c:     MAXScript class name (``classOf obj``)
    - p:     world-space position [x, y, z] (``obj.pos``; falls back to
             ``obj.position`` / [0,0,0] for nodes such as ``Biped_Object``)
    - m:     material name, "" when unassigned (``obj.material.name``)
    - n:     modifier count (``obj.modifiers.count``)
    - h:     hidden flag (``obj.isHidden``)

    Returns a dict keyed by node AnimHandle, e.g.::

        {"12345": {"name": "Box01", "c": "Editable_Mesh", "p": [0,1,2],
                   "m": "", "n": 0, "h": false}}
    """
    try:
        response = client.send_command("MCP_SceneManage.captureSceneState()")
    except RuntimeError as exc:
        raise RuntimeError(
            "MCP_SceneManage.captureSceneState() failed — 3ds Max MCP server is incomplete (mcp_SceneManage.ms not loaded)"
        ) from exc
    return json.loads(response.get("result", "{}"))


def _round_pos(pos: list) -> list:
    return [round(v, 1) for v in pos]


def _diff_objects(prev: dict, curr: dict) -> dict:
    changes = {}
    if prev["c"] != curr["c"]:
        changes["class"] = {"from": prev["c"], "to": curr["c"]}
    if _round_pos(prev["p"]) != _round_pos(curr["p"]):
        changes["position"] = {"from": prev["p"], "to": curr["p"]}
    if prev["m"] != curr["m"]:
        changes["material"] = {"from": prev["m"] or None, "to": curr["m"] or None}
    if prev["n"] != curr["n"]:
        changes["modifierCount"] = {"from": prev["n"], "to": curr["n"]}
    if prev["h"] != curr["h"]:
        changes["hidden"] = {"from": prev["h"], "to": curr["h"]}
    return changes


def _class_instances_refs_maxscript(class_name: str, superclass: str, limit: int) -> str:
    max_show = min(limit, 50)
    if superclass:
        safe_sc = safe_string(superclass)
        return f"""(
            local result = "{{\\\"superclass\\\": \\\"" + "{safe_sc}" + "\\\", \\\"classes\\\": ["
            local scls = execute "{safe_sc}"
            if scls == undefined then (
                "{{\\\"error\\\": \\\"Unknown superclass: {safe_sc}\\\"}}"
            ) else (
                local allClasses = scls.classes
                local entries = #()
                for c in allClasses do (
                    local insts = getclassinstances c
                    if insts.count > 0 do (
                        local entry = "{{\\\"class\\\": \\\"" + (c as string) + "\\\", \\\"count\\\": " + (insts.count as string)
                        local nodeNames = #()
                        local maxCheck = amin #(insts.count, 5)
                        for i = 1 to maxCheck do (
                            local depNodes = refs.dependentnodes insts[i]
                            for n in depNodes do (
                                if (finditem nodeNames n.name) == 0 do append nodeNames n.name
                            )
                        )
                        entry += ", \\\"sampleNodes\\\": ["
                        local maxNames = amin #(nodeNames.count, 10)
                        for i = 1 to maxNames do (
                            if i > 1 do entry += ","
                            entry += "\\\"" + nodeNames[i] + "\\\""
                        )
                        entry += "]}}"
                        append entries entry
                    )
                )
                for i = 1 to entries.count do (
                    if i > 1 do result += ","
                    result += entries[i]
                )
                result += "]}}"
                result
            )
        )"""

    safe_cls = safe_string(class_name)
    return f"""(
        local cls = execute "{safe_cls}"
        if cls == undefined then (
            "{{\\\"error\\\": \\\"Unknown class: {safe_cls}\\\"}}"
        ) else (
            local insts = getclassinstances cls
            local result = "{{\\\"class\\\": \\\"" + (cls as string) + "\\\", \\\"count\\\": " + (insts.count as string) + ", \\\"instances\\\": ["
            local maxShow = amin #(insts.count, {max_show})
            for i = 1 to maxShow do (
                if i > 1 do result += ","
                local inst = insts[i]
                local instName = ""
                try (instName = inst.name) catch (try (instName = (exprForMAXObject inst)) catch (instName = (classof inst) as string))
                local depNodes = refs.dependentnodes inst
                local nodeArr = "["
                local maxNodes = amin #(depNodes.count, 5)
                for j = 1 to maxNodes do (
                    if j > 1 do nodeArr += ","
                    nodeArr += "\\\"" + depNodes[j].name + "\\\""
                )
                nodeArr += "]"
                result += "{{\\\"name\\\": \\\"" + instName + "\\\", \\\"usedByNodes\\\": " + nodeArr + "}}"
            )
            result += "]}}"
            result
        )
    )"""


def _find_by_property_maxscript(
    property_name: str,
    property_value: str,
    class_filter: str,
) -> str:
    safe_prop = safe_string(property_name)
    safe_val = safe_string(property_value)
    class_cond = ""
    if class_filter:
        safe_class = safe_string(class_filter)
        class_cond = f'and (matchPattern ((classof obj) as string) pattern:"*{safe_class}*")'

    if property_value:
        return f"""(
            local matched = #()
            for obj in objects {class_cond} do (
                try (
                    local val = getproperty obj #{safe_prop}
                    if (val as string) == "{safe_val}" or (toLower (val as string)) == (toLower "{safe_val}") do
                        append matched obj
                ) catch ()
            )
            local result = "["
            for i = 1 to matched.count do (
                if i > 1 do result += ","
                result += "\\\"" + matched[i].name + "\\\""
            )
            result += "]"
            result
        )"""

    return f"""(
        local matched = #()
        for obj in objects {class_cond} do (
            try (
                local val = getproperty obj #{safe_prop}
                append matched #(obj.name, val as string)
            ) catch ()
        )
        local result = "["
        for i = 1 to matched.count do (
            if i > 1 do result += ","
            result += "{{\\\"name\\\": \\\"" + matched[i][1] + "\\\", \\\"value\\\": \\\"" + matched[i][2] + "\\\"}}"
        )
        result += "]"
        result
    )"""
