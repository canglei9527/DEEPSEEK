"""Windows desktop entry point for existing lab commands.

Device operations remain owned by the CLI.  The GUI never writes device files
or changes the recovery gate itself.
"""

from __future__ import annotations

import json
import os
import queue
from datetime import datetime
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import END, Menu, StringVar, Tk, filedialog, messagebox, simpledialog

from cpu_efficiency_lab.matrix_controller import INDEPENDENT_RECOVERY_VERIFIED


def parse_adb_devices(output: str):
    """Return selectable serials and all device states from adb's table."""
    ready, states = [], []
    for line in output.splitlines():
        fields = line.strip().split()
        if len(fields) < 2 or fields[0] == "List" or fields[0].startswith("*"):
            continue
        serial, state = fields[:2]
        states.append((serial, state))
        if state == "device":
            ready.append(serial)
    return ready, states


def desktop_command(dispatch, args):
    """Use the same executable for background work in source and frozen builds."""
    if getattr(sys, "frozen", False):
        return [sys.executable, dispatch, *args]
    return [sys.executable, "-m", "cpu_efficiency_lab.desktop", dispatch, *args]


def cli_command(args):
    return desktop_command("--cli", args)


def fresh_child(parent: str, name: str) -> Path:
    """Validate a new output name while leaving exclusive creation to the CLI."""
    if not name or name in (".", "..") or Path(name).name != name or "/" in name or "\\" in name:
        raise ValueError("请输入一个不含路径分隔符的输出名称。")
    result = Path(parent) / name
    if result.exists():
        raise ValueError("输出路径已经存在，请使用新的名称。")
    return result


def default_workspace():
    """Resolve source/build defaults without depending on Explorer's working directory."""
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parents[2]
    executable = Path(sys.executable).resolve()
    if executable.parent.parent.name == "dist":
        project = executable.parent.parent.parent
        if (project / "data").is_dir():
            return project
    return executable.parent


class Desktop:
    def __init__(self, root):
        self.root = root
        self.root.title("CPU Efficiency Lab · 能效工作台")
        self.root.geometry("1240x940")
        self.root.minsize(900, 680)
        self.device = StringVar()
        self.status = StringVar(value="就绪")
        self.result_status = StringVar(value="等待数据")
        self.events = queue.Queue()
        self.busy = False
        self.task_buttons = []
        self.permanently_disabled = set()
        self.recovery_verified = INDEPENDENT_RECOVERY_VERIFIED
        self.current_efficiency = None
        self.selected_core = 0
        self.latest_result = None
        self.pending_result = None
        base = default_workspace()
        self.single_path = StringVar(value=str(base / "data/eeff.tsv") if (base / "data/eeff.tsv").is_file() else "")
        self.combo_path = StringVar(value=str(base / "data/eeff_combo.tsv") if (base / "data/eeff_combo.tsv").is_file() else "")
        self.preflight_path = StringVar()
        known_export = base / "data/scheduler-sources-exe-20261112-204339"
        self.export_path = StringVar(value=str(known_export) if known_export.is_dir() else "")
        self.output_parent = StringVar(value=str(base / "reports"))
        from cpu_efficiency_lab.desktop_views import build
        build(self)
        menu = Menu(root)
        actions = Menu(menu, tearoff=False)
        actions.add_command(label="生成八核曲线与组合分析", accelerator="Ctrl+R", command=self.generate_efficiency)
        actions.add_command(label="载入已有曲线报告", command=self.load_efficiency)
        actions.add_command(label="打开完整报告", accelerator="Ctrl+O", command=self.open_latest)
        actions.add_separator()
        actions.add_command(label="分析调度配置", command=self.generate_scheduler)
        actions.add_command(label="生成实验性 KSU 模块", command=self.generate_module)
        menu.add_cascade(label="操作", menu=actions)
        root.configure(menu=menu)
        root.bind("<Control-r>", lambda event: self.generate_efficiency())
        root.bind("<Control-o>", lambda event: self.open_latest())
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self.poll)
        self.refresh_devices()

    def set_busy(self, value):
        self.busy = value
        for item in self.task_buttons:
            item.state(["disabled"] if value or item in self.permanently_disabled else ["!disabled"])
        if value:
            self.progress.start(12)
        else:
            self.progress.stop()

    def choose_input(self, variable, directory=False):
        path = (filedialog.askdirectory(parent=self.root) if directory else
                filedialog.askopenfilename(parent=self.root, filetypes=[("TSV 文件", "*.tsv"), ("所有文件", "*")]))
        if path:
            variable.set(path)

    def new_result(self, prefix):
        parent = Path(self.output_parent.get()).expanduser()
        if not parent.is_dir():
            parent.mkdir(parents=True, exist_ok=True)
        return fresh_child(str(parent), prefix + "-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))

    def start_result(self, command, output, kind):
        if self.busy:
            messagebox.showinfo("任务进行中", "请等待当前任务结束。", parent=self.root)
            return
        self.pending_result = (Path(output), kind)
        self.start(command)

    def generate_efficiency(self):
        try:
            output = self.new_result("efficiency")
            self.start_result(desktop_command("--efficiency-report", ["--single", self.single_path.get(),
                              "--combo", self.combo_path.get(), "--output", str(output)]), output, "efficiency")
        except (ValueError, OSError) as exc:
            messagebox.showerror("输出路径", str(exc), parent=self.root)

    def generate_scheduler(self):
        try:
            output = self.new_result("scheduler")
            self.start_result(cli_command(["scheduler-report", "--input", self.export_path.get(),
                                          "--output", str(output)]), output, "scheduler")
        except (ValueError, OSError) as exc:
            messagebox.showerror("输出路径", str(exc), parent=self.root)

    def generate_module(self):
        if not messagebox.askyesno("生成后台调度候选", "将生成待实机验证的后台调度模块，不安装到手机。\n省电效果尚未验证，是否生成并查看修改清单？", parent=self.root):
            return
        try:
            output = self.new_result("ksu-candidate")
            self.start_result(desktop_command("--ksu-module", ["--export", self.export_path.get(),
                              "--single", self.single_path.get(), "--combo", self.combo_path.get(),
                              "--output", str(output)]), output, "module")
        except (ValueError, OSError) as exc:
            messagebox.showerror("输出路径", str(exc), parent=self.root)

    def result_finished(self, code):
        pending, self.pending_result = self.pending_result, None
        if code != 0 or pending is None:
            if code != 0:
                self.result_status.set("任务失败，请查看日志")
            return
        output, kind = pending
        self.latest_result = output
        self.result_status.set("结果已保存：" + output.name)
        if kind == "efficiency":
            self.show_efficiency(output)

    def show_efficiency(self, output):
        try:
            report = json.loads((Path(output) / "report.json").read_text(encoding="utf-8"))
            if report.get("schema") != "cpu-lab-efficiency-report-v1":
                raise ValueError("这不是逐核能效报告。")
            self.current_efficiency = report
            if self.selected_core not in {row["core"] for row in report["cores"]}:
                self.selected_core = 0
            from cpu_efficiency_lab.desktop_views import draw_combo_preview, draw_preview
            draw_preview(self)
            draw_combo_preview(self)
            self.latest_result = Path(output)
            self.result_status.set("曲线已载入：" + Path(output).name)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.append("无法读取曲线预览：%s\n" % exc)
            self.result_status.set("报告预览失败，请查看日志")

    def load_efficiency(self):
        source = filedialog.askdirectory(title="选择已有逐核能效报告目录", parent=self.root)
        if source:
            self.show_efficiency(Path(source))

    def open_latest(self):
        if self.latest_result:
            for name in ("index.html", "report.html", "README.md"):
                path = self.latest_result / name
                if path.is_file():
                    self.open_path(str(path))
                    return
        messagebox.showinfo("结果", "请先生成一份报告。", parent=self.root)

    def open_latest_folder(self):
        if self.latest_result:
            self.open_path(str(self.latest_result))

    def append(self, text):
        self.log.insert(END, text)
        self.log.see(END)
        if int(self.log.index("end-1c").split(".")[0]) > 5000:
            self.log.delete("1.0", "1000.0")

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "text":
                    self.append(value)
                elif kind == "devices":
                    ready, states = parse_adb_devices(value)
                    self.device_box["values"] = ready
                    if self.device.get() not in ready:
                        self.device.set(ready[0] if len(ready) == 1 else "")
                    self.append("ADB 设备：%s\n" % (", ".join("%s (%s)" % item for item in states) or "未发现"))
                elif kind == "done":
                    self.set_busy(False)
                    self.status.set("完成" if value == 0 else "失败（退出码 %s）" % value)
                    self.append("\n退出码：%s\n" % value)
                    self.result_finished(value)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def start(self, command, device_list=False):
        if self.busy:
            messagebox.showinfo("任务进行中", "请等待当前任务结束。")
            return
        self.set_busy(True)
        self.status.set("运行中")
        self.append("\n$ %s\n" % subprocess.list2cmdline(command))

        def worker():
            code = 1
            process = None
            try:
                env = os.environ.copy()
                env["PYTHONIOENCODING"] = "utf-8"
                if not getattr(sys, "frozen", False):
                    source = str(Path(__file__).resolve().parent.parent)
                    env["PYTHONPATH"] = source + os.pathsep + env.get("PYTHONPATH", "")
                process = subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", env=env,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if device_list:
                    output, _ = process.communicate(timeout=15)
                    self.events.put(("devices", output))
                else:
                    for line in process.stdout:
                        self.events.put(("text", line))
                code = process.wait()
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                self.events.put(("text", "读取设备列表超时。\n"))
            except OSError as exc:
                self.events.put(("text", "无法启动命令：%s\n" % exc))
            finally:
                if process is not None and process.stdout is not None:
                    process.stdout.close()
                self.events.put(("done", code))

        threading.Thread(target=worker, daemon=True).start()

    def refresh_devices(self):
        self.start(["adb", "devices", "-l"], device_list=True)

    def serial(self):
        serial = self.device.get().strip()
        if not serial:
            messagebox.showerror("请选择设备", "请先刷新设备并选择状态为 device 的设备。")
        return serial

    def output(self, prompt):
        parent = filedialog.askdirectory(title="选择输出文件夹")
        if not parent:
            return None
        name = simpledialog.askstring("输出名称", prompt, parent=self.root)
        if name is None:
            return None
        try:
            return str(fresh_child(parent, name.strip()))
        except ValueError as exc:
            messagebox.showerror("输出路径", str(exc))
            return None

    def preflight(self):
        serial = self.serial()
        if not serial:
            return
        try:
            output = self.new_result("preflight") / "preflight.json"
            self.preflight_path.set(str(output))
            self.pending_result = (output, "preflight")
            self.start(cli_command(["preflight", "--serial", serial, "--output", str(output)]))
        except (ValueError, OSError) as exc:
            messagebox.showerror("输出路径", str(exc), parent=self.root)

    def scheduler_status(self):
        serial = self.serial()
        if serial:
            self.start(cli_command(["scheduler-status", "--serial", serial]))

    def scheduler_sources(self):
        serial = self.serial()
        if not serial:
            return
        model = simpledialog.askstring("核对手机型号", "请输入预检显示的 model", parent=self.root)
        if model is None:
            return
        device = simpledialog.askstring("核对设备代号", "请输入预检显示的 device", parent=self.root)
        if device is None:
            return
        model, device = model.strip(), device.strip()
        if not model or not device:
            messagebox.showerror("设备身份", "型号和设备代号均不能为空。", parent=self.root)
            return
        output = self.output("输入新的调度配置输出文件夹名")
        if output:
            self.start(desktop_command("--scheduler-sources", ["--serial", serial, "--expected-model", model,
                                                               "--expected-device", device, "--output", output]))

    def scheduler_report(self):
        source = filedialog.askdirectory(title="选择已导出的调度配置文件夹（含 manifest.json）")
        if source:
            output = self.output("输入新的官方调度报告文件夹名")
            if output:
                self.start(cli_command(["scheduler-report", "--input", source, "--output", output]))

    def capture(self):
        serial = self.serial()
        if not serial:
            return
        duration = simpledialog.askinteger("采集时长", "采集秒数", initialvalue=10, minvalue=1, maxvalue=7200, parent=self.root)
        if duration is None:
            return
        output = self.output("输入新的 CSV 文件名，例如 battery.csv")
        if output:
            self.start(cli_command(["capture-power", "--serial", serial, "--duration", str(duration), "--interval", "1", "--output", output]))

    def plot(self):
        single = filedialog.askopenfilename(title="选择单核运行数据 TSV", filetypes=[("TSV 文件", "*.tsv"), ("所有文件", "*")])
        if not single:
            return
        combo = filedialog.askopenfilename(title="可选：选择多核数据 TSV，取消可跳过", filetypes=[("TSV 文件", "*.tsv"), ("所有文件", "*")])
        output = self.output("输入新的曲线输出文件夹名")
        if output:
            args = ["plot-efficiency", "--single", single, "--output", output]
            if combo:
                args += ["--combo", combo]
            self.start(cli_command(args))

    def analyze(self):
        source = filedialog.askdirectory(title="选择两小时实验数据文件夹")
        if source:
            output = self.output("输入新的分析输出文件夹名")
            if output:
                self.start(cli_command(["two-hour-analyze", "--input", source, "--output", output]))

    def historical_report(self):
        single = filedialog.askopenfilename(title="选择历史单核数据 TSV", filetypes=[("TSV 文件", "*.tsv"), ("所有文件", "*")])
        if not single:
            return
        combo = filedialog.askopenfilename(title="选择历史多核数据 TSV", filetypes=[("TSV 文件", "*.tsv"), ("所有文件", "*")])
        if not combo:
            return
        output = self.output("输入新的历史报告文件夹名")
        if output:
            self.start(desktop_command("--historical-report", ["--single", single, "--combo", combo, "--output", output]))

    def plan(self):
        source = self.preflight_path.get().strip()
        if not source or not Path(source).is_file():
            source = filedialog.askopenfilename(title="选择已保存的预检 JSON", filetypes=[("JSON 文件", "*.json"), ("所有文件", "*")])
        if source:
            self.preflight_path.set(source)
            output = self.output("输入新的计划文件名，例如 plan.json")
            if output:
                self.start(cli_command(["two-hour-plan", "--preflight", source, "--output", output]))

    def sweep(self):
        serial = self.serial()
        if not serial:
            return
        binary = filedialog.askopenfilename(title="选择 ARM64 CPU 测试程序")
        if not binary:
            return
        output = self.output("输入新的扫频结果前缀")
        if output and messagebox.askyesno("确认设备操作", "扫频将上传并运行 CPU 满载测试，临时调整 CPU 频率。确认开始吗？", parent=self.root):
            self.start(cli_command(["cpu-sweep", "--serial", serial, "--binary", binary, "--output", output, "--confirm-device-writes"]))

    def matrix(self, bounded):
        serial = self.serial()
        if not serial:
            return
        binary = filedialog.askopenfilename(title="选择 ARM64 CPU 测试程序")
        if not binary:
            return
        output = self.output("输入新的实验结果名称")
        if not output or not messagebox.askyesno("确认设备操作", "实验将运行 CPU 满载测试并临时改变频率和核心在线状态。确认开始吗？", parent=self.root):
            return
        args = ["two-hour-sweep" if bounded else "matrix-sweep", "--serial", serial, "--binary", binary, "--output", output,
                "--confirm-device-writes", "--confirm-core-hotplug", "--confirm-independent-recovery"]
        if bounded:
            args.append("--confirm-screen-off")
        self.start(cli_command(args))

    def open_file(self):
        path = filedialog.askopenfilename(title="选择结果或图表")
        if path:
            self.open_path(path)

    def open_folder(self):
        path = filedialog.askdirectory(title="选择结果文件夹")
        if path:
            self.open_path(path)

    def open_path(self, path):
        try:
            os.startfile(path)
        except (OSError, AttributeError) as exc:
            messagebox.showerror("无法打开", str(exc))

    def close(self):
        if self.busy:
            messagebox.showinfo("任务进行中", "请等待当前任务结束，再关闭窗口。", parent=self.root)
            return
        self.root.destroy()


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--cli":
        from cpu_efficiency_lab.cli import main as cli_main
        return cli_main(args[1:])
    if args and args[0] == "--efficiency-report":
        from cpu_efficiency_lab.efficiency_report import main as efficiency_main
        return efficiency_main(args[1:])
    if args and args[0] == "--ksu-module":
        from cpu_efficiency_lab.ksu_module import main as module_main
        return module_main(args[1:])
    if args and args[0] == "--scheduler-sources":
        from cpu_efficiency_lab.scheduler_sources import main as sources_main
        return sources_main(args[1:])
    if args and args[0] == "--historical-report":
        from cpu_efficiency_lab.historical_report import main as report_main
        original_argv = sys.argv
        try:
            sys.argv = [original_argv[0], *args[1:]]
            report_main()
            return 0
        except (OSError, ValueError) as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 2
        finally:
            sys.argv = original_argv
    if args:
        print("Usage: python -m cpu_efficiency_lab.desktop [--cli COMMAND ...]", file=sys.stderr)
        return 2
    root = Tk()
    Desktop(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
