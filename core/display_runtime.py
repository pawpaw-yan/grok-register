# Engine | Python 3.10+ | Linux 虚拟桌面后端 + VNC 远程观察
# Deps: filelock（项目既有依赖）；Xvfb / x11vnc 二进制可选（apt install xvfb x11vnc）

"""Linux 服务器上的虚拟桌面管理，注册浏览器启动前调用 prepare_display()。

四种后端（config.display_backend）：
  auto     有 DISPLAY 用现成的，没有自动拉 Xvfb（默认，推荐）
  native   只用环境变量 DISPLAY，没有就报错（A1 上就是 neko 容器的 Xorg :99）
  xvfb     代码自管 Xvfb：确保 :<display_num> 有 X 在监听，没有就拉起并等待
  headless 不需要 X，浏览器加 --headless=new（Turnstile 过验率可能下降，仅兜底）

Windows 什么都不做（窗口管理走 hidden_window 那套）；macOS 只透传现有 DISPLAY。

VNC 远程观察（config.display_vnc_enabled=true）：
  在当前桌面上挂 x11vnc（默认 viewonly 纯观看、只绑 127.0.0.1），
  远程机器先开 SSH 隧道再连 VNC 客户端：
      ssh -L 5999:localhost:5999 <host>
  然后客户端连 localhost:5999。关掉 viewonly 可直接上手点，适合 debug。
"""

import os
import shutil
import socket
import subprocess
import sys
import time

from filelock import FileLock

from core.app_config import config


class DisplayBackendError(RuntimeError):
    """虚拟桌面后端不可用（缺二进制 / native 模式没有 DISPLAY / 启动超时）。"""


def is_linux():
    return sys.platform.startswith("linux")


def resolve_backend():
    backend = str(config.get("display_backend", "auto") or "auto").strip().lower()
    if backend not in ("auto", "native", "xvfb", "headless"):
        backend = "auto"
    return backend


def headless_requested():
    """create_browser_options 据此决定是否加 --headless=new。"""
    return resolve_backend() == "headless"


def _env_display():
    return str(os.environ.get("DISPLAY") or "").strip()


def _display_socket_path(display_num):
    return "/tmp/.X11-unix/X%d" % int(display_num)


def _unix_socket_alive(path):
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.settimeout(0.5)
        probe.connect(path)
        return True
    except OSError:
        return False
    finally:
        try:
            probe.close()
        except OSError:
            pass


def _tcp_alive(host, port):
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.settimeout(0.5)
        probe.connect((host, int(port)))
        return True
    except OSError:
        return False
    finally:
        try:
            probe.close()
        except OSError:
            pass


def _log(message, log_callback=None):
    if log_callback:
        log_callback(message)


def ensure_xvfb(display_num, screen, log_callback=None, wait_sec=None):
    """确保指定显示号上有 X 在监听；没有就拉起 Xvfb 并等待。返回 ":<n>"。"""
    display_num = int(display_num)
    socket_path = _display_socket_path(display_num)
    if _unix_socket_alive(socket_path):
        return ":%d" % display_num
    binary = shutil.which("Xvfb")
    if not binary:
        raise DisplayBackendError(
            "Xvfb 未安装（apt install xvfb），或改用 display_backend=native"
        )
    if wait_sec is None:
        wait_sec = float(config.get("display_wait_sec", 5) or 5)
    spawn_lock = FileLock(socket_path + ".spawn.lock", timeout=10)
    with spawn_lock:
        if _unix_socket_alive(socket_path):
            return ":%d" % display_num
        command = [binary, ":%d" % display_num, "-screen", "0", screen, "-nolisten", "tcp"]
        subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.time() + max(wait_sec, 1)
        while time.time() < deadline:
            if _unix_socket_alive(socket_path):
                _log("[*] Xvfb :%d 已启动（%s）" % (display_num, screen), log_callback)
                return ":%d" % display_num
            time.sleep(0.2)
    raise DisplayBackendError(
        "Xvfb :%d 启动后 %.0fs 内仍未监听，检查 %s 与磁盘空间"
        % (display_num, max(wait_sec, 1), socket_path)
    )


def _ensure_vnc(display, log_callback=None):
    """display_vnc_enabled=true 时在该桌面上挂 x11vnc（幂等，已在监听则复用）。"""
    if not bool(config.get("display_vnc_enabled", False)):
        return
    port = int(config.get("display_vnc_port", 5999) or 5999)
    if _tcp_alive("127.0.0.1", port):
        _log("[*] VNC 观察已在 127.0.0.1:%d 监听，复用" % port, log_callback)
        return
    binary = shutil.which("x11vnc")
    if not binary:
        _log("[!] x11vnc 未安装，无法开启远程观察（apt install x11vnc）", log_callback)
        return
    if bool(config.get("hidden_window", False)):
        _log("[!] hidden_window=true 时浏览器窗口在屏幕外，VNC 里看不到；远程调试建议关闭 hidden_window", log_callback)
    listen = str(config.get("display_vnc_listen", "127.0.0.1") or "127.0.0.1").strip()
    viewonly = bool(config.get("display_vnc_viewonly", True))
    password = str(config.get("display_vnc_password", "") or "")
    if listen not in ("127.0.0.1", "localhost", "::1") and not password:
        _log(
            "[!] display_vnc_listen=%s 非回环地址但未设置 display_vnc_password，"
            "拒绝无密码暴露公网；请设密码或改回 127.0.0.1 走 SSH 隧道" % listen,
            log_callback,
        )
        return
    command = [
        binary,
        "-display", display,
        "-rfbport", str(port),
        "-listen", listen,
        "-forever", "-shared", "-bg", "-quiet",
    ]
    if viewonly:
        command.append("-viewonly")
    if password:
        command += ["-passwd", password]
    else:
        command.append("-nopw")
    subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.time() + 3
    while time.time() < deadline:
        if _tcp_alive("127.0.0.1", port):
            break
        time.sleep(0.2)
    else:
        _log("[!] x11vnc 启动后 3s 内未监听 127.0.0.1:%d，查看系统日志排查" % port, log_callback)
        return
    mode = "viewonly 只读" if viewonly else "可操作"
    if listen in ("127.0.0.1", "localhost", "::1"):
        _log(
            "[*] VNC 远程观察已开启（%s，绑定 127.0.0.1）: 在观看机上执行 ssh -L %d:localhost:%d <host>，"
            "VNC 客户端连 localhost:%d" % (mode, port, port, port),
            log_callback,
        )
    else:
        _log(
            "[*] VNC 远程观察已开启（%s，监听 %s:%d）: 观看机 VNC 客户端直连 <host>:%d"
            % (mode, listen, port, port),
            log_callback,
        )


def prepare_display(log_callback=None):
    """浏览器启动前调用。返回实际使用的 DISPLAY 字符串；headless 返回 ""。

    非 Linux 平台原样透传环境（Windows 返回环境 DISPLAY 或空串，供日志参考）。
    """
    if not is_linux():
        return _env_display()
    backend = resolve_backend()
    if backend == "headless":
        _log("[*] 显示后端 headless：浏览器以 --headless=new 启动（过验率可能下降）", log_callback)
        return ""
    env_display = _env_display()
    if backend == "native":
        if not env_display:
            raise DisplayBackendError(
                "display_backend=native 但环境没有 DISPLAY；"
                "设 display_backend=auto/xvfb，或先启动 X 服务"
            )
        _log("[*] 显示后端 native：使用 DISPLAY=%s" % env_display, log_callback)
        _ensure_vnc(env_display, log_callback)
        return env_display
    # auto / xvfb
    if backend == "auto" and env_display:
        _log("[*] 显示后端 auto：检测到 DISPLAY=%s，直接使用" % env_display, log_callback)
        _ensure_vnc(env_display, log_callback)
        return env_display
    display_num = int(config.get("display_num", 99) or 99)
    screen = str(config.get("display_screen", "1280x900x24") or "1280x900x24")
    display = ensure_xvfb(display_num, screen, log_callback)
    os.environ["DISPLAY"] = display
    _ensure_vnc(display, log_callback)
    return display
