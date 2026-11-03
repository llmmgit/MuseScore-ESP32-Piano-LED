#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MuseScore ESP32 Piano LED Bridge."""

import colorsys
import json
import queue
import socket
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk

try:
    import mido
except ImportError:
    mido = None

CONFIG_PATH = Path(__file__).with_name("piano_led_config.json")
UDP_PORT = 4210
MIDI_LOW, MIDI_HIGH = 21, 108
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def clamp(value, low, high):
    return max(low, min(high, value))


def note_name(note):
    return f"{NOTE_NAMES[note % 12]}{note // 12 - 1}"


class LedClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.seq = 0
        self.note_to_led = {int(k): int(v) for k, v in cfg["note_to_led"].items()}
        self.set_target(cfg.get("esp32_ip", "192.168.1.123"))

    def set_target(self, ip):
        self.ip = str(ip).strip()
        self.addr = (self.ip, UDP_PORT)

    def send(self, text):
        self.sock.sendto(text.encode("ascii"), self.addr)

    def send_frame(self, colors):
        self.seq = (self.seq + 1) & 0xFFFFFFFF
        body = ";".join(
            f"{led}:{clamp(r,0,255)}:{clamp(g,0,255)}:{clamp(b,0,255)}"
            for led, (r, g, b) in sorted(colors.items())
        )
        self.send(f"F,{self.seq}," + body)

    def all_off(self):
        self.send_frame({})
        self.send("A")

    def set_brightness(self, value):
        self.send(f"B,{clamp(int(value), 0, 255)}")

    def single_led(self, led, color=(0, 160, 0)):
        self.send("A")
        self.send(f"L,{int(led)},{color[0]},{color[1]},{color[2]}")

    def note_color(self, velocity):
        velocity_cfg = self.cfg.setdefault("velocity", {})
        velocity = clamp(int(velocity), 1, 127)
        x = velocity / 127.0
        gamma = float(velocity_cfg.get("gamma", 1.8))
        minimum = float(velocity_cfg.get("min_brightness_ratio", 0.05))

        if velocity_cfg.get("mode", "brightness") == "color":
            # Blue -> cyan -> yellow -> orange-red.
            # The strongest end is shifted toward red, while retaining enough green
            # to remain visually brighter than pure red on typical WS2812B strips.
            if x < 0.42:
                local = x / 0.42
                hue_deg = 220.0 + (180.0 - 220.0) * local       # blue -> cyan
            elif x < 0.76:
                local = (x - 0.42) / 0.34
                hue_deg = 180.0 + (55.0 - 180.0) * local        # cyan -> yellow
            else:
                local = (x - 0.76) / 0.24
                hue_deg = 55.0 + (18.0 - 55.0) * local          # yellow -> orange-red

            value = 0.28 + 0.72 * (x ** max(0.25, gamma * 0.60))
            saturation = 0.95
            r, g, b = colorsys.hsv_to_rgb(hue_deg / 360.0, saturation, value)
            return int(r * 255), int(g * 255), int(b * 255)

        base = self.cfg.get("default_color", {"r": 0, "g": 255, "b": 0})
        ratio = minimum + (1.0 - minimum) * (x ** gamma)
        return tuple(int(base[channel] * ratio) for channel in ("r", "g", "b"))


class CalibrationDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.title("琴键校准 / Key Calibration")
        self.geometry("720x430")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.configure(bg="#f3f5f9")

        self.note = MIDI_LOW
        self.mapping = dict(parent.led.note_to_led)
        self.note_var = tk.StringVar()
        self.led_var = tk.IntVar()
        self.status_var = tk.StringVar(value="选择琴键并微调 LED / Select a key and adjust its LED")
        self.build_ui()
        self.refresh_fields()
        self.bind("<Left>", lambda _e: self.move(-1))
        self.bind("<Right>", lambda _e: self.move(1))
        self.bind("<Down>", lambda _e: self.adjust(-1))
        self.bind("<Up>", lambda _e: self.adjust(1))
        self.bind("<space>", lambda _e: self.light())
        self.bind("<KeyPress-s>", lambda _e: self.save())
        self.bind("<KeyPress-a>", lambda _e: self.all_off())
        self.protocol("WM_DELETE_WINDOW", self.close_dialog)

    @staticmethod
    def button(parent, text, command, primary=False):
        return tk.Button(
            parent, text=text, command=command, relief="flat", bd=0,
            padx=14, pady=9, cursor="hand2", font=("Segoe UI", 10),
            bg="#2563eb" if primary else "#eef2f7",
            fg="white" if primary else "#243247",
        )

    def build_ui(self):
        outer = tk.Frame(self, bg="#f3f5f9", padx=24, pady=22)
        outer.pack(fill="both", expand=True)
        tk.Label(outer, text="琴键校准", bg="#f3f5f9", fg="#172033", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        tk.Label(outer, text="Key Calibration", bg="#f3f5f9", fg="#718096").pack(anchor="w", pady=(0, 16))

        card = tk.Frame(outer, bg="white", highlightbackground="#dfe3ea", highlightthickness=1, padx=24, pady=22)
        card.pack(fill="x")
        top = tk.Frame(card, bg="white")
        top.pack(fill="x")
        left, right = tk.Frame(top, bg="white"), tk.Frame(top, bg="white")
        left.pack(side="left", expand=True, fill="x")
        right.pack(side="right")
        tk.Label(left, text="当前音符 / Current note", bg="white", fg="#718096").pack(anchor="w")
        tk.Label(left, textvariable=self.note_var, bg="white", fg="#172033", font=("Segoe UI", 22, "bold")).pack(anchor="w")
        tk.Label(right, text="LED 编号 / LED index", bg="white", fg="#718096").pack(anchor="e")
        tk.Spinbox(right, from_=0, to=int(self.parent.cfg.get("led_count", 176))-1,
                   textvariable=self.led_var, width=7, justify="center",
                   font=("Segoe UI", 18, "bold")).pack(anchor="e")

        nav = tk.Frame(card, bg="white")
        nav.pack(pady=(24, 8))
        for text, command in [
            ("← 上一键 / Previous", lambda: self.move(-1)),
            ("下一键 / Next →", lambda: self.move(1)),
            ("LED -1", lambda: self.adjust(-1)),
            ("LED +1", lambda: self.adjust(1)),
        ]:
            self.button(nav, text, command).pack(side="left", padx=5)

        actions = tk.Frame(card, bg="white")
        actions.pack(pady=8)
        for text, command, primary in [
            ("点亮 / Light", self.light, True),
            ("全部熄灭 / All off", self.all_off, False),
            ("保存校准 / Save", self.save, False),
            ("关闭 / Close", self.close_dialog, False),
        ]:
            self.button(actions, text, command, primary).pack(side="left", padx=5)
        tk.Label(outer, textvariable=self.status_var, bg="#f3f5f9", fg="#536174").pack(anchor="w", pady=(14, 0))

    def commit(self):
        self.mapping[self.note] = clamp(int(self.led_var.get()), 0, int(self.parent.cfg.get("led_count", 176))-1)

    def refresh_fields(self):
        self.note_var.set(f"{note_name(self.note)}  ·  MIDI {self.note}")
        self.led_var.set(self.mapping.get(self.note, 0))

    def move(self, delta):
        self.commit()
        self.note = clamp(self.note + delta, MIDI_LOW, MIDI_HIGH)
        self.refresh_fields()
        self.light()

    def adjust(self, delta):
        self.led_var.set(clamp(int(self.led_var.get()) + delta, 0, int(self.parent.cfg.get("led_count", 176))-1))
        self.light()

    def light(self):
        self.commit()
        try:
            self.parent.led.single_led(self.mapping[self.note])
            self.status_var.set(f"{note_name(self.note)} → LED {self.mapping[self.note]}")
        except OSError as exc:
            self.status_var.set(str(exc))

    def all_off(self):
        try:
            self.parent.led.all_off()
            self.status_var.set("已熄灭 / All off")
        except OSError as exc:
            self.status_var.set(str(exc))

    def save(self):
        self.commit()
        self.parent.led.note_to_led = dict(self.mapping)
        self.parent.cfg["note_to_led"] = {str(k): int(v) for k, v in sorted(self.mapping.items())}
        self.parent.save_config()
        self.status_var.set("校准已保存 / Calibration saved")

    def close_dialog(self):
        try:
            self.parent.led.all_off()
        except OSError:
            pass
        self.grab_release()
        self.destroy()


class BridgeApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MuseScore ESP32 Piano LED")
        self.geometry("820x650")
        self.minsize(780, 620)
        self.configure(bg="#eef1f5")

        self.cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        self.cfg["udp_port"] = UDP_PORT
        self.cfg.setdefault("velocity", {}).setdefault("mode", "brightness")
        self.led = LedClient(self.cfg)

        self.events = queue.Queue()
        self.lock = threading.RLock()
        self.frame_wakeup = threading.Event()
        self.running = False
        self.active_notes = {}
        self.visual_hold = {}
        self.note_on_times = {}
        self.input_handle = None
        self.esp_connected = False
        self.heartbeat_failures = 0
        self.closing = False

        self.midi_var = tk.StringVar(value=self.cfg.get("midi_input", ""))
        self.ip_var = tk.StringVar(value=self.cfg.get("esp32_ip", "192.168.1.123"))
        self.mode_var = tk.StringVar(value=self.cfg["velocity"]["mode"])
        self.midi_status_var = tk.StringVar(value="未启动 / Stopped")
        self.esp_status_var = tk.StringVar(value="未连接 / Offline")
        self.last_midi_var = tk.StringVar(value="等待 MIDI / Waiting for MIDI")

        self.build_ui()
        self.refresh_ports()
        self.after(100, self.process_events)
        self.after(2500, self.heartbeat_tick)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    @staticmethod
    def button(parent, text, command, primary=False):
        return tk.Button(
            parent, text=text, command=command, relief="flat", bd=0,
            padx=16, pady=10, cursor="hand2", font=("Segoe UI", 10),
            bg="#2563eb" if primary else "#eef2f7",
            fg="white" if primary else "#243247", disabledforeground="#aab3c0",
        )

    @staticmethod
    def card(parent):
        return tk.Frame(parent, bg="white", highlightbackground="#dfe3ea", highlightthickness=1, padx=22, pady=18)

    def build_ui(self):
        outer = tk.Frame(self, bg="#eef1f5", padx=30, pady=24)
        outer.pack(fill="both", expand=True)
        tk.Label(outer, text="MuseScore ESP32 Piano LED", bg="#eef1f5", fg="#172033", font=("Segoe UI", 22, "bold")).pack(anchor="w")
        tk.Label(outer, text="MIDI to piano-key LED bridge", bg="#eef1f5", fg="#758297").pack(anchor="w", pady=(2, 18))

        midi_card = self.card(outer)
        midi_card.pack(fill="x", pady=(0, 12))
        header = tk.Frame(midi_card, bg="white")
        header.pack(fill="x")
        tk.Label(header, text="MIDI 输入 / MIDI Input", bg="white", font=("Segoe UI", 12, "bold")).pack(side="left")
        tk.Label(header, textvariable=self.midi_status_var, bg="white", fg="#64748b").pack(side="right")
        row = tk.Frame(midi_card, bg="white")
        row.pack(fill="x", pady=(12, 0))
        self.midi_combo = ttk.Combobox(row, textvariable=self.midi_var, state="readonly")
        self.midi_combo.pack(side="left", fill="x", expand=True)
        self.midi_combo.bind("<<ComboboxSelected>>", self.auto_save)
        self.button(row, "刷新 / Refresh", self.refresh_ports).pack(side="left", padx=(10, 0))

        esp_card = self.card(outer)
        esp_card.pack(fill="x", pady=(0, 12))
        header = tk.Frame(esp_card, bg="white")
        header.pack(fill="x")
        tk.Label(header, text="ESP32 设备 / Device", bg="white", font=("Segoe UI", 12, "bold")).pack(side="left")
        self.esp_dot = tk.Canvas(header, width=14, height=14, bg="white", highlightthickness=0)
        self.esp_dot.pack(side="right", padx=(7, 0))
        self.esp_dot_id = self.esp_dot.create_oval(3, 3, 11, 11, fill="#94a3b8", outline="")
        tk.Label(header, textvariable=self.esp_status_var, bg="white", fg="#64748b").pack(side="right")
        row = tk.Frame(esp_card, bg="white")
        row.pack(fill="x", pady=(12, 0))
        tk.Entry(row, textvariable=self.ip_var, relief="solid", bd=1, font=("Segoe UI", 11)).pack(side="left", fill="x", expand=True, ipady=8)
        self.button(row, "发现 / Discover", self.discover_esp32).pack(side="left", padx=(10, 0))
        self.button(row, "连接 / Connect", self.connect_esp32, True).pack(side="left", padx=(8, 0))

        velocity_card = self.card(outer)
        velocity_card.pack(fill="x", pady=(0, 12))
        tk.Label(velocity_card, text="力度显示 / Velocity Display", bg="white", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        modes = tk.Frame(velocity_card, bg="white")
        modes.pack(anchor="w", pady=(10, 0))
        for text, value in [("绿色明暗 / Green brightness", "brightness"), ("力度颜色 / Velocity colors", "color")]:
            tk.Radiobutton(modes, text=text, variable=self.mode_var, value=value,
                           command=self.velocity_mode_changed, bg="white",
                           activebackground="white", selectcolor="white",
                           font=("Segoe UI", 10)).pack(side="left", padx=(0, 24))

        controls_card = self.card(outer)
        controls_card.pack(fill="x")
        tk.Label(controls_card, text="控制 / Controls", bg="white", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        row = tk.Frame(controls_card, bg="white")
        row.pack(fill="x", pady=(12, 0))
        self.start_button = self.button(row, "开始 / Start", self.start_bridge, True)
        self.stop_button = self.button(row, "停止并清灯 / Stop", self.stop_bridge)
        self.test_button = self.button(row, "彩色测试 / Test", self.play_rainbow)
        self.calibrate_button = self.button(row, "琴键校准 / Calibrate", self.open_calibration)
        for button, padding in [(self.start_button, (0, 7)), (self.stop_button, 7), (self.test_button, 7), (self.calibrate_button, 7)]:
            button.pack(side="left", padx=padding)

        tk.Label(outer, textvariable=self.last_midi_var, bg="#eef1f5", fg="#667488", font=("Consolas", 9)).pack(anchor="w", pady=(15, 0))
        tk.Label(outer, text="MuseScore 音源请选择 MS Basic  ·  Use MS Basic in MuseScore",
                 bg="#eef1f5", fg="#9a3412", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(6, 0))
        self.update_button_states()

    def save_config(self):
        self.cfg["midi_input"] = self.midi_var.get()
        self.cfg["esp32_ip"] = self.ip_var.get().strip()
        self.cfg["udp_port"] = UDP_PORT
        self.cfg["velocity"]["mode"] = self.mode_var.get()
        CONFIG_PATH.write_text(json.dumps(self.cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    def auto_save(self, _event=None):
        self.save_config()
        self.update_button_states()

    def velocity_mode_changed(self):
        self.cfg["velocity"]["mode"] = self.mode_var.get()
        self.led.cfg = self.cfg
        self.save_config()
        self.last_midi_var.set("力度模式已切换 / Velocity mode changed")

    def refresh_ports(self):
        if not mido:
            self.midi_status_var.set("缺少依赖 / Missing dependency")
            return
        try:
            names = mido.get_input_names()
            self.midi_combo["values"] = names
            if self.midi_var.get() not in names:
                self.midi_var.set(names[0] if names else "")
            self.midi_status_var.set("就绪 / Ready" if names else "未发现端口 / No port")
            self.save_config()
        except Exception as exc:
            self.midi_status_var.set(str(exc))
        self.update_button_states()

    def set_esp_status(self, connected, text=None):
        self.esp_connected = connected
        color = "#22a06b" if connected else "#ef4444" if text else "#94a3b8"
        self.esp_dot.itemconfigure(self.esp_dot_id, fill=color)
        self.esp_status_var.set(text or ("已连接 / Connected" if connected else "未连接 / Offline"))
        self.update_button_states()

    def update_button_states(self):
        connected = "normal" if self.esp_connected else "disabled"
        self.start_button.config(state="normal" if self.esp_connected and self.midi_var.get() and not self.running else "disabled")
        self.test_button.config(state=connected)
        self.calibrate_button.config(state=connected)
        self.stop_button.config(state="normal" if self.running else "disabled")

    @staticmethod
    def ping(ip, timeout=0.7):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        try:
            sock.sendto(b"P", (ip, UDP_PORT))
            data, address = sock.recvfrom(128)
            return data.decode(errors="ignore").strip() == "PONG", address[0]
        except OSError:
            return False, None
        finally:
            sock.close()

    def connect_esp32(self):
        ip = self.ip_var.get().strip()
        self.set_esp_status(False, "连接中 / Connecting")
        threading.Thread(target=lambda: self.events.put(("ping", *self.ping(ip), ip)), daemon=True).start()

    def discover_esp32(self):
        self.set_esp_status(False, "搜索中 / Searching")
        def worker():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(1.4)
            try:
                sock.bind(("", 0))
                sock.sendto(b"DISCOVER_MUSESCORE_LED", ("255.255.255.255", UDP_PORT))
                _data, address = sock.recvfrom(512)
                self.events.put(("found", address[0]))
            except OSError:
                self.events.put(("error", "未找到设备 / Device not found"))
            finally:
                sock.close()
        threading.Thread(target=worker, daemon=True).start()

    def heartbeat_tick(self):
        if self.closing:
            return
        if self.esp_connected:
            ip = self.ip_var.get().strip()
            threading.Thread(target=lambda: self.events.put(("heartbeat", self.ping(ip, 0.55)[0])), daemon=True).start()
        self.after(3000, self.heartbeat_tick)

    def start_bridge(self):
        if self.running or not self.esp_connected or not self.midi_var.get():
            return
        self.led.set_target(self.ip_var.get())
        self.led.set_brightness(self.cfg.get("global_brightness", 80))
        self.running = True
        self.midi_status_var.set("运行中 / Running")
        threading.Thread(target=self.midi_loop, daemon=True).start()
        threading.Thread(target=self.frame_loop, daemon=True).start()
        self.update_button_states()

    def midi_loop(self):
        try:
            self.input_handle = mido.open_input(self.midi_var.get())
            for message in self.input_handle:
                if not self.running:
                    break
                now = time.perf_counter()
                note = int(getattr(message, "note", -1))
                if message.type == "note_on" and message.velocity > 0:
                    with self.lock:
                        self.active_notes[note] = int(message.velocity)
                        self.note_on_times[note] = now
                        self.visual_hold.pop(note, None)
                    self.frame_wakeup.set()  # send the on-state without waiting for the next periodic tick
                    self.events.put(("midi", str(message)))
                elif message.type == "note_off" or (message.type == "note_on" and message.velocity == 0):
                    minimum_seconds = float(self.cfg.get("playback", {}).get("min_led_on_ms", 10)) / 1000.0
                    with self.lock:
                        velocity = self.active_notes.pop(note, 64)
                        started = self.note_on_times.pop(note, now)
                        remaining = minimum_seconds - (now - started)
                        if remaining > 0:
                            self.visual_hold[note] = (now + remaining, velocity)
                        else:
                            self.visual_hold.pop(note, None)
                    self.frame_wakeup.set()  # immediately publish the off/hold state
                    self.events.put(("midi", str(message)))
        except Exception as exc:
            self.events.put(("midi_error", str(exc)))
        finally:
            self.running = False
            self.frame_wakeup.set()
            self.events.put(("stopped",))

    def build_frame(self):
        now = time.perf_counter()
        colors = {}
        with self.lock:
            velocities = dict(self.active_notes)
            expired = []
            for note, (until, velocity) in self.visual_hold.items():
                if until > now:
                    velocities.setdefault(note, velocity)
                else:
                    expired.append(note)
            for note in expired:
                self.visual_hold.pop(note, None)
            for note, velocity in velocities.items():
                led = self.led.note_to_led.get(note)
                if led is not None:
                    colors[led] = self.led.note_color(velocity)
        return colors

    def frame_loop(self):
        interval = max(0.003, float(self.cfg.get("playback", {}).get("led_frame_interval_ms", 10)) / 1000.0)
        while self.running:
            self.frame_wakeup.wait(timeout=interval)
            self.frame_wakeup.clear()
            try:
                self.led.send_frame(self.build_frame())
            except OSError:
                self.events.put(("error", "发送失败 / Send failed"))
                break
        try:
            self.led.send_frame({})
        except OSError:
            pass

    def stop_bridge(self):
        self.running = False
        self.frame_wakeup.set()
        with self.lock:
            self.active_notes.clear()
            self.visual_hold.clear()
            self.note_on_times.clear()
        try:
            self.led.all_off()
        except OSError:
            pass
        self.midi_status_var.set("未启动 / Stopped")
        self.last_midi_var.set("等待 MIDI / Waiting for MIDI")
        self.update_button_states()

    def play_rainbow(self):
        if not self.esp_connected:
            return
        self.test_button.config(state="disabled")
        def worker():
            count = int(self.cfg.get("led_count", 176))
            center = int(self.cfg["note_to_led"].get("60", count // 2))
            trail = int(self.cfg.get("connect_animation", {}).get("trail_length", 5))
            step_ms = int(self.cfg.get("connect_animation", {}).get("step_ms", 22))
            for step in range(max(center, count - 1 - center) + trail):
                frame = {}
                for back in range(trail):
                    offset = step - back
                    if offset < 0:
                        continue
                    r, g, b = colorsys.hsv_to_rgb(((step * 7 + back * 17) % 360) / 360, 1, max(.18, 1 - back / max(1, trail)))
                    rgb = int(r * 120), int(g * 120), int(b * 120)
                    for led in (center - offset, center + offset):
                        if 0 <= led < count:
                            frame[led] = rgb
                self.led.send_frame(frame)
                time.sleep(step_ms / 1000)
            self.led.send_frame({})
            self.events.put(("rainbow_done",))
        threading.Thread(target=worker, daemon=True).start()

    def open_calibration(self):
        if self.esp_connected:
            CalibrationDialog(self)

    def process_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "found":
                    self.ip_var.set(event[1])
                    self.led.set_target(event[1])
                    self.save_config()
                    self.connect_esp32()
                elif kind == "ping":
                    if event[1]:
                        self.ip_var.set(event[2])
                        self.led.set_target(event[2])
                        self.heartbeat_failures = 0
                        self.set_esp_status(True)
                        self.save_config()
                    else:
                        self.set_esp_status(False, "未响应 / No response")
                elif kind == "heartbeat":
                    if event[1]:
                        self.heartbeat_failures = 0
                    else:
                        self.heartbeat_failures += 1
                        if self.heartbeat_failures >= 3:
                            self.set_esp_status(False, "连接中断 / Disconnected")
                            self.stop_bridge()
                elif kind == "error":
                    self.set_esp_status(False, event[1])
                elif kind == "midi":
                    self.last_midi_var.set(event[1])
                elif kind == "midi_error":
                    self.midi_status_var.set("端口错误 / Port error")
                    self.last_midi_var.set(event[1])
                elif kind == "stopped":
                    self.midi_status_var.set("未启动 / Stopped")
                    self.update_button_states()
                elif kind == "rainbow_done":
                    self.update_button_states()
        except queue.Empty:
            pass
        if not self.closing:
            self.after(100, self.process_events)

    def on_close(self):
        self.closing = True
        self.stop_bridge()
        self.save_config()
        self.destroy()


if __name__ == "__main__":
    BridgeApp().mainloop()
