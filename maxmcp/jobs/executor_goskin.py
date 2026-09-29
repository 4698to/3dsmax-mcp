# -*- coding: utf-8 -*-
"""GoSkin 任务执行器（设计文档 docs/goskin-job-queue-design.md §7）。

executor **不经过** server.py 的 SessionRoutedClient（它按 MCP 会话路由）：
调度器用 ``acquire`` 拿到的 ``MaxClient`` 实例直接调用
``dialog_monitor.goskin_flow`` 的函数（这些函数本就要 client 参数，见
maxmcp/tools/goskin.py）。任务线程在每个安全点（§7.2）检查取消与整体超时。
"""
#2026-09-29
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

# 优先使用 checkout 的 dialog_monitor（与 maxmcp/tools/goskin.py 相同加载技巧，
# 避免命中过时的 site-packages 快照）。
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DM_DIR = _REPO_ROOT / "dialog_monitor"
if _DM_DIR.is_dir() and (_DM_DIR / "goskin_flow.py").is_file():
    root = str(_REPO_ROOT)
    while root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)
    for key in list(sys.modules):
        if key == "dialog_monitor" or key.startswith("dialog_monitor."):
            del sys.modules[key]

from dialog_monitor.goskin_flow import (  # noqa: E402
    TITLE_PATTERN,
    VENDOR_PATTERN,
    confirm_goskin_start,
    ensure_goskin_ready,
    run_goskin_skin,
)
from dialog_monitor.click_button import (  # noqa: E402
    max_window_state,
    restore_max_window,
)

from ..helpers.maxscript import safe_value  # noqa: E402
from .job_model import JobStatus  # noqa: E402


# --------------------------------------------------------------------------- #
# MAXScript 等价命令（镜像 maxmcp/tools/scene_manage.py 的命令构造，
# 但直接用 MaxClient 发送，不经过 MCP 工具层）。
# --------------------------------------------------------------------------- #

def _unwrap_maxscript_text(raw: Any) -> str:
    """去掉 MAXScript 字符串外层引号，便于解析内嵌 JSON。"""
    text = "" if raw is None else str(raw).strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1]
        text = (
            text.replace('\\"', '"')
            .replace("\\\\", "\\")
            .replace("\\n", "\n")
            .replace("\\r", "\r")
            .replace("\\t", "\t")
        )
    return text


def _parse_json(raw: Any) -> dict[str, Any]:
    text = _unwrap_maxscript_text(raw)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {"ok": False, "error": f"bad MaxScript JSON: {raw!r}", "raw": raw}
    if not isinstance(data, dict):
        return {
            "ok": False,
            "error": f"expected JSON object, got {type(data).__name__}",
            "raw": raw,
        }
    data.setdefault("ok", True)
    return data


def _exec_ms(client: Any, code: str, timeout: Optional[float] = None) -> str:
    response = client.send_command(code, cmd_type="maxscript", timeout=timeout)
    return response.get("result", "")


def _maxscript_int_array(values: list[int]) -> str:
    return "#(" + ", ".join(str(int(v)) for v in values) + ")"


def _load_scene(client: Any, file_path: str) -> dict[str, Any]:
    """等价 tools/scene_manage.load_scene 的 MaxScript 命令。

    MCP_SceneManage.loadScene 返回纯文本（"Loaded scene: <name>" 或
    "ERROR: ..."），不是 JSON；这里按文本约定解析，不要走 _parse_json。
    """
    fp = safe_value(file_path)
    if not fp.startswith("@"):
        fp = '@"' + fp.replace('"', '""') + '"'
    text = _exec_ms(client, f"MCP_SceneManage.loadScene {fp}").strip()
    if text.startswith("Loaded scene:"):
        return {"ok": True, "loaded": text[len("Loaded scene:"):].strip()}
    if text.startswith("ERROR:"):
        return {"ok": False, "error": text[len("ERROR:"):].strip()}
    return {"ok": False, "error": f"unexpected response: {text!r}"}


def _save_scene_as(client: Any, file_path: str) -> dict[str, Any]:
    """等价 tools/scene_manage.save_scene_as 的 MaxScript 命令。

    ``MCP_SceneManage.saveSceneAs`` 返回纯文本（保存路径或 "ERROR: ..."），
    直接保存到显式绝对路径（Max 侧自动建父目录）。这里按文本约定解析。
    """
    fp = safe_value(file_path)
    if not fp.startswith("@"):
        fp = '@"' + fp.replace('"', '""') + '"'
    text = _exec_ms(client, f"MCP_SceneManage.saveSceneAs {fp}").strip()
    if text.startswith("ERROR"):
        return {"ok": False, "error": text[len("ERROR:"):].strip() or text}
    return {"ok": True, "path": text}


def _attach_saved_output(
    result: dict[str, Any],
    client: Any,
    job: Any,
    output_dir: Optional[str],
    log: Callable[[str, str, str], None],
) -> None:
    """蒙皮完成后把场景另存为 {output_dir}/{job_id}.max 并挂到 result。

    产物路径/文件名写入 result 的 ``output_file``/``output_name``，由网页模板
    dlLinks 转成 /files/ 下载链接。保存失败只 warn、不判任务失败。
    """
    if not output_dir:
        log("save_output", "output_dir 未配置，跳过保存蒙皮结果", "warn")
        return
    try:
        dest = os.path.join(output_dir, f"{job.job_id}.max")
        saved = _save_scene_as(client, dest)
    except Exception as exc:  # noqa: BLE001
        log("save_output", f"保存蒙皮结果异常: {exc}", "warn")
        return
    if saved.get("ok"):
        log("save_output", f"已保存蒙皮结果: {saved.get('path')}")
        result["output_file"] = saved["path"]
        result["output_name"] = Path(saved["path"]).name
    else:
        log("save_output", f"保存蒙皮结果失败: {saved.get('error')}", "warn")


def _capture_viewport(client: Any, file_path: str) -> dict[str, Any]:
    """截取 Max 当前视口到 PNG（等价 viewport.py 的 _capture_viewport_to_file）。

    走 ``gw.getViewportDib`` 直接拿 DIB，不经 Windows 窗口捕获。Max 最小化时
    会返回 16×16 占位图，调用前需确保非最小化。
    """
    dirpart = os.path.dirname(file_path).replace(os.sep, "/") or "."
    fp = file_path.replace("\\", "/")
    ms = (
        f'(makeDir "{dirpart}" all:true; '
        "completeredraw(); "
        "local vp = gw.getViewportDib(); "
        f'vp.filename = "{fp}"; save vp; "OK")'
    )
    text = _exec_ms(client, ms).strip()
    if text.startswith("ERROR"):
        return {"ok": False, "error": text}
    return {"ok": True, "path": file_path}


def _attach_viewport_capture(
    result: dict[str, Any],
    client: Any,
    job: Any,
    output_dir: Optional[str],
    log: Callable[[str, str, str], None],
) -> None:
    """蒙皮完成后截取 Max 视口到 {output_dir}/{job_id}_viewport.png 并挂到 result。

    截图写入 result 的 ``viewport_file``/``viewport_name``，网页模板在结果分块
    直接渲染 <img>（/files/ 下载链路）。失败只 warn，不判任务失败。
    """
    if not output_dir:
        return
    try:
        # Max 最小化时 getViewportDib 只返回 16×16 占位；仅 iconic 时恢复窗口
        # （restore 只动最小化，不会把全屏/最大化窗口窗口化）。
        st = max_window_state(client=client)
        if st.get("iconic"):
            restore_max_window(client=client)
        dest = os.path.join(output_dir, f"{job.job_id}_viewport.png")
        shot = _capture_viewport(client, dest)
    except Exception as exc:  # noqa: BLE001
        log("capture_viewport", f"视口截图异常: {exc}", "warn")
        return
    if shot.get("ok"):
        log("capture_viewport", f"已保存视口截图: {shot.get('path')}")
        result["viewport_file"] = shot["path"]
        result["viewport_name"] = Path(shot["path"]).name
    else:
        log("capture_viewport", f"视口截图失败: {shot.get('error')}", "warn")


def _get_unhidden_meshes_bones(client: Any) -> dict[str, Any]:
    """等价 tools/scene_manage.get_unhidden_meshes_bones 的 MaxScript 命令。"""
    data = _parse_json(_exec_ms(client, "MCP_SceneManage.getunhidden_meshes_bones()"))
    if not data.get("ok"):
        return data
    return {
        "ok": True,
        "meshes": list(data.get("meshes") or []),
        "bones": list(data.get("bones") or []),
        "meshes_handle": list(data.get("meshes_handle") or []),
        "bones_handle": list(data.get("bones_handle") or []),
    }


def _propose_skin_bones(
    client: Any,
    mesh_handles: list[int],
    *,
    pad_ratio: float = 0.05,
    abs_padding: float = 0.0,
) -> dict[str, Any]:
    """等价 tools/scene_manage.propose_skin_bones（默认过滤规则）的 MaxScript 命令。

    v1 执行流程默认不调用（全量骨骼更保守），保留供后续复杂场景使用。
    """
    if not mesh_handles:
        return {"ok": True, "bones_handle": [], "error": None}
    pr = float(pad_ratio)
    ap = float(abs_padding)
    mesh_arr = _maxscript_int_array(mesh_handles)
    ms = (
        "(if MCP_SkinManage == undefined then "
        + '"{\\"ok\\":false,\\"code\\":\\"PLUGIN_MISSING\\",\\"error\\":\\"MCP_SkinManage not loaded; fileIn skin_Manage.ms\\"}" '
        + f"else MCP_SkinManage.proposeSkinBones {mesh_arr} boneHandles:undefined "
        + f"padRatio:{pr} absPadding:{ap} sampleCount:5)"
    )
    data = _parse_json(_exec_ms(client, ms))
    if data.get("ok") is False and "error" in data:
        return data
    data.setdefault("ok", True)
    data["bones_handle"] = list(data.get("bones_handle") or [])
    return data


# --------------------------------------------------------------------------- #
# 主执行入口
# --------------------------------------------------------------------------- #

#: 默认单步 OCR 等待上限（§7.4，随任务参数可覆盖；v1 Job 模型未带该字段）。
DEFAULT_COMPLETE_TIMEOUT_S = 300.0


def run_goskin_job(
    client: Any,
    job: Any,
    *,
    ocr_base: str,
    run_timeout_s: float = 1800.0,
    complete_timeout_s: float = DEFAULT_COMPLETE_TIMEOUT_S,
    output_dir: Optional[str] = None,
    callback: Optional[Callable[[str, str, str], None]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> dict[str, Any]:
    """按 §7 顺序执行一个 GoSkin 任务（阻塞直至完成/失败/安全点中止）。

    Args:
        client: 调度器从 InstanceManager acquire 到的 MaxClient（已绑定实例）。
        job: Job 对象（只读使用 scene_local_path / mesh_names / bone_names /
            confirm_mode / cancel_requested 等字段）。
        ocr_base: OCR 服务地址（由 scheduler 从配置解析）。
        run_timeout_s: 任务整体超时（§7.4 MAXMCP_JOB_RUN_TIMEOUT），<=0 不限。
        complete_timeout_s: 单步 OCR 等待上限（「开始蒙皮」等「完成」）。
        output_dir: 蒙皮完成后场景另存为的目录（通常为共享 workspace，
            /files/ 可直接下载）；为 None 时跳过保存。
        callback: 步骤日志回调 callback(step, note, level)；scheduler 写入
            job.log 并落盘。
        is_cancelled: 每安全点调用的取消探测；返回 True 则中止。

    Returns:
        供 scheduler 切换状态的字典：
          - {"ok": True,  "status": JobStatus.SUCCEEDED, "counts": ...,
             "output_file": ..., "output_name": ...}（auto 模式，含产物路径）
          - {"ok": True,  "status": JobStatus.AWAITING_CONFIRM,
             "confirmation": ..., "counts": ...}（manual 门禁，提示供人工确认）
          - {"ok": False, "status": JobStatus.FAILED, "code": ..., "error": ...}
          - {"ok": False, "status": JobStatus.CANCELLED}
          - {"ok": False, "status": JobStatus.FAILED, "code": "timeout", "error": ...}
    """
    log = callback or (lambda step, note, level="info": None)
    deadline = time.monotonic() + run_timeout_s if run_timeout_s and run_timeout_s > 0 else float("inf")

    def _abort(step_name: str) -> Optional[dict[str, Any]]:
        if is_cancelled is not None and is_cancelled():
            log(step_name, "收到取消请求，安全点中止", "warn")
            return {"ok": False, "status": JobStatus.CANCELLED}
        if time.monotonic() >= deadline:
            log(step_name, f"超过整体运行时限 {run_timeout_s:.0f}s", "error")
            return {"ok": False, "status": JobStatus.FAILED, "code": "timeout"}
        return None

    # 1. 恢复主窗口（最小化时 OCR/点击均失效；§7.1 步骤 2）。
    #    仅最小化时才需要 SW_RESTORE；全屏/最大化窗口保持原状——
    #    SW_RESTORE 会把最大化窗口也恢复为 normal（窗口化），并可能
    #    触发窗口重绘时序问题导致后续 OCR 截图失败。
    aborted = _abort("restore_window")
    if aborted is not None:
        return aborted
    try:
        win_state = max_window_state(client=client)
        if win_state.get("iconic"):
            restored = restore_max_window(client=client, restore_mode="restore")
            if restored.get("ok"):
                log("restore_window", "3ds Max 主窗口已从最小化恢复")
            else:
                log("restore_window", f"恢复窗口未确认成功: {restored.get('error')}", "warn")
        else:
            log("restore_window", "主窗口可见，保持当前窗口状态（不做 SW_RESTORE）")
    except Exception as exc:  # noqa: BLE001 恢复失败不阻塞任务，继续尝试
        log("restore_window", f"检查/恢复窗口异常（继续尝试）: {exc}", "warn")

    # 2. 加载场景（可选；scene_local_path 为空则沿用当前场景）
    if job.scene_local_path:
        aborted = _abort("load_scene")
        if aborted is not None:
            return aborted
        log("load_scene", f"加载场景 {job.scene_local_path}")
        loaded = _load_scene(client, job.scene_local_path)
        if not loaded.get("ok"):
            log("load_scene", f"加载失败: {loaded.get('error')}", "error")
            return {
                "ok": False,
                "status": JobStatus.FAILED,
                "code": "load_scene",
                "error": loaded.get("error"),
            }
        log("load_scene", "场景加载完成")
    else:
        log("load_scene", "scene_local_path 为空，沿用当前场景")

    # 3. 解析网格/骨骼：任务指定名称则用之；否则从场景自动读取全量
    aborted = _abort("resolve_meshes_bones")
    if aborted is not None:
        return aborted
    mesh_names = list(job.mesh_names) if job.mesh_names else None
    bone_names = list(job.bone_names) if job.bone_names else None
    mesh_handles: Optional[list[int]] = None
    bone_handles: Optional[list[int]] = None
    if not mesh_names and not bone_names:
        found = _get_unhidden_meshes_bones(client)
        if not found.get("ok"):
            log("resolve_meshes_bones", f"读取场景网格/骨骼失败: {found.get('error')}", "error")
            return {
                "ok": False,
                "status": JobStatus.FAILED,
                "code": "resolve",
                "error": found.get("error"),
            }
        mesh_names = found.get("meshes") or None
        bone_names = found.get("bones") or None
        mesh_handles = [int(h) for h in (found.get("meshes_handle") or [])] or None
        bone_handles = [int(h) for h in (found.get("bones_handle") or [])] or None
        log(
            "resolve_meshes_bones",
            f"自动解析：{len(mesh_names or [])} 个网格，{len(bone_names or [])} 个骨骼",
        )
    else:
        log(
            "resolve_meshes_bones",
            f"使用任务指定名称：{len(mesh_names or [])} 个网格，{len(bone_names or [])} 个骨骼",
        )

    # 4. 打开 GoSkin 对话框并切到 蒙皮/全局蒙皮（§7.1 步骤 1）
    aborted = _abort("ensure_ready")
    if aborted is not None:
        return aborted
    log("ensure_ready", "打开 GoSkin 并切换到 蒙皮/全局蒙皮")
    ensured = ensure_goskin_ready(
        client,
        title_pattern=TITLE_PATTERN,
        vendor_pattern=VENDOR_PATTERN,
        ocr_base=ocr_base,
    )
    if not ensured.get("ok"):
        log("ensure_ready", f"准备失败: {ensured.get('error')}", "error")
        return {
            "ok": False,
            "status": JobStatus.FAILED,
            "code": "ensure_ready",
            "error": ensured.get("error"),
        }
    log("ensure_ready", "GoSkin 就绪")

    # 5. 选网格/骨骼，停在「开始蒙皮」前（§7.1 步骤 4）
    aborted = _abort("run_skin")
    if aborted is not None:
        return aborted
    log("run_skin", "选择网格与骨骼（等待 OCR）")
    skin = run_goskin_skin(
        client,
        mesh_names=mesh_names,
        bone_names=bone_names,
        mesh_handles=mesh_handles,
        bone_handles=bone_handles,
        title_pattern=TITLE_PATTERN,
        vendor_pattern=VENDOR_PATTERN,
        ocr_base=ocr_base,
        click_start=False,
        require_counts=True,
        auto_cleanup=True,
    )
    if not skin.get("ok"):
        log("run_skin", f"选择网格/骨骼失败: {skin.get('error')}", "error")
        return {
            "ok": False,
            "status": JobStatus.FAILED,
            "code": "run_skin",
            "error": skin.get("error"),
        }
    counts = skin.get("counts") or {}
    log("run_skin", f"网格/骨骼已选定（关节数 {counts.get('joints')}）")
    confirmation = skin.get("confirmation") or {}

    # 6. 「开始蒙皮」门禁（§7.3）：manual 停住持租约；auto 自动点击
    if job.confirm_mode == "manual":
        log("run_skin", "manual 模式：停在 awaiting_confirm，等待确认动作")
        return {
            "ok": True,
            "status": JobStatus.AWAITING_CONFIRM,
            "confirmation": confirmation,
            "counts": counts,
        }

    aborted = _abort("confirm_start")
    if aborted is not None:
        return aborted
    log("confirm_start", "auto 模式：点击「开始蒙皮」并等待「完成」")
    started = confirm_goskin_start(
        client,
        user_confirmed=True,
        title_pattern=TITLE_PATTERN,
        vendor_pattern=VENDOR_PATTERN,
        ocr_base=ocr_base,
        complete_timeout_s=complete_timeout_s,
    )
    if not started.get("ok"):
        log("confirm_start", f"蒙皮失败: {started.get('error')}", "error")
        return {
            "ok": False,
            "status": JobStatus.FAILED,
            "code": "confirm_start",
            "error": started.get("error"),
        }
    log("confirm_start", "蒙皮完成")

    # 7. 保存蒙皮结果：场景另存为 {output_dir}/{job_id}.max，/files/ 可直接下载。
    #    保存失败不判任务失败（warn 继续），auto 模式不带 confirmation 提示。
    result: dict[str, Any] = {
        "ok": True,
        "status": JobStatus.SUCCEEDED,
        "counts": counts,
    }
    aborted = _abort("save_output")
    if aborted is not None:
        return aborted
    _attach_saved_output(result, client, job, output_dir, log)
    _attach_viewport_capture(result, client, job, output_dir, log)
    return result


def confirm_goskin_job_step(
    client: Any,
    job: Any,
    *,
    ocr_base: str,
    complete_timeout_s: float = DEFAULT_COMPLETE_TIMEOUT_S,
    output_dir: Optional[str] = None,
    callback: Optional[Callable[[str, str, str], None]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> dict[str, Any]:
    """manual 模式确认动作（§7.3）：对停在 awaiting_confirm 的任务点击「开始蒙皮」。

    复用 executor 持有的 MaxClient 与租约（场景已就绪）。成功后同样把场景
    另存为 {output_dir}/{job_id}.max（与 auto 分支一致）。返回结构与
    ``run_goskin_job`` 相同，供 scheduler 切换状态。
    """
    log = callback or (lambda step, note, level="info": None)
    if is_cancelled is not None and is_cancelled():
        log("confirm_start", "收到取消请求，放弃点击「开始蒙皮」", "warn")
        return {"ok": False, "status": JobStatus.CANCELLED}
    log("confirm_start", "收到确认：点击「开始蒙皮」并等待「完成」")
    started = confirm_goskin_start(
        client,
        user_confirmed=True,
        title_pattern=TITLE_PATTERN,
        vendor_pattern=VENDOR_PATTERN,
        ocr_base=ocr_base,
        complete_timeout_s=complete_timeout_s,
    )
    if not started.get("ok"):
        log("confirm_start", f"蒙皮失败: {started.get('error')}", "error")
        return {
            "ok": False,
            "status": JobStatus.FAILED,
            "code": "confirm_start",
            "error": started.get("error"),
        }
    log("confirm_start", "蒙皮完成")
    result: dict[str, Any] = {"ok": True, "status": JobStatus.SUCCEEDED}
    _attach_saved_output(result, client, job, output_dir, log)
    _attach_viewport_capture(result, client, job, output_dir, log)
    return result
