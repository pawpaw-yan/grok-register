"""提供共享的 HTTP 请求、代理处理和 Chromium 启动参数。"""
import os
import threading
import urllib.parse

from DrissionPage import ChromiumOptions
from curl_cffi import requests
from cpa_xai.proxyutil import (
    LocalAuthProxyBridge,
    prepare_chromium_proxy,
    proxy_for_chromium,
)
from proxy.proxy_pool import (
    ProxyTransportError,
    current_proxy_lease,
    managed_proxy_active,
    safe_proxy_error_text,
)

_config = {}
_extension_path = ""


def _legacy_extension_path():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "turnstilePatch")


def _turnstile_screen_patch_path():
    """内置 screenX/screenY 补丁扩展：修复 CDP Input.dispatchMouseEvent 的
    MouseEvent.screenX/screenY 恒等于 x/y 的 Chromium bug（crbug 40280325），
    Cloudflare Turnstile 靠它识别自动化点击并拒绝发 token。
    必须以 content-script 扩展形式在所有 frame（含跨域挑战 iframe）document_start
    注入，页面级 run_js 补不进 OOPIF。"""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "extensions", "turnstile_screen_patch"
    )


def _resolve_extension_path(explicit=None):
    if explicit is not None:
        candidate = str(explicit or "").strip()
        return candidate if candidate and os.path.isdir(candidate) else ""
    configured = str(_extension_path or "").strip()
    if configured and os.path.isdir(configured):
        return configured
    legacy = _legacy_extension_path()
    return legacy if os.path.isdir(legacy) else ""


def configure_runtime(config_ref, extension_path=""):
    global _config, _extension_path
    _config = config_ref
    _extension_path = str(extension_path or "")


def get_configured_proxy():
    lease = current_proxy_lease()
    if lease is not None:
        return str(lease.proxy_url or "").strip()
    mode = str(_config.get("proxy_mode", "auto") or "auto").strip().lower()
    if mode == "direct" or mode in ("single", "pool"):
        return ""
    return str(_config.get("proxy", "") or "").strip()


def get_proxies():
    proxy = get_configured_proxy()
    return {"http": proxy, "https": proxy} if proxy else {}


def _parse_proxy_url(proxy):
    raw = str(proxy or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "http://" + raw
    try:
        return urllib.parse.urlsplit(raw)
    except Exception:
        return None


def _safe_proxy_port(parsed):
    try:
        return parsed.port
    except Exception:
        return None


def _proxy_has_auth(proxy):
    parsed = _parse_proxy_url(proxy)
    return bool(parsed and parsed.hostname and (parsed.username is not None or parsed.password is not None))


def _strip_proxy_auth(proxy):
    raw = str(proxy or "").strip()
    parsed = _parse_proxy_url(raw)
    if not parsed or not parsed.hostname:
        return raw
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = "[%s]" % host
    port = _safe_proxy_port(parsed)
    netloc = "%s:%s" % (host, port) if port else host
    stripped = urllib.parse.urlunsplit((parsed.scheme or "http", netloc, parsed.path, parsed.query, parsed.fragment))
    return stripped.split("://", 1)[1] if "://" not in raw else stripped


def _proxy_endpoint_terms(proxy=None):
    parsed = _parse_proxy_url(proxy or get_configured_proxy())
    if not parsed or not parsed.hostname:
        return []
    terms = [parsed.hostname]
    port = _safe_proxy_port(parsed)
    if port:
        terms.extend(["%s:%s" % (parsed.hostname, port), "port %s" % port])
    return [item.lower() for item in terms if item]


def is_proxy_connection_error(exc):
    if not get_configured_proxy():
        return False
    err = str(exc or "").lower()
    if not err:
        return False
    if any(item in err for item in ("proxy", "tunnel", "socks")):
        return True
    markers = (
        "could not connect", "failed to connect", "connection refused",
        "connection reset", "connect error", "timed out", "timeout",
    )
    if any(item in err for item in markers):
        terms = _proxy_endpoint_terms()
        return not terms or any(term in err for term in terms)
    return False


def page_has_proxy_error(page_obj):
    try:
        url = str(getattr(page_obj, "url", "") or "")
        title = str(page_obj.run_js("return document.title || ''") or "")
        body = str(page_obj.run_js("return document.body ? document.body.innerText.slice(0, 2000) : ''") or "")
    except Exception:
        return False
    text = "%s\n%s\n%s" % (url, title, body)
    text = text.lower()
    failed = any(marker in text for marker in (
        "err_proxy", "proxy connection failed", "proxy server",
        "proxy authentication", "tunnel connection failed",
        "无法连接到代理服务器", "代理服务器",
    ))
    if failed and managed_proxy_active():
        raise ProxyTransportError("Chromium 检测到代理连接错误页面")
    return failed


def prepare_browser_proxy(use_proxy=True, log_callback=None):
    # Managed registration leases must never silently switch to another IP in
    # the middle of an account. Legacy auto mode keeps the historical direct
    # fallback behavior when callers explicitly pass use_proxy=False.
    if managed_proxy_active():
        use_proxy = True
    proxy = get_configured_proxy()
    if not use_proxy or not proxy:
        return "", None
    logger = None
    if log_callback:
        logger = lambda message: log_callback("[*] 已为 Chromium启动本地认证代理桥: %s" % message.split(": ", 1)[-1]) if "started authenticated proxy bridge" in message else log_callback(message)
    return prepare_chromium_proxy(proxy, log=logger)


def apply_browser_proxy_option(options, proxy):
    if not proxy:
        return
    if hasattr(options, "set_proxy"):
        try:
            options.set_proxy(proxy)
            return
        except Exception:
            pass
    if not hasattr(options, "set_argument"):
        raise AttributeError("当前 DrissionPage ChromiumOptions 不支持设置浏览器代理")
    try:
        options.set_argument("--proxy-server=%s" % proxy)
    except TypeError:
        options.set_argument("--proxy-server", proxy)


def hidden_window_enabled():
    """离屏隐藏窗口开关（config.hidden_window），默认关闭。"""
    try:
        return bool(_config.get("hidden_window", False))
    except Exception:
        return False


# 离屏窗口要用的参数：把窗口挪出屏幕，同时关掉 Chrome 的“窗口被遮挡/后台”降级，
# 让 Turnstile 认为窗口完全可见（过验率与可见窗口一致）。
_HIDDEN_WINDOW_ARGUMENTS = (
    "--window-position=-32000,-32000",
    "--disable-features=CalculateNativeWinOcclusion",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-background-timer-throttling",
)


def _apply_hidden_window_option(options):
    if not hidden_window_enabled():
        return
    for argument in _HIDDEN_WINDOW_ARGUMENTS:
        try:
            options.set_argument(argument)
        except TypeError:
            options.set_argument(argument.split("=", 1)[0], argument.split("=", 1)[1])
    # 离屏后无法手动处理“保存密码”气泡，直接关掉密码保存与自动登录提示
    try:
        options.set_pref("credentials_enable_service", False)
        options.set_pref("credentials_enable_autosignin", False)
    except Exception:
        pass


def _cloak_browser_path():
    """定位 CloakBrowser 的 chrome.exe：~/.cloakbrowser/chromium-*/chrome.exe，取版本号最新的。"""
    import glob
    pattern = os.path.join(os.path.expanduser("~"), ".cloakbrowser", "chromium-*", "chrome.exe")
    candidates = glob.glob(pattern)
    if not candidates:
        return ""
    # chromium-146.0.7680.177.5 → 按版本元组排序取最新
    def version_key(path):
        part = os.path.basename(os.path.dirname(path))
        digits = part.replace("chromium-", "")
        try:
            return tuple(int(x) for x in digits.split("."))
        except ValueError:
            return (0,)
    return max(candidates, key=version_key)


def resolve_browser_path():
    """browser_path 显式配置优先；否则按 browser_preset 解析（system=空，交给 DrissionPage 探测）。"""
    explicit = str(_config.get("browser_path", "") or "").strip()
    if explicit:
        return os.path.expanduser(explicit)
    preset = str(_config.get("browser_preset", "system") or "system").strip().lower()
    if preset == "cloak":
        return _cloak_browser_path()
    if preset == "camoufox":
        # Firefox 内核，无 CDP；注册流程的 Playwright 驱动分支未实现前不支持
        return ""
    return ""


def create_browser_options(browser_proxy="", extension_path=None):
    options = ChromiumOptions()
    # 项目级浏览器路径 / browser_preset（如 cloak）优先；留空沿用 DrissionPage 全局 ini / 自动探测
    browser_path = resolve_browser_path()
    if browser_path:
        options.set_browser_path(browser_path)
    # Linux 无显示环境（display_backend=headless）时用新版 headless 渲染
    try:
        from core.display_runtime import headless_requested
        if headless_requested():
            options.headless(True)
            options.set_argument("--headless=new")
    except Exception:
        pass
    options.auto_port()
    options.set_timeouts(base=1)
    apply_browser_proxy_option(options, browser_proxy)
    _apply_hidden_window_option(options)
    effective_extension = _resolve_extension_path(extension_path)
    if effective_extension:
        options.add_extension(effective_extension)
    # Turnstile screenX/screenY 补丁：默认开启，配置 turnstile_screen_patch=false 关闭
    try:
        screen_patch_enabled = bool(_config.get("turnstile_screen_patch", True))
    except Exception:
        screen_patch_enabled = True
    if screen_patch_enabled:
        patch_ext = _turnstile_screen_patch_path()
        if os.path.isdir(patch_ext):
            options.add_extension(patch_ext)
    return options


# hwnd -> (x, y, w, h)；离屏时记住原位置，回屏时还原。None = 该窗口本来就是离屏启动的，没有"原位置"。
_restore_rects = {}
_window_walk_lock = threading.Lock()


def set_tool_windows_visible(visible, fallback_x=60, fallback_y=60):
    """勾选联动：False=把本工具浏览器窗口移出屏幕（记住原位置），True=移回屏幕（优先还原原位置）。

    仅匹配带 --remote-debugging-port 的 Chrome_WidgetWin_1 窗口，
    不会碰到用户自己开的普通 Chrome。返回(移动数, 出错说明)。
    """
    if os.name != "nt":
        return 0, "仅支持 Windows"
    try:
        import ctypes
        from ctypes import wintypes
    except Exception as exc:  # pragma: no cover
        return 0, f"加载 win32 API 失败: {exc}"
    try:
        import psutil
    except Exception as exc:
        return 0, f"psutil 不可用: {exc}"

    user32 = ctypes.windll.user32

    class _RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    with _window_walk_lock:
        EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        chrome_hwnds = []

        def _enum_callback(hwnd, _lparam):
            # 注意：GetClassNameW 不支持 NULL 查长度（恒返回 0），必须用固定缓冲
            buf = ctypes.create_unicode_buffer(256)
            if user32.GetClassNameW(hwnd, buf, 256) <= 0:
                return True
            if buf.value == "Chrome_WidgetWin_1" and user32.IsWindowVisible(hwnd):
                chrome_hwnds.append(hwnd)
            return True

        user32.EnumWindows(EnumWindowsProc(_enum_callback), 0)

        touched = 0
        errors = []
        SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0004, 0x0010
        for hwnd in chrome_hwnds:
            try:
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                try:
                    cmdline = " ".join(psutil.Process(pid.value).cmdline())
                except Exception:
                    continue
                if "remote-debugging-port" not in cmdline:
                    continue  # 普通浏览器窗口，不动
                SW_RESTORE = 9
                if user32.IsIconic(hwnd):
                    user32.ShowWindow(hwnd, SW_RESTORE)
                if visible:
                    rect = _restore_rects.pop(hwnd, None)
                    if rect:
                        x, y, width, height = rect
                        flags = SWP_NOZORDER | SWP_NOACTIVATE
                    else:
                        # 离屏启动的窗口没有"原位置"，兜底放到屏幕左上并级联排开
                        x = int(fallback_x) + (touched % 8) * 32
                        y = int(fallback_y) + (touched % 8) * 32
                        width = height = 0
                        flags = SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
                else:
                    rc = _RECT()
                    if user32.GetWindowRect(hwnd, ctypes.byref(rc)) and rc.left > -10000 and rc.top > -10000:
                        _restore_rects[hwnd] = (rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top)
                    else:
                        _restore_rects[hwnd] = None
                    x = y = -32000
                    width = height = 0
                    flags = SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
                if not user32.SetWindowPos(hwnd, 0, x, y, width, height, flags):
                    errors.append(f"hwnd={hwnd} 移动失败")
                    continue
                touched += 1
            except Exception as exc:
                errors.append(str(exc))
    return touched, "; ".join(errors[:3])


def show_hidden_browser_windows(target_x=60, target_y=60):
    """把本工具启动的浏览器窗口移回屏幕内（「窗口回屏」按钮入口）。"""
    return set_tool_windows_visible(True, target_x, target_y)


def _build_request_kwargs(**kwargs):
    request_kwargs = dict(kwargs)
    proxies = request_kwargs.pop("proxies", None)
    if proxies is None:
        proxies = get_proxies()
    if proxies:
        request_kwargs["proxies"] = proxies
    request_kwargs.setdefault("timeout", 15)
    return request_kwargs


def http_get(url, **kwargs):
    request_kwargs = _build_request_kwargs(**kwargs)
    try:
        return requests.get(url, **request_kwargs)
    except Exception as exc:
        if is_proxy_connection_error(exc):
            if managed_proxy_active():
                raise ProxyTransportError(safe_proxy_error_text(exc)) from exc
            direct = dict(request_kwargs)
            direct.pop("proxies", None)
            return requests.get(url, **direct)
        raise


def http_post(url, **kwargs):
    replay_safe = bool(kwargs.pop("replay_safe", False))
    request_kwargs = _build_request_kwargs(**kwargs)
    try:
        return requests.post(url, **request_kwargs)
    except Exception as exc:
        if is_proxy_connection_error(exc):
            if managed_proxy_active() or not replay_safe:
                raise ProxyTransportError(safe_proxy_error_text(exc)) from exc
            direct = dict(request_kwargs)
            direct.pop("proxies", None)
            return requests.post(url, **direct)
        raise
