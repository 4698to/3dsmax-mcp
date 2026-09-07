"""Explicit provider annotations; SDK ranges are never treated as enum lists."""
VRAY_LIGHT = (1012233633, 1607860959)
VRAY_SHAPES = {"rectangle": 0, "environment": 1, "sphere": 2, "disk": 4}
VRAY_UNITS = {"renderer": 0, "lm": 1, "cd/m2": 2}
OCTANE_LIGHT = (592523983, 1640440069)
OCTANE_SHAPES = {"rectangle": 0, "disk": 1, "sphere": 3, "cylinder": 4}
VRAY_IMAGE = (1734939723, 46203261)
VRAY_IMAGE_MAPPING = {"angular": 0, "cubic": 1, "spherical": 2, "mirrored_ball": 3, "max_standard": 4}
_VRAY_IMAGE_DOMAINS = {
    (0, 2, "mapType"): VRAY_IMAGE_MAPPING,
    (0, 25, "color_space"): {"none": 0, "inverse_gamma": 1, "srgb": 2, "from_max": 3, "auto": 4},
    (3, 41, "rgbColorSpace"): {"default": 0, "srgb": 1, "acescg": 2, "raw": 3},
}

# Independent source: Chaos's shipped scripts/VRay-VRayLightLister.mcr.
# Its Type dropdown uses (type + 1): Plane, Dome, Sphere, Mesh, Disc.
# Its Units dropdown similarly uses normalizeColor + 1.
_VRAY_DOMAINS = {
    (0, 1, "type"): {"rectangle": 0, "dome": 1, "sphere": 2, "mesh": 3, "disk": 4},
    (0, 12, "normalizeColor"): {"renderer": 0, "lm": 1, "cd/m2": 2, "W": 3, "W/m2/sr": 4},
    (0, 39, "color_mode"): {"rendering_rgb": 0, "kelvin": 1},
}


def annotate(data: dict) -> dict:
    identity = data.get("identity", {})
    superclass = identity.get("superclass_id")
    ids=tuple(identity.get("class_id", []))
    if superclass==48 and ids==VRAY_LIGHT:
        domains,evidence=_VRAY_DOMAINS,"Chaos VRay-VRayLightLister.mcr"
    elif superclass==48 and ids==OCTANE_LIGHT:
        domains,evidence={(0,14768,"analyticLightType"):OCTANE_SHAPES},"Octane 2026.3 Type combo item data (Quad=0, Disc=1, Sphere=3, Tube=4)"
    elif superclass==3088 and ids==VRAY_IMAGE:
        domains,evidence=_VRAY_IMAGE_DOMAINS,"V-Ray 7 update 3 VRayBitmap Qt combo item data (mapping, transfer function and RGB primaries)"
    else:
        return data
    for property in data.get("properties", []):
        ref = property.get("property_ref", {})
        choices = domains.get((ref.get("block_id"), ref.get("param_id"), property.get("name")))
        if choices and property.get("type") == "int":
            property["domain"] = {"kind": "enum", "choices": [{"name": k, "value": v} for k,v in choices.items()],
                "complete": True, "source": "provider_reference", "evidence": evidence}
    return data


def resolve_enum(property: dict, value):
    if not isinstance(value, dict) or set(value) != {"enum"}:
        return value
    domain = property.get("domain", {})
    choices = domain.get("choices") or []
    found = [x["value"] for x in choices if x["name"] == value["enum"]]
    if domain.get("kind") != "enum" or len(found) != 1:
        raise ValueError("UNKNOWN_ENUM: choose an exact named value published by the inspector; numeric ranges do not identify meanings.")
    return found[0]
