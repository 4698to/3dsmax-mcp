import argparse
import base64
import getpass
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from xml.sax.saxutils import quoteattr

UC_HOST = "https://aqapi.cn.ndhy.com"
IM_HOST = "https://imcoreapis.cn.ndhy.com"
TIME_URL = "https://uc-gateway.cn.ndhy.com/v1.1/time"
SDP_APP_ID = "b4fb92a0-af7f-49c2-b270-8f62afac1133"


def request_json(url, method="GET", body=None, headers=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request_headers = {"Content-Type": "application/json; charset=utf-8"}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(detail)
            reason = payload.get("message") or payload.get("code") or ""
        except (json.JSONDecodeError, AttributeError):
            reason = ""
        suffix = f": {reason}" if reason else ""
        raise RuntimeError(f"HTTP {error.code}{suffix}") from None
    except urllib.error.URLError as error:
        raise RuntimeError(f"请求失败：{error.reason}") from None
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def request_bytes(url, method="GET", body=None, headers=None, timeout=60):
    request = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(detail)
            reason = payload.get("message") or payload.get("code") or ""
        except (json.JSONDecodeError, AttributeError):
            reason = ""
        suffix = f": {reason}" if reason else ""
        raise RuntimeError(f"HTTP {error.code}{suffix}") from None
    except urllib.error.URLError as error:
        raise RuntimeError(f"请求失败：{error.reason}") from None


def salted_md5(password):
    value = password.encode("utf-8") + bytes((163, 172, 161, 163)) + b"fdjf,jkgfkl"
    return hashlib.md5(value).hexdigest()


def get_server_time():
    payload = request_json(TIME_URL)
    value = payload["sys_time"]
    if isinstance(value, (int, float)) or str(value).isdigit():
        timestamp = int(value)
        if timestamp > 1_000_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def mac_authorization(method, path_and_query, token, server_time):
    nonce = f"{int(server_time.timestamp() * 1000)}:{secrets.token_hex(4)}"
    normalized = f"{nonce}\n{method.upper()}\n{path_and_query}\nimcoreapis.cn.ndhy.com\n"
    signature = hmac.new(
        token["mac_key"].encode("utf-8"), normalized.encode("utf-8"), hashlib.sha256
    ).digest()
    mac = base64.b64encode(signature).decode("ascii")
    return f'MAC id="{token["access_token"]}",nonce="{nonce}",mac="{mac}"'


def send_message(sender, password, org_name, receiver, message):
    token = request_json(
        f"{UC_HOST}/v0.93/tokens",
        method="POST",
        body={
            "login_name": sender,
            "password": salted_md5(password),
            "org_name": org_name,
        },
    )
    server_time = get_server_time()
    common_headers = {
        "platform-type": "1",
        "sdp-app-id": SDP_APP_ID,
    }

    conversation_path = "/v1.0/api/conversations?" + urllib.parse.urlencode({"uid": receiver})
    print("正在请求收件人会话…", file=sys.stderr, flush=True)
    common_headers["Authorization"] = mac_authorization(
        "POST", conversation_path, token, server_time
    )
    conversation = request_json(
        f"{IM_HOST}{conversation_path}", method="POST", headers=common_headers
    )
    print(
        "会话接口响应体：\n" + json.dumps(conversation, ensure_ascii=False, indent=2),
        file=sys.stderr,
        flush=True,
    )
    if conversation.get("msgsendflag") is False:
        raise RuntimeError("该会话当前不允许发送消息（msgsendflag=false）")
    conv_id = conversation["conv_id"]

    message_path = f"/v1.0/api/conversations/{urllib.parse.quote(str(conv_id), safe='')}/messages"
    common_headers["Authorization"] = mac_authorization(
        "POST", message_path, token, server_time
    )
    now_ns = time.time_ns()
    msg_seq = (now_ns // 1_000_000_000 << 32) | (now_ns % 1_000_000_000 & 0x7FFFFFFF)
    content = f"Content-Type: text/plain\r\n\r\n{message}"
    response = request_json(
        f"{IM_HOST}{message_path}",
        method="POST",
        headers=common_headers,
        body={"content": content, "qos_flag": 0, "msg_seq": str(msg_seq), "resend_flag": 0},
    )
    return conv_id, response


def im_request(token, server_time, path, method="GET", body=None):
    headers = {
        "Authorization": mac_authorization(method, path, token, server_time),
        "platform-type": "1",
        "sdp-app-id": SDP_APP_ID,
    }
    return request_json(f"{IM_HOST}{path}", method=method, body=body, headers=headers)


def encode_multipart(fields, filename, file_data, mime_type):
    boundary = "----99uUploadBoundary" + secrets.token_hex(8)
    chunks = []
    for name, value in fields.items():
        chunks.extend((
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            str(value).encode("utf-8"),
            b"\r\n",
        ))
    safe_filename = filename.replace('"', "'").replace("\r", "_").replace("\n", "_")
    encoded_filename = urllib.parse.quote(filename.encode("utf-8"))
    chunks.extend((
        f"--{boundary}\r\n".encode(),
        (f'Content-Disposition: form-data; name="file"; filename="{safe_filename}"; '
         f"filename*=UTF-8''{encoded_filename}\r\n").encode("utf-8"),
        f"Content-Type: {mime_type}\r\n\r\n".encode(),
        file_data,
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def send_file(sender, password, org_name, receiver, file_path, upload_only=False):
    print("正在读取文件…", file=sys.stderr, flush=True)
    if not os.path.isfile(file_path):
        raise RuntimeError(f"文件不存在：{file_path}")
    filename = os.path.basename(file_path)
    with open(file_path, "rb") as source:
        file_data = source.read()
    file_size = len(file_data)
    file_md5 = hashlib.md5(file_data).hexdigest()

    print("正在登录 99U…", file=sys.stderr, flush=True)
    token = request_json(
        f"{UC_HOST}/v0.93/tokens",
        method="POST",
        body={"login_name": sender, "password": salted_md5(password), "org_name": org_name},
    )
    print("正在同步服务器时间并获取会话…", file=sys.stderr, flush=True)
    server_time = get_server_time()
    common_headers = {"platform-type": "1", "sdp-app-id": SDP_APP_ID}
    conversation_path = "/v1.0/api/conversations?" + urllib.parse.urlencode({"uid": receiver})
    print("正在请求收件人会话…", file=sys.stderr, flush=True)
    common_headers["Authorization"] = mac_authorization(
        "POST", conversation_path, token, server_time
    )
    conversation = request_json(
        f"{IM_HOST}{conversation_path}", method="POST", headers=common_headers
    )
    print(
        "会话接口响应体：\n" + json.dumps(conversation, ensure_ascii=False, indent=2),
        file=sys.stderr,
        flush=True,
    )
    if conversation.get("msgsendflag") is False:
        raise RuntimeError("该会话当前不允许发送消息（msgsendflag=false）")
    conv_id = str(conversation["conv_id"])

    conv_path = "/conv/" + urllib.parse.quote(conv_id, safe="")

    print("正在获取会话文件根路径…", file=sys.stderr, flush=True)
    roots = im_request(token, server_time, f"{conv_path}/roots")
    root_path = roots["root_path"].rstrip("/")
    storage_path = f"{root_path}/file/{filename}"
    print("正在申请文件上传凭证…", file=sys.stderr, flush=True)
    token_info = im_request(
        token,
        server_time,
        f"{conv_path}/files/actions/get_token",
        method="POST",
        body={"type": "UPLOAD_NORMAL", "biz": "0", "path": storage_path},
    )
    token_info = token_info.get("token_info", token_info)
    upload_token = token_info.get("token")
    policy = token_info.get("policy")
    upload_date = token_info.get("date") or token_info.get("date_time")
    if not upload_token or not policy or not upload_date:
        raise RuntimeError("原生 get_token 响应缺少 token、policy 或 date")

    fields = {
        "path": storage_path,
        "name": filename,
        "size": str(file_size),
        "scope": "1",
        "infoJson": json.dumps({"identify_param": {
            "strategy": "strategy_before_origin_illegal_replace",
            "sensitive_word_lib": SDP_APP_ID,
        }}, ensure_ascii=False, separators=(",", ":")),
        "md5": file_md5,
        "tokenParams": "{}",
    }
    body, content_type = encode_multipart(
        fields, filename, file_data, mimetypes.guess_type(filename)[0] or "application/octet-stream"
    )
    query = urllib.parse.urlencode({
        "supportCustomHeader": "true",
        "sdk": "js",
        "uploadChoiceCDN": "0",
        "token": upload_token,
        "policy": policy,
        "date": upload_date,
    })
    print(f"正在上传文件（{file_size} 字节）…", file=sys.stderr, flush=True)
    upload_result = json.loads(request_bytes(
        f"https://cs.cn.ndhy.com/v0.1/upload/actions/direct?{query}",
        method="POST",
        body=body,
        headers={"Content-Type": content_type},
    ).decode("utf-8"))
    dentry_id = upload_result.get("dentry_id")
    if not dentry_id:
        raise RuntimeError("文件存储服务未返回 dentry_id")
    uploaded_name = upload_result.get("name", filename)
    upload_params = upload_result.get("upload_params") or {}
    if upload_params.get("upload_url"):
        print("存储服务要求 S3 直传，正在上传…", file=sys.stderr, flush=True)
        request_bytes(
            upload_params["upload_url"],
            method="PUT",
            body=file_data,
            headers=upload_params.get("upload_headers", {}),
        )
        if upload_only:
            return conv_id, dentry_id, {"upload_only": True}
    elif upload_only:
        return conv_id, dentry_id, {"upload_only": True}

    print("上传完成，正在登记文件…", file=sys.stderr, flush=True)
    im_request(
        token,
        server_time,
        f"{conv_path}/files",
        method="POST",
        body={"biz": "0", "dentry_id": dentry_id, "name": uploaded_name},
    )

    print("文件已登记，正在发送文件消息…", file=sys.stderr, flush=True)
    message_path = f"/v1.0/api/conversations/{urllib.parse.quote(conv_id, safe='')}/messages"
    common_headers["Authorization"] = mac_authorization("POST", message_path, token, server_time)
    now_ns = time.time_ns()
    msg_seq = ((now_ns // 1_000_000_000) << 32) | (now_ns % 1_000_000_000 & 0x7FFFFFFF)
    xml_content = (
        "Content-Type: file/xml\r\n\r\n"
        f"<file src={quoteattr(str(dentry_id))} name={quoteattr(uploaded_name)} "
        f"size={quoteattr(str(file_size))} compressed=\"\" md5={quoteattr(file_md5)} />"
    )
    response = request_json(
        f"{IM_HOST}{message_path}",
        method="POST",
        headers=common_headers,
        body={"content": xml_content, "qos_flag": 0, "msg_seq": str(msg_seq), "resend_flag": 0},
    )
    return conv_id, dentry_id, response


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="登录 99U 并发送 UTF-8 文本或文件消息")
    parser.add_argument("receiver", help="接收方 99U 用户 ID")
    parser.add_argument("message", nargs="?", help="消息内容；省略时交互输入")
    parser.add_argument("--file", help="上传并发送本地文件")
    parser.add_argument("--upload-only", action="store_true", help="仅上传文件，不确认、登记或发送；须与 --file 同用")
    parser.add_argument("--sender", default=os.getenv("99U_LOGIN_NAME", "10030473"), help="发送账号")
    parser.add_argument("--org", default=os.getenv("99U_ORG_NAME", "ND"), help="组织名")
    args = parser.parse_args()

    if args.upload_only and not args.file:
        parser.error("--upload-only 必须与 --file 同用")
    if args.file and args.message is not None:
        parser.error("--file 与文本消息不能同时使用")
    if args.file and not os.path.isfile(args.file):
        parser.error(f"文件不存在：{args.file}")
    if not args.file and args.message is not None and not args.message.strip():
        parser.error("消息内容不能为空")

    password = os.getenv("99U_PASSWORD") or getpass.getpass("99U 密码（不会回显）: ")
    message = args.message
    if not args.file and message is None:
        message = input("消息内容: ")
    if not args.file and not message.strip():
        parser.error("消息内容不能为空")

    try:
        if args.file:
            conv_id, dentry_id, response = send_file(
                args.sender, password, args.org, args.receiver, args.file,
                upload_only=args.upload_only,
            )
            if args.upload_only:
                print(f"上传测试成功：会话 {conv_id}，文件 ID {dentry_id}；未确认、登记或发送")
            else:
                print(
                    f"文件发送成功：会话 {conv_id}，文件 ID {dentry_id}，"
                    f"消息 ID {response.get('conv_msg_id', '未知')}"
                )
        else:
            conv_id, response = send_message(
                args.sender, password, args.org, args.receiver, message
            )
            print(f"发送成功：会话 {conv_id}，消息 ID {response.get('conv_msg_id', '未知')}")
    except (RuntimeError, KeyError, json.JSONDecodeError) as error:
        print(f"发送失败：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
