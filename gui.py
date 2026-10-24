import argparse
import ctypes
import logging
import os
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import pystray
from PIL import ImageTk
from serial.tools import list_ports

from elrs_sim.branding import make_image
from elrs_sim.config import Calibration, Config
from elrs_sim.engine import Engine
from elrs_sim.link import find_ch340_port

APP_NAME = "ElrsSim"
BG = "#0f1420"
BG2 = "#1a2233"
FG = "#dbe4f0"
MUTED = "#7f8ea8"
ACCENT = "#00c8ff"
OK_COLOR = "#38d996"
ERROR_COLOR = "#ff6b6b"


def app_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


LOG_PATH = app_data_dir() / "elrs_sim.log"


def default_config_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().with_name("config.json")
    return Path(__file__).resolve().parent / "config.json"


def setup_logging() -> None:
    try:
        app_data_dir().mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=str(LOG_PATH),
            level=logging.INFO,
            format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s",
        )
    except Exception:
        logging.basicConfig(level=logging.INFO)


def enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        result = ctypes.windll.shcore.SetProcessDpiAwareness(1)
        logging.info("SetProcessDpiAwareness(1) -> %s", result)
    except Exception as exc:
        logging.warning("SetProcessDpiAwareness failed: %s", exc)
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def system_scale() -> float:
    if sys.platform != "win32":
        return 1.0
    try:
        awareness = ctypes.c_int()
        ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(awareness))
        if awareness.value == 0:
            return 1.0
    except Exception:
        pass
    try:
        factor = ctypes.windll.shcore.GetScaleFactorForDevice(0)
        if factor:
            return max(1.0, factor / 100.0)
    except Exception:
        pass
    try:
        return max(1.0, ctypes.windll.user32.GetDpiForSystem() / 96.0)
    except Exception:
        return 1.0


_mutex_handle = None


def acquire_single_instance() -> bool:
    global _mutex_handle
    if sys.platform != "win32":
        return True
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    _mutex_handle = kernel32.CreateMutexW(None, False, "ElrsSimTrayMutex")
    return kernel32.GetLastError() != 183


class CalibrationSession:
    def __init__(self, seconds: float = 10.0) -> None:
        self.seconds = seconds
        self.started = time.monotonic()
        self.minimums = [None] * 16
        self.maximums = [None] * 16

    def update(self, channels) -> None:
        if not channels:
            return
        for index, value in enumerate(channels[:16]):
            if self.minimums[index] is None or value < self.minimums[index]:
                self.minimums[index] = value
            if self.maximums[index] is None or value > self.maximums[index]:
                self.maximums[index] = value

    def remaining(self) -> float:
        return max(0.0, self.seconds - (time.monotonic() - self.started))

    def done(self) -> bool:
        return self.remaining() <= 0.0

    def apply(self, config: Config) -> int:
        changed = 0
        for index in range(16):
            low = self.minimums[index]
            high = self.maximums[index]
            if low is None or high is None or high - low < 300:
                continue
            config.calibration[index] = Calibration(
                min=low,
                center=(low + high) // 2,
                max=high,
            )
            changed += 1
        return changed


class App:
    def __init__(
        self,
        root: tk.Tk,
        config_path: Path,
        config: Config,
        engine: Engine,
        auto_start: bool = True,
    ) -> None:
        self.root = root
        self.config_path = Path(config_path)
        self.config = config
        self.engine = engine
        self.calibration = None
        self.icon = None
        self._commands = queue.Queue()
        self._closing = False
        self._hidden_notified = False
        self._last_link_error = None
        self._last_pad_error = None

        self._build_ui()
        self._build_tray()
        self.root.protocol("WM_DELETE_WINDOW", self._hide_window)
        self.root.report_callback_exception = self._report_exception
        self.root.after(50, self._tick)
        self.root.after(50, self._drain_commands)

        if auto_start:
            self.root.after(300, self._start)

    def _px(self, value: float) -> int:
        return int(round(value * self.scale))

    def _build_ui(self) -> None:
        root = self.root
        self.scale = system_scale()
        px = self._px
        logging.info(
            "ui scale=%.3f fpixels=%.1f tk_scaling=%.3f screen=%dx%d",
            self.scale,
            root.winfo_fpixels("1i"),
            float(root.tk.call("tk", "scaling")),
            root.winfo_screenwidth(),
            root.winfo_screenheight(),
        )
        root.title("ELRS Sim - 虚拟手柄")
        self._window_icons = [
            ImageTk.PhotoImage(make_image(size), master=root)
            for size in (64, 48, 32, 24, 16)
        ]
        root.iconphoto(True, *self._window_icons)
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        width = min(px(680), int(screen_width * 0.9))
        height = min(px(560), int(screen_height * 0.85))
        root.geometry(f"{width}x{height}")
        root.minsize(min(px(620), width), min(px(520), height))
        root.configure(bg=BG)
        root.option_add("*Font", "{Microsoft YaHei UI} 9")

        style = ttk.Style(root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=BG, foreground=FG, fieldbackground=BG2)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        style.configure("TLabelframe", background=BG, foreground=FG, bordercolor=BG2)
        style.configure("TLabelframe.Label", background=BG, foreground=ACCENT)
        style.configure("TButton", background=BG2, foreground=FG, padding=px(6), borderwidth=0)
        style.map("TButton", background=[("active", "#26324a"), ("disabled", "#151b28")])
        style.configure("TCombobox", fieldbackground=BG2, background=BG2, foreground=FG)
        style.configure(
            "TProgressbar",
            troughcolor=BG2,
            background=ACCENT,
            bordercolor=BG2,
            lightcolor=ACCENT,
            darkcolor=ACCENT,
        )

        root.columnconfigure(0, weight=1)

        serial_frame = ttk.Frame(root, padding=(px(12), px(10), px(12), px(4)))
        serial_frame.grid(row=0, column=0, sticky="ew")
        ttk.Label(serial_frame, text="端口").pack(side="left")
        self.port_var = tk.StringVar(value=self.config.serial.port)
        self.port_combo = ttk.Combobox(serial_frame, textvariable=self.port_var, width=12)
        self.port_combo.pack(side="left", padx=(px(6), px(10)))
        ttk.Label(serial_frame, text="波特率").pack(side="left")
        self.baud_var = tk.StringVar(value=str(self.config.serial.baud))
        ttk.Entry(serial_frame, textvariable=self.baud_var, width=9).pack(
            side="left", padx=(px(6), px(10))
        )
        ttk.Button(serial_frame, text="刷新端口", command=self._refresh_ports).pack(side="left")
        self.start_button = ttk.Button(serial_frame, text="启动", command=self._toggle_run)
        self.start_button.pack(side="right")
        self._refresh_ports()

        status_frame = ttk.Frame(root, padding=(px(12), px(4), px(12), px(4)))
        status_frame.grid(row=1, column=0, sticky="ew")
        self.state_label = tk.Label(
            status_frame,
            text="已停止",
            bg=BG,
            fg=MUTED,
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self.state_label.pack(side="left")
        self.failsafe_label = tk.Label(
            status_frame,
            text="",
            bg=BG,
            fg=ERROR_COLOR,
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.failsafe_label.pack(side="left", padx=(px(10), 0))
        self.link_label = ttk.Label(status_frame, text="--", style="Muted.TLabel")
        self.link_label.pack(side="right")

        sticks_frame = ttk.Frame(root, padding=(px(12), px(4), px(12), px(4)))
        sticks_frame.grid(row=2, column=0, sticky="ew")
        left_frame, self.left_canvas, self.left_dot = self._make_stick(
            sticks_frame, "左摇杆  Yaw / Throttle"
        )
        left_frame.pack(side="left", padx=(0, px(12)))
        right_frame, self.right_canvas, self.right_dot = self._make_stick(
            sticks_frame, "右摇杆  Roll / Pitch"
        )
        right_frame.pack(side="left")

        self.axis_label = ttk.Label(sticks_frame, text="R +0.00   P +0.00   T -1.00   Y +0.00")
        self.axis_label.pack(side="left", padx=(px(18), 0), anchor="n")

        channels_frame = ttk.LabelFrame(root, text="通道", padding=(px(10), px(6)))
        channels_frame.grid(row=3, column=0, sticky="nsew", padx=px(12), pady=px(4))
        root.rowconfigure(3, weight=1)
        for column in range(2):
            channels_frame.columnconfigure(column, weight=1)

        self.channel_bars = []
        self.channel_values = []
        for index in range(16):
            row = index % 8
            column = index // 8
            cell = ttk.Frame(channels_frame)
            cell.grid(row=row, column=column, sticky="ew", padx=px(4), pady=px(1))
            ttk.Label(cell, text=f"CH{index + 1:02d}", width=5).pack(side="left")
            bar = ttk.Progressbar(cell, maximum=100, length=px(120))
            bar.pack(side="left", padx=px(4))
            value_label = ttk.Label(cell, text="   0", width=5)
            value_label.pack(side="left")
            self.channel_bars.append(bar)
            self.channel_values.append(value_label)

        actions = ttk.Frame(root, padding=(px(12), px(4), px(12), px(4)))
        actions.grid(row=4, column=0, sticky="ew")
        self.calibrate_button = ttk.Button(
            actions, text="校准 10 秒", command=self._start_calibration
        )
        self.calibrate_button.pack(side="left")
        self.bind_button = ttk.Button(
            actions, text="对频接收机", command=self._bind_receiver
        )
        self.bind_button.pack(side="left", padx=(px(8), 0))
        ttk.Button(actions, text="重新加载配置", command=self._reload_config).pack(
            side="left", padx=(px(8), 0)
        )
        ttk.Button(actions, text="打开配置", command=self._open_config).pack(
            side="left", padx=(px(8), 0)
        )
        ttk.Button(actions, text="隐藏到托盘", command=self._hide_window).pack(side="right")

        self.message_var = tk.StringVar(value="就绪")
        self.message_label = tk.Label(
            root,
            textvariable=self.message_var,
            bg=BG,
            fg=MUTED,
            anchor="w",
            padx=px(12),
            pady=px(6),
        )
        self.message_label.grid(row=5, column=0, sticky="ew")

    def _make_stick(self, parent, title):
        px = self._px
        size = px(128)
        frame = ttk.LabelFrame(parent, text=title, padding=px(4))
        canvas = tk.Canvas(
            frame,
            width=size,
            height=size,
            bg=BG2,
            highlightthickness=0,
        )
        canvas.pack()
        center = size // 2
        margin = px(10)
        amplitude = px(44)
        canvas.create_line(center, margin, center, size - margin, fill="#2b3a57")
        canvas.create_line(margin, center, size - margin, center, fill="#2b3a57")
        canvas.create_oval(
            center - amplitude,
            center - amplitude,
            center + amplitude,
            center + amplitude,
            outline="#2b3a57",
        )
        radius = px(5)
        dot = canvas.create_oval(
            center - radius,
            center - radius,
            center + radius,
            center + radius,
            fill=ACCENT,
            outline="",
        )
        self.stick_center = center
        self.stick_amplitude = amplitude
        self.stick_dot_radius = radius
        return frame, canvas, dot

    def _build_tray(self) -> None:
        menu = pystray.Menu(
            pystray.MenuItem("显示窗口", self._tray_show, default=True),
            pystray.MenuItem("启动", self._tray_start),
            pystray.MenuItem("停止", self._tray_stop),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("校准 10 秒", self._tray_calibrate),
            pystray.MenuItem("对频接收机", self._tray_bind),
            pystray.MenuItem("重新加载配置", self._tray_reload),
            pystray.MenuItem("打开配置文件", self._tray_open_config),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._tray_quit),
        )
        self.icon = pystray.Icon(APP_NAME, make_image(64), "ELRS Sim 虚拟手柄", menu)
        threading.Thread(target=self._run_tray, name="tray", daemon=True).start()

    def _run_tray(self) -> None:
        try:
            self.icon.run()
        except Exception:
            logging.exception("tray icon failed")

    def _post(self, func, *args) -> None:
        self._commands.put((func, args))

    def _drain_commands(self) -> None:
        if self._closing:
            return
        while True:
            try:
                func, args = self._commands.get_nowait()
            except queue.Empty:
                break
            try:
                func(*args)
            except Exception:
                logging.exception("command failed")
        self.root.after(50, self._drain_commands)

    def _report_exception(self, exc_type, exc_value, traceback) -> None:
        logging.error("unhandled ui error", exc_info=(exc_type, exc_value, traceback))
        try:
            messagebox.showerror("错误", str(exc_value))
        except Exception:
            pass

    def _refresh_ports(self) -> None:
        values = ["auto"] + [port.device for port in list_ports.comports()]
        self.port_combo["values"] = values
        if self.port_var.get() not in values:
            self.port_var.set(find_ch340_port() or "auto")

    def _apply_serial_selection(self) -> None:
        port = self.port_var.get().strip() or "auto"
        try:
            baud = int(self.baud_var.get().strip())
        except ValueError:
            baud = self.config.serial.baud
            self.baud_var.set(str(baud))
            self._set_message("波特率无效，沿用原值", error=True)
        changed = port != self.config.serial.port or baud != self.config.serial.baud
        self.config.serial.port = port
        self.config.serial.baud = baud
        if changed:
            try:
                self.config.save(self.config_path)
            except Exception as exc:
                logging.warning("cannot save config: %s", exc)

    def _start(self) -> None:
        if self.engine.is_running():
            return
        self._apply_serial_selection()
        try:
            self.engine.start(pad_enabled=True)
        except RuntimeError as exc:
            logging.exception("cannot start engine")
            self._set_message(str(exc), error=True)
            messagebox.showerror("启动失败", str(exc))
            return
        mapped = [name for name, axis in self.config.axes.items() if axis.map_0_1]
        logging.info(
            "engine started port=%s baud=%s map_0_1=%s",
            self.config.serial.port,
            self.config.serial.baud,
            mapped,
        )
        mode = f"（0-1: {', '.join(mapped)}）" if mapped else ""
        self._set_message("运行中" + mode, ok=True)

    def _stop(self) -> None:
        self.engine.stop()
        self.calibration = None
        self.calibrate_button.config(state="normal")
        logging.info("engine stopped")
        self._set_message("已停止")

    def _toggle_run(self) -> None:
        if self.engine.is_running():
            self._stop()
        else:
            self._start()

    def _start_calibration(self) -> None:
        if not self.engine.is_running():
            messagebox.showinfo("校准", "请先启动虚拟手柄。")
            return
        self.calibration = CalibrationSession(10.0)
        self.calibrate_button.config(state="disabled")
        self._set_message("校准中：把 roll / pitch / yaw 打到两端，油门保持最低")

    def _bind_receiver(self) -> None:
        status = self.engine.status()
        if not status.running or not status.snapshot.connected:
            messagebox.showinfo("对频", "请先启动虚拟手柄并确认串口已连接。")
            return
        if not messagebox.askyesno(
            "对频",
            "接收机将进入对频模式，当前链路会断开。\n"
            "请确认 CH340 的 TX 已接到接收机的 CRSF RX。\n\n继续？",
            icon=messagebox.WARNING,
            default=messagebox.NO,
        ):
            return
        if self.engine.bind():
            logging.info("bind command sent by user")
            self._set_message("已发送对频命令，接收机应双闪；请在发射机上执行 Bind", ok=True)
        else:
            self._set_message("对频命令发送失败，请检查串口连接", error=True)

    def _reload_config(self) -> None:
        try:
            config = Config.load(self.config_path)
        except Exception as exc:
            logging.exception("config reload failed")
            messagebox.showerror("配置错误", str(exc))
            return
        self.config = config
        try:
            self.engine.reload(config)
        except RuntimeError as exc:
            logging.exception("engine reload failed")
            self._set_message(str(exc), error=True)
            return
        self.port_var.set(config.serial.port)
        self.baud_var.set(str(config.serial.baud))
        mapped = [name for name, axis in config.axes.items() if axis.map_0_1]
        logging.info("config reloaded map_0_1=%s", mapped)
        mode = ", ".join(mapped) if mapped else "无"
        self._set_message(f"配置已重新加载（0-1 轴：{mode}）", ok=True)

    def _open_config(self) -> None:
        try:
            self.config.save(self.config_path)
            os.startfile(str(self.config_path))
        except Exception as exc:
            logging.exception("open config failed")
            messagebox.showerror("打开配置失败", str(exc))

    def _hide_window(self) -> None:
        if self._closing:
            return
        self.root.withdraw()
        if not self._hidden_notified and self.icon is not None:
            self._hidden_notified = True
            try:
                self.icon.notify("程序仍在后台运行，可从托盘图标恢复窗口。", "ELRS Sim")
            except Exception:
                pass

    def _show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.focus_force()
        except Exception:
            pass

    def _quit(self) -> None:
        if self._closing:
            return
        self._closing = True
        logging.info("application exit")
        try:
            self.engine.stop()
        except Exception:
            logging.exception("engine stop failed")
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                logging.exception("tray stop failed")
        try:
            self.root.destroy()
        except Exception:
            pass

    def _set_message(self, text: str, error: bool = False, ok: bool = False) -> None:
        self.message_var.set(text)
        color = ERROR_COLOR if error else OK_COLOR if ok else MUTED
        self.message_label.configure(fg=color)
        if error:
            logging.warning("%s", text)

    def _set_stick(self, canvas, dot, x_value: float, y_value: float) -> None:
        center = self.stick_center
        amplitude = self.stick_amplitude
        radius = self.stick_dot_radius
        x = center + max(-1.0, min(1.0, x_value)) * amplitude
        y = center - max(-1.0, min(1.0, y_value)) * amplitude
        canvas.coords(dot, x - radius, y - radius, x + radius, y + radius)

    def _tick(self) -> None:
        if self._closing:
            return

        status = self.engine.status()
        snapshot = status.snapshot
        state = status.state
        running = status.running

        self.state_label.configure(
            text="运行中" if running else "已停止",
            fg=OK_COLOR if running else MUTED,
        )
        self.start_button.configure(text="停止" if running else "启动")
        self.failsafe_label.configure(text="FAILSAFE" if running and status.failsafe else "")

        age_ms = snapshot.age_ms()
        details = f"{(snapshot.port or '--'):>6}  {snapshot.rate_hz:4.0f} Hz  frame {snapshot.frames}  crc {snapshot.crc_errors}"
        if age_ms is not None:
            details += f"  age {age_ms:4.0f} ms"
        self.link_label.configure(text=details)

        self.axis_label.configure(
            text=(
                f"R {state.right_x:+.2f}   P {state.right_y:+.2f}   "
                f"T {state.left_y:+.2f}   Y {state.left_x:+.2f}"
            )
        )
        self._set_stick(self.left_canvas, self.left_dot, state.left_x, state.left_y)
        self._set_stick(self.right_canvas, self.right_dot, state.right_x, state.right_y)

        if snapshot.channels:
            for index, value in enumerate(snapshot.channels[:16]):
                clamped = max(0, min(2047, value))
                self.channel_bars[index]["value"] = clamped / 2047.0 * 100.0
                self.channel_values[index].configure(text=f"{value:4d}")

        if self.calibration is not None:
            self.calibration.update(snapshot.channels)
            remaining = self.calibration.remaining()
            if remaining > 0:
                self._set_message(f"校准中... {remaining:4.1f}s")
            else:
                changed = self.calibration.apply(self.config)
                try:
                    self.config.save(self.config_path)
                except Exception as exc:
                    logging.warning("cannot save calibration: %s", exc)
                self.calibration = None
                self.calibrate_button.config(state="normal")
                logging.info("calibration saved: %d channels", changed)
                self._set_message(f"校准完成，已更新 {changed} 个通道", ok=True)

        if snapshot.error and snapshot.error != self._last_link_error:
            self._last_link_error = snapshot.error
            self._set_message(snapshot.error, error=True)
        elif not snapshot.error:
            self._last_link_error = None

        if status.error and status.error != self._last_pad_error:
            self._last_pad_error = status.error
            self._set_message(status.error, error=True)
        elif not status.error:
            self._last_pad_error = None

        self.root.after(50, self._tick)

    def _tray_show(self, icon=None, item=None) -> None:
        self._post(self._show_window)

    def _tray_start(self, icon=None, item=None) -> None:
        self._post(self._start)

    def _tray_stop(self, icon=None, item=None) -> None:
        self._post(self._stop)

    def _tray_calibrate(self, icon=None, item=None) -> None:
        self._post(self._start_calibration)

    def _tray_bind(self, icon=None, item=None) -> None:
        self._post(self._bind_receiver)

    def _tray_reload(self, icon=None, item=None) -> None:
        self._post(self._reload_config)

    def _tray_open_config(self, icon=None, item=None) -> None:
        self._post(self._open_config)

    def _tray_quit(self, icon=None, item=None) -> None:
        self._post(self._quit)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ELRS receiver to virtual gamepad tray app.")
    parser.add_argument("--config", default=str(default_config_path()), help="config file path")
    parser.add_argument("--port", help="serial port such as COM6")
    parser.add_argument("--baud", type=int, help="CRSF baud rate")
    parser.add_argument("--no-autostart", action="store_true", help="do not start on launch")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    setup_logging()
    logging.info("gui start argv=%s", sys.argv[1:])
    enable_dpi_awareness()

    if not acquire_single_instance():
        logging.info("another instance is already running")
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("ELRS Sim", "程序已在运行，请查看系统托盘。")
        root.destroy()
        return 0

    config_path = Path(args.config)
    try:
        config = Config.load(config_path)
    except Exception as first_error:
        fallback_path = app_data_dir() / "config.json"
        try:
            config = Config.load(fallback_path)
            config_path = fallback_path
            logging.warning("config fallback to %s: %s", fallback_path, first_error)
        except Exception:
            logging.exception("config load failed")
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("配置错误", f"{config_path}\n{first_error}")
            root.destroy()
            return 2

    if args.port:
        config.serial.port = args.port
    if args.baud:
        config.serial.baud = args.baud

    root = tk.Tk()
    engine = Engine(config)
    App(root, config_path, config, engine, auto_start=not args.no_autostart)
    try:
        root.mainloop()
    finally:
        try:
            engine.stop()
        except Exception:
            logging.exception("shutdown engine stop failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
