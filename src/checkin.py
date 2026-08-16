"""PT 网站签到：从 GitHub 读取加密 Cookie，解密后发送一次 GET。"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)


class VisibleTextParser(HTMLParser):
    """Extract visible HTML text without a third-party parser."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)


def read_secret(secret_id: str) -> str:
    """Read a secret without logging its value.

    Cloud Run uses the Secret Manager SDK. The gcloud fallback keeps local
    testing possible before the SDK dependencies are installed in the venv.
    """

    local_key_file = os.environ.get("MYCHECKBOX_COOKIE_KEY_FILE")
    if local_key_file:
        try:
            value = Path(local_key_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError("读取本地项目 Cookie Key 失败") from exc
        if not value:
            raise RuntimeError("本地项目 Cookie Key 为空")
        return value

    project_id = os.environ.get("GCP_PROJECT_ID") or os.environ.get(
        "GOOGLE_CLOUD_PROJECT"
    )
    if not project_id:
        raise RuntimeError("缺少 GCP_PROJECT_ID")

    try:
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
        result = client.access_secret_version(request={"name": name})
        value = result.payload.data.decode("utf-8").strip()
    except ModuleNotFoundError:
        try:
            result = subprocess.run(
                [
                    "gcloud",
                    "secrets",
                    "versions",
                    "access",
                    "latest",
                    f"--secret={secret_id}",
                    f"--project={project_id}",
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            value = result.stdout.strip()
        except Exception as exc:
            raise RuntimeError(f"读取 Secret 失败：{secret_id}") from exc
    except Exception as exc:
        raise RuntimeError(f"读取 Secret 失败：{secret_id}") from exc

    if not value:
        raise RuntimeError(f"Secret 为空：{secret_id}")
    return value


def decode_root_key(value: str) -> bytes:
    """Decode the locally generated 32-byte base64 project key."""

    try:
        key = base64.b64decode(value.strip(), validate=True)
    except Exception as exc:
        raise RuntimeError("项目 Cookie Key 不是有效的 base64") from exc
    if len(key) != 32:
        raise RuntimeError("项目 Cookie Key 必须解码为 32 字节")
    return key


def decrypt_cookie(site: dict[str, Any], payload: dict[str, Any], root_key: bytes) -> str:
    """Decrypt one site cookie using a site-separated AES-GCM key."""

    name = str(site["name"])
    if payload.get("format") != "mycheckbox-cookie-v1" or payload.get("site") != name:
        raise RuntimeError(f"{name}: 加密 Cookie 格式或站点名不匹配")

    try:
        salt = base64.b64decode(payload["salt"], validate=True)
        nonce = base64.b64decode(payload["nonce"], validate=True)
        ciphertext = base64.b64decode(payload["ciphertext"], validate=True)
        key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            info=f"mycheckbox-cookie-v1:{name}".encode(),
        ).derive(root_key)
        cookie = AESGCM(key).decrypt(nonce, ciphertext, name.encode()).decode("utf-8")
    except Exception as exc:
        raise RuntimeError(f"{name}: Cookie 解密失败") from exc

    if not cookie.strip():
        raise RuntimeError(f"{name}: 解密后的 Cookie 为空")
    return cookie.strip()


def load_encrypted_cookie(site: dict[str, Any], root_key: bytes) -> str:
    """Load an encrypted Cookie from local test storage or GitHub Raw."""

    name = str(site["name"])
    local_cookie_dir = os.environ.get("MYCHECKBOX_ENCRYPTED_COOKIE_DIR")
    if local_cookie_dir:
        local_path = Path(local_cookie_dir) / f"{name}.cookie.enc"
        try:
            payload = json.loads(local_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise RuntimeError(f"{name}: 读取本地加密 Cookie 失败") from exc
        except ValueError as exc:
            raise RuntimeError(f"{name}: 本地加密 Cookie 不是有效 JSON") from exc
        return decrypt_cookie(site, payload, root_key)

    cookie_url = str(site.get("encrypted_cookie_url", ""))
    if not cookie_url.startswith("https://raw.githubusercontent.com/"):
        raise RuntimeError(f"{name}: encrypted_cookie_url 必须是 GitHub Raw HTTPS 地址")

    try:
        response = requests.get(cookie_url, timeout=30)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"{name}: 从 GitHub 读取加密 Cookie 失败") from exc
    except ValueError as exc:
        raise RuntimeError(f"{name}: GitHub Cookie 文件不是有效 JSON") from exc

    return decrypt_cookie(site, payload, root_key)


def extract_account_info(html: str) -> dict[str, str]:
    """Extract the account summary shown after a successful login."""

    parser = VisibleTextParser()
    parser.feed(html)
    text = " ".join(" ".join(parser.parts).split())
    if "欢迎回来" not in text:
        return {}

    def find(pattern: str) -> str | None:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        return match.group(1).strip() if match else None

    info: dict[str, str] = {
        "username": find(r"欢迎回来\s*[,，]\s*([^\s\[\]<>]+)") or "unknown"
    }
    patterns = {
        "bonus": r"魔力值.*?[:：]\s*([\d,.]+)",
        "invites": r"邀请.*?[:：]\s*([\d+]+)",
        "ratio": r"分享率\s*[:：]\s*([\d.]+)",
        "hr": r"H\s*&\s*R\s*[:：]\s*([\d]+\s*/\s*[\d]+)",
        "uploaded": r"上传量\s*[:：]\s*([\d.]+\s*[A-Za-z]+)",
        "downloaded": r"下载量\s*[:：]\s*([\d.]+\s*[A-Za-z]+)",
        "seeding": r"当前活动.*?seeding\s*(\d+)",
        "leeching": r"当前活动.*?leeching\s*(\d+)",
        "connectable": r"可连接\s*[:：]\s*(是|否)",
        "connections": r"连接数\s*[:：]\s*([^\s]+)",
    }
    for key, pattern in patterns.items():
        value = find(pattern)
        if value is not None:
            info[key] = re.sub(r"\s*/\s*", "/", value) if key == "hr" else value
    current = re.search(r"当前活动\s*[:：]\s*(\d+)\s+(\d+)\s+可连接", text)
    if current:
        info.setdefault("seeding", current.group(1))
        info.setdefault("leeching", current.group(2))
    return info


def format_account_info(info: dict[str, str]) -> str:
    labels = {
        "username": "用户", "bonus": "魔力值", "invites": "邀请", "ratio": "分享率",
        "hr": "H&R", "uploaded": "上传", "downloaded": "下载", "seeding": "做种",
        "leeching": "下载中", "connectable": "可连接", "connections": "连接数",
    }
    return " | ".join(f"{labels[key]}={value}" for key, value in info.items())


def checkin(site: dict[str, Any], root_key: bytes) -> str:
    """Decrypt one site Cookie, then make one check-in GET request."""

    name = str(site["name"])
    base_url = str(site["url"]).rstrip("/")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError(f"{name}: url 必须是 HTTPS 地址")

    cookie = load_encrypted_cookie(site, root_key)
    response = requests.get(
        f"{base_url}/index.php?action=addbonus",
        headers={
            "Cookie": cookie,
            "User-Agent": str(site.get("user_agent") or os.environ.get("PTSCHOOL_UA", DEFAULT_USER_AGENT)),
            "Referer": f"{base_url}/",
        },
        timeout=float(os.environ.get("PTSCHOOL_TIMEOUT_SECONDS", "30")),
        allow_redirects=False,
    )
    text = response.text.casefold()

    if response.status_code in {301, 302, 303, 307, 308}:
        location = response.headers.get("Location", "").casefold()
        if "login.php" in location:
            raise RuntimeError(f"{name}: Cookie 已失效")
        raise RuntimeError(f"{name}: 返回重定向 HTTP {response.status_code}")
    if response.status_code != 200:
        if response.status_code == 403 or "cloudflare" in text or "just a moment" in text:
            raise RuntimeError(f"{name}: 被 Cloudflare 拦截")
        raise RuntimeError(f"{name}: HTTP错误 {response.status_code}")
    if "cloudflare" in text or "just a moment" in text:
        raise RuntimeError(f"{name}: 被 Cloudflare 拦截")
    if "login.php" in text or "你需要启用cookies才能登录" in text:
        raise RuntimeError(f"{name}: Cookie 已失效")

    info = extract_account_info(response.text)
    if "签到成功" in text:
        result = f"{name}: 签到成功"
    elif any(word in text for word in ("今日已经签过到", "今日已签到", "已经签到", "已签到")):
        result = f"{name}: 今日已经签到"
    elif info:
        result = f"{name}: 登录有效，页面未返回签到提示"
    else:
        raise RuntimeError(f"{name}: 无法判断签到结果")
    return f"{result} | {format_account_info(info)}" if info else result


def main() -> int:
    try:
        config_file = os.environ.get("MYCHECKBOX_SITES_FILE", str(PROJECT_ROOT / "sites.json"))
        sites = json.loads(Path(config_file).read_text(encoding="utf-8"))
        root_key = decode_root_key(
            read_secret(os.environ.get("MYCHECKBOX_COOKIE_KEY_SECRET_ID", "mycheckbox-cookie-key"))
        )
        failed = False
        for site in sites:
            try:
                print(checkin(site, root_key))
            except Exception as exc:
                failed = True
                print(f"签到失败：{exc}", file=sys.stderr)
        return 1 if failed else 0
    except Exception as exc:
        print(f"配置失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
