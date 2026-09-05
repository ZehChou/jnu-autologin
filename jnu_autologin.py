#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JNU ePortal 校园网自动登录脚本(开源版)
=======================================
功能:
  1. 循环探测网络,掉线后自动重新登录
  2. 登录成功后定时发送 keepalive,防止空闲被踢
  3. WiFi 完全断开时持续重试,WiFi 自动重连后自动登录
  4. 自动完成门户 RSA 加密,只需提供校园网账号和密码即可使用

快速开始:
  python jnu_autologin.py --configure   # 首次配置:输入账号密码,生成 config.json
  python jnu_autologin.py               # 守护模式(一直运行,推荐)
  python jnu_autologin.py --selftest    # 端到端自检:下线 -> 自动重登
  python jnu_autologin.py --once        # 只跑一次探测 + 登录
  python jnu_autologin.py --logout      # 主动下线

依赖:仅 Python 3.8+ 标准库,无需安装任何第三方包。
凭证仅保存在本地 config.json 中,请勿将 config.json 提交或分享。
"""

import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

# 控制台尽量用 UTF-8 输出(出错也不影响)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")
LOG_FILE = os.path.join(SCRIPT_DIR, "jnu_autologin.log")

# ======================== 配置 ========================
DEFAULT_CONFIG = {
    "username": "",            # 校园网账号/学号
    "password": "",            # 校园网密码(明文,本地保存)
    "password_mode": "auto",   # auto | plain | cookie
    "password_encrypt": "",    # 可选:浏览器 Cookie 中的 EPORTAL_COOKIE_PASSWORD 密文
    "service": "",             # 认证服务,留空使用默认
    "portal": "https://webauthsa.jnu.edu.cn:8443/eportal",
    "check_interval": 5,       # 在线时探测间隔(秒)
    "retry_interval": 3,       # 登录失败重试间隔(秒)
    "keepalive_interval": 60,  # 保活间隔(秒),登录成功后会被服务器返回值覆盖
}

# 探测地址(必须用 http 明文,门户才能拦截到跳转)
PROBE_URLS = [
    "http://www.msftconnecttest.com/connecttest.txt",
    "http://www.baidu.com",
]
ONLINE_MARKER = "Microsoft Connect Test"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")

# ePortal 使用自签证书 + 老式加密套件,必须降级 SSL 安全等级才能握手
SSL_CTX = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE
try:
    SSL_CTX.set_ciphers("DEFAULT@SECLEVEL=0")
except Exception:
    SSL_CTX.set_ciphers("ALL")


# ======================== 日志 ========================
def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def mask_account(name):
    """账号打码后输出,避免日志泄露完整账号。"""
    if not name:
        return "(未设置)"
    if len(name) <= 4:
        return name[0] + "****"
    return name[:4] + "****" + name[-3:]


# ======================== 配置读写 ========================
def load_config():
    if not os.path.exists(CONFIG_FILE):
        return None
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise RuntimeError(f"无法读取 config.json: {e}") from e
    cfg = dict(DEFAULT_CONFIG)
    cfg.update({k: v for k, v in data.items() if k in DEFAULT_CONFIG})
    return cfg


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")


def validate_config(cfg):
    missing = []
    if not cfg.get("username"):
        missing.append("username")
    mode = cfg.get("password_mode", "auto")
    if mode == "cookie":
        if not cfg.get("password_encrypt"):
            missing.append("password_encrypt (cookie 模式)")
    else:
        if not cfg.get("password"):
            missing.append("password")
    if missing:
        raise RuntimeError("config.json 缺少字段: " + ", ".join(missing)
                           + "。请运行: python jnu_autologin.py --configure")


def configure():
    """交互式生成 config.json。"""
    import getpass
    print("=== JNU ePortal 自动登录 - 初始化配置 ===")
    print(f"配置文件: {CONFIG_FILE}")
    print("提示:账号密码仅保存在本机该文件中,请勿分享或提交到代码仓库。")
    username = input("校园网账号/学号: ").strip()
    password = getpass.getpass("校园网密码: ")
    print("密码提交方式: [1] 自动(RSA 加密,推荐) [2] 明文 [3] 使用浏览器 Cookie 密文")
    try:
        choice = input("请选择 [1]: ").strip() or "1"
    except EOFError:
        choice = "1"
    mode = {"1": "auto", "2": "plain", "3": "cookie"}.get(choice, "auto")
    cfg = dict(DEFAULT_CONFIG)
    cfg["username"] = username
    cfg["password"] = password
    cfg["password_mode"] = mode
    if mode == "cookie":
        cfg["password_encrypt"] = input("EPORTAL_COOKIE_PASSWORD 密文: ").strip()
    save_config(cfg)
    print(f"配置已保存: {CONFIG_FILE}")
    print("现在可以运行: python jnu_autologin.py")


# ======================== 门户密码加密(复刻官方 JS) ========================
def fetch_public_key(portal):
    """
    从门户登录页解析 RSA 公钥(隐藏域 #publicKey,格式: 指数&模数,均为十六进制)。
    返回 (exponent, modulus)。
    """
    url = portal.rstrip("/") + "/"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    html = urllib.request.urlopen(req, context=SSL_CTX, timeout=10).read()
    html = html.decode("utf-8", errors="ignore")
    m = re.search(r'publicKey[^>]*value=["\']([0-9a-fA-F]+)&([0-9a-fA-F]+)["\']', html)
    if not m:
        raise RuntimeError("登录页中未找到 RSA 公钥(publicKey)")
    return int(m.group(1), 16), int(m.group(2), 16)


def rsa_encrypt_password(password, exponent, modulus):
    """
    精确复刻门户 JS(security.js RSAUtils.encryptedString + login.js):
      1. 密码按 UTF-16 code unit 反转;
      2. 按 16 位小端组块(块大小 = 2*(模数字数-1),1024 位密钥为 126 字节),补零对齐;
      3. 每块作为小端大整数做裸 RSA 模幂(无 PKCS#1 填充);
      4. 输出十六进制,按 16 位数字(4 个 hex 字符)对齐,多块用空格连接。
    仅支持 ASCII 密码(门户 JS 对非 ASCII 会产生乱码)。
    """
    units = [ord(ch) for ch in password[::-1]]
    if any(u > 255 for u in units):
        raise ValueError("密码包含非 ASCII 字符,门户加密算法不支持,请使用明文模式(password_mode=plain)")
    num_digits = (modulus.bit_length() + 15) // 16
    chunk = 2 * (num_digits - 1)
    while len(units) % chunk:
        units.append(0)
    blocks = []
    for i in range(0, len(units), chunk):
        blk = units[i:i + chunk]
        val = int.from_bytes(bytes(blk), "little")
        c = pow(val, exponent, modulus)
        h = format(c, "x")
        # biToHex 按 16 位数字输出,每个数字固定 4 个 hex 字符
        h = h.rjust((len(h) + 3) // 4 * 4, "0")
        blocks.append(h)
    return " ".join(blocks)


# ======================== 网络探测 ========================
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁止自动跟随重定向,以便捕获门户跳转地址。"""

    def redirect_request(self, *a, **kw):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _qs_from_url(url):
    return urllib.parse.urlparse(url).query


def _qs_from_html(html):
    """从门户页面 HTML/JS 中提取 queryString。"""
    m = re.search(r'["\'](https?://[^"\']*\?[^"\']*)["\']', html)
    if m:
        q = _qs_from_url(m.group(1))
        if q:
            return q
    m = re.search(r'queryString\s*[:=]\s*["\']([^"\']+)["\']', html, re.I)
    if m:
        return m.group(1)
    return None


def probe():
    """
    探测当前是否已联网。
    返回 (online: bool, query_string: str|None, info: str)。
    掉线时 query_string 是从门户跳转/页面里抓到的参数,登录要用。
    """
    for url in PROBE_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            resp = _opener.open(req, timeout=3)
            body = resp.read(4096).decode("utf-8", errors="ignore")
            final_url = resp.geturl()
            if ONLINE_MARKER in body or ("baidu" in body.lower() and "eportal" not in body.lower()):
                return True, None, f"在线({url})"
            if "eportal" in body.lower() or "InterFace" in body:
                qs = _qs_from_html(body) or _qs_from_url(final_url)
                return False, qs, f"门户页面({url})"
        except urllib.error.HTTPError as e:
            loc = e.headers.get("Location", "")
            if loc:
                qs = _qs_from_url(loc)
                if not qs:
                    try:
                        r = urllib.request.Request(loc, headers={"User-Agent": UA})
                        b = _opener.open(r, timeout=3).read(4096).decode("utf-8", errors="ignore")
                        qs = _qs_from_html(b)
                    except Exception:
                        pass
                return False, qs, f"跳转-> {loc}"
        except Exception:
            continue
    return False, None, "探测全部失败(WiFi 可能完全断开)"


# ======================== 登录 / 保活 / 下线 ========================
def _post(url, fields, use_ssl=True):
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    })
    kw = {"timeout": 10}
    if use_ssl:
        kw["context"] = SSL_CTX
    resp = urllib.request.urlopen(req, **kw)
    return resp.read().decode("utf-8", errors="ignore")


def _login_fields(cfg, query_string):
    """按配置的密码模式构造登录表单字段。"""
    mode = cfg.get("password_mode", "auto")
    if mode == "cookie":
        password, encrypted = cfg.get("password_encrypt", ""), "true"
    elif mode == "plain":
        password, encrypted = cfg.get("password", ""), "false"
    else:  # auto: 优先 RSA 加密,失败回退明文
        try:
            exp, mod = fetch_public_key(cfg.get("portal") or DEFAULT_CONFIG["portal"])
            password = rsa_encrypt_password(cfg.get("password", ""), exp, mod)
            encrypted = "true"
        except Exception as e:
            log(f"RSA 加密不可用({e}),回退为明文提交")
            password, encrypted = cfg.get("password", ""), "false"
    return {
        "userId": cfg.get("username", ""),
        "password": password,
        "service": cfg.get("service", ""),
        "queryString": query_string or "",
        "operatorPwd": "",
        "operatorUserId": "",
        "validcode": "",
        "passwordEncrypt": encrypted,
    }


def login(query_string, cfg):
    portal = cfg.get("portal") or DEFAULT_CONFIG["portal"]
    text = _post(portal.rstrip("/") + "/InterFace.do?method=login",
                 _login_fields(cfg, query_string))
    try:
        return json.loads(text)
    except Exception:
        return {"result": "fail", "message": "返回非JSON: " + text[:300]}


def keepalive(user_index, portal):
    try:
        return json.loads(_post(portal.rstrip("/") + "/InterFace.do?method=keepalive",
                                {"userIndex": user_index}))
    except Exception as e:
        return {"result": "fail", "message": str(e)}


def get_user_index(portal):
    """通过本机 IP 识别当前会话,返回 userIndex(在线时有效)。"""
    try:
        r = json.loads(_post(portal.rstrip("/") + "/InterFace.do?method=getOnlineUserInfo",
                             {"userIndex": ""}))
        ui = r.get("userIndex")
        if ui and r.get("userId"):
            return ui
        return None
    except Exception:
        return None


def logout(user_index, portal):
    """用 userIndex 主动下线。"""
    try:
        return json.loads(_post(portal.rstrip("/") + "/InterFace.do?method=logout",
                                {"userIndex": user_index}))
    except Exception as e:
        return {"result": "fail", "message": str(e)}


# ======================== 主循环 ========================
def run_daemon(cfg):
    # 单实例锁:占用一个本地端口,进程退出自动释放
    import socket
    _lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _lock.bind(("127.0.0.1", 47823))
    except OSError:
        log("已有另一个实例在运行,退出。")
        return
    portal = cfg.get("portal") or DEFAULT_CONFIG["portal"]
    log("JNU ePortal 自动登录已启动(守护模式)")
    log(f"账号={mask_account(cfg.get('username'))}  探测间隔={cfg['check_interval']}s  "
        f"保活间隔={cfg['keepalive_interval']}s  密码模式={cfg.get('password_mode', 'auto')}")
    user_index = None
    ka_interval = int(cfg.get("keepalive_interval") or DEFAULT_CONFIG["keepalive_interval"])
    last_ka = 0.0
    was_online = True  # 只在状态变化时记日志,避免刷屏
    while True:
        try:
            online, qs, info = probe()
            if online:
                if not was_online:
                    log("★ 网络已恢复(在线)")
                    was_online = True
                if not user_index:
                    user_index = get_user_index(portal)
                    if user_index:
                        last_ka = time.time()
                if user_index and (time.time() - last_ka) >= ka_interval:
                    r = keepalive(user_index, portal)
                    if r.get("result") != "success":
                        log(f"保活失败: {r.get('message', '')} -> 下轮重新登录")
                        user_index = None
                    last_ka = time.time()
                time.sleep(int(cfg.get("check_interval") or DEFAULT_CONFIG["check_interval"]))
            else:
                if was_online:
                    log(f"✗ 掉线: {info}")
                    was_online = False
                r = login(qs, cfg)
                if r.get("result") == "success":
                    user_index = r.get("userIndex")
                    ki = r.get("keepaliveInterval")
                    if ki and int(ki) > 0:
                        ka_interval = int(ki)
                    last_ka = time.time()
                    was_online = True
                    log(f"★ 自动登录成功! userIndex={user_index}")
                else:
                    log(f"✗ 登录失败: {r.get('message', '')} | {str(r)[:150]}")
                    time.sleep(int(cfg.get("retry_interval") or DEFAULT_CONFIG["retry_interval"]))
        except Exception as e:
            log(f"异常(忽略继续): {e}")
            time.sleep(int(cfg.get("retry_interval") or DEFAULT_CONFIG["retry_interval"]))


def run_once(cfg):
    portal = cfg.get("portal") or DEFAULT_CONFIG["portal"]
    log("单次测试模式")
    online, qs, info = probe()
    log(f"探测结果: online={online}, info={info}")
    log(f"queryString: {qs}")
    if online:
        log("当前已在线,无需登录。如要测试自动登录,请先运行 --logout 下线。")
        return
    log("尝试登录...")
    r = login(qs, cfg)
    log(f"登录返回: {json.dumps(r, ensure_ascii=False)[:400]}")


def run_selftest(cfg):
    """端到端自检:拿 userIndex -> 下线 -> 等几秒 -> 自动重登。"""
    portal = cfg.get("portal") or DEFAULT_CONFIG["portal"]
    log("========== 自检开始 ==========")
    log("第1步:获取当前会话 userIndex(按 IP 识别)...")
    ui = get_user_index(portal)
    log(f"  userIndex = {ui}")
    if ui:
        log("第2步:用 userIndex 下线...")
        r = logout(ui, portal)
        log(f"  下线返回: {json.dumps(r, ensure_ascii=False)[:300]}")
        log("第3步:等待 4 秒让会话清理...")
        time.sleep(4)
    else:
        log("  拿不到 userIndex(可能已掉线),直接测登录。")
    log("第4步:探测网络 + 自动登录...")
    online, qs, info = probe()
    log(f"探测: online={online}, info={info}")
    log(f"queryString: {qs}")
    if online:
        log("仍显示在线?下线可能未生效。可改为手动断开WiFi重连后再跑 --once。")
        return
    r = login(qs, cfg)
    log(f"登录返回: {json.dumps(r, ensure_ascii=False)[:500]}")
    if r.get("result") == "success":
        log(f"★★★ 自检成功!自动登录可用。userIndex={r.get('userIndex')}")
        log("可以放心启用守护模式了。")
    else:
        log("✗ 自检失败。请把以上全部输出复制发给我排查。")
        log("若需手动恢复上网:浏览器打开任意 http 网页会跳到认证页,手动登录即可。")


def run_logout(cfg):
    portal = cfg.get("portal") or DEFAULT_CONFIG["portal"]
    log("主动下线(用 userIndex)...")
    ui = get_user_index(portal)
    log(f"userIndex={ui}")
    if ui:
        log(json.dumps(logout(ui, portal), ensure_ascii=False))
    else:
        log("拿不到 userIndex,可能已经掉线。")


def main():
    if "--configure" in sys.argv:
        configure()
        return
    try:
        cfg = load_config()
        if cfg is None:
            print("未找到 config.json,请先运行: python jnu_autologin.py --configure")
            sys.exit(1)
        validate_config(cfg)
    except RuntimeError as e:
        print(e)
        sys.exit(1)
    if "--once" in sys.argv:
        run_once(cfg)
    elif "--selftest" in sys.argv:
        run_selftest(cfg)
    elif "--logout" in sys.argv:
        run_logout(cfg)
    else:
        run_daemon(cfg)


if __name__ == "__main__":
    main()
