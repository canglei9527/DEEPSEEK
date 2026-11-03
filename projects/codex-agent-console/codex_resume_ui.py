"""A lightweight Tk view of the independent quota-resume service.

The view only reads the service's in-memory snapshot.  It never scans sessions,
opens a desktop connection, or starts its own worker thread.
"""

from __future__ import annotations

from datetime import datetime
import math
import tkinter as tk
from tkinter import ttk
from typing import Any, Protocol


class ResumeService(Protocol):
    def snapshot(self) -> dict[str, Any]: ...

    def set_enabled(self, enabled: bool) -> None: ...

    def exclude(self, key: str) -> None: ...

    def wake(self) -> None: ...


_STATE_LABELS = {
    "waiting": "等待重试",
    "pending": "等待重试",
    "checking": "正在核实",
    "resuming": "已发恢复，待确认",
    "resumed": "已恢复",
    "cancelled": "已取消",
    "canceled": "已取消",
    "excluded": "本次已取消",
    "skipped": "已跳过",
    "superseded": "已人工处理",
    "uncertain": "需人工确认",
    "blocked": "暂不可恢复",
    "exhausted": "重试已用尽",
    "failed": "恢复失败",
    "error": "恢复失败",
}

_KIND_LABELS = {
    "quota": "额度",
    "capacity": "容量",
}


def _format_time(value: Any) -> str:
    if value is None or value == "":
        return "待确认"
    try:
        timestamp = float(value)
        if not math.isfinite(timestamp):
            return "待确认"
        return datetime.fromtimestamp(timestamp).strftime("%m-%d %H:%M:%S")
    except (TypeError, ValueError, OverflowError, OSError):
        return str(value)


class ResumeWindow:
    """Reusable child window; closing it never stops the background service."""

    REFRESH_MS = 2000

    def __init__(self, owner: tk.Tk, service: ResumeService) -> None:
        self.owner = owner
        self.service = service
        self._window: tk.Toplevel | None = None
        self._timer_id: str | None = None
        self._enabled = False
        self._item_keys: dict[str, str] = {}
        self._items: dict[str, dict[str, Any]] = {}
        self._next_iid = 0

    def show(self) -> None:
        if self._window is None or not self._window.winfo_exists():
            self._build()
        window = self._window
        if window is None:
            return
        window.deiconify()
        window.lift()
        window.update_idletasks()
        self._resume_refresh()

    def close(self) -> None:
        self._cancel_refresh()
        window = self._window
        self._window = None
        self._item_keys.clear()
        self._items.clear()
        if window is not None:
            try:
                window.destroy()
            except tk.TclError:
                pass

    def _build(self) -> None:
        window = tk.Toplevel(self.owner)
        self._window = window
        window.title("额度续跑 · Codex Agent Console")
        window.geometry("900x520")
        window.minsize(780, 460)
        window.protocol("WM_DELETE_WINDOW", self.close)
        background = ttk.Style(self.owner).lookup("TFrame", "background")
        if background:
            window.configure(background=background)

        shell = ttk.Frame(window, padding=(20, 16))
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(4, weight=1)

        header = ttk.Frame(shell)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="额度续跑", style="Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.enabled_var = tk.BooleanVar(master=window, value=self._enabled)
        ttk.Checkbutton(
            header, text="开启自动续跑", variable=self.enabled_var,
            command=self._toggle_enabled,
        ).grid(row=0, column=1, sticky="e")

        self._notice = ttk.Label(
            shell,
            text=(
                "控制台运行期间生效，关闭主窗口停止；关闭本窗口不影响后台。\n"
                "手动停止 / 新指令后取消本次恢复；支持明确额度或模型容量不足，不处理普通网络 429。"
            ),
            style="Subtitle.TLabel", wraplength=850, justify="left",
        )
        self._notice.grid(row=1, column=0, sticky="ew", pady=(6, 10))

        self.status_var = tk.StringVar(master=window, value="正在读取后台状态…")
        self._status_label = ttk.Label(
            shell, textvariable=self.status_var, wraplength=850, justify="left"
        )
        self._status_label.grid(row=2, column=0, sticky="ew", pady=(0, 8))

        actions = ttk.Frame(shell)
        actions.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        actions.columnconfigure(0, weight=1)
        self.summary_var = tk.StringVar(master=window, value="任务记录：0")
        ttk.Label(actions, textvariable=self.summary_var, style="Subtitle.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.exclude_button = ttk.Button(
            actions, text="取消选中任务本次恢复", command=self._exclude_selected,
            state="disabled",
        )
        self.exclude_button.grid(row=0, column=1, padx=(8, 6))
        ttk.Button(actions, text="立即检查", command=self._wake).grid(
            row=0, column=2, sticky="e"
        )

        table = ttk.Frame(shell)
        table.grid(row=4, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            table, columns=("title", "kind", "state", "reset", "detail"),
            show="headings", selectmode="browse",
        )
        for name, title, width, minimum in (
            ("title", "任务标题", 220, 140),
            ("kind", "中断类型", 90, 70),
            ("state", "状态", 110, 85),
            ("reset", "预计恢复", 140, 130),
            ("detail", "说明", 300, 160),
        ):
            self.tree.heading(name, text=title)
            self.tree.column(name, width=width, minwidth=minimum, stretch=True)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.bind("<<TreeviewSelect>>", self._selection_changed)

        self.selection_var = tk.StringVar(master=window, value="选择任务可查看完整说明。")
        self._selection_label = ttk.Label(
            shell, textvariable=self.selection_var, style="Subtitle.TLabel",
            wraplength=850, justify="left",
        )
        self._selection_label.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        self.action_var = tk.StringVar(master=window, value="")
        self._action_label = ttk.Label(
            shell, textvariable=self.action_var, wraplength=850, justify="left"
        )
        self._action_label.grid(row=6, column=0, sticky="ew", pady=(4, 0))

        window.bind("<Map>", self._on_map, add="+")
        window.bind("<Unmap>", self._on_unmap, add="+")
        window.bind("<Destroy>", self._on_destroy, add="+")
        window.bind("<Configure>", self._on_configure, add="+")

    def _is_visible(self) -> bool:
        try:
            return bool(self._window is not None and self._window.winfo_viewable())
        except tk.TclError:
            return False

    def _resume_refresh(self) -> None:
        if self._timer_id is None and self._is_visible():
            self._refresh_snapshot()
            self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        if self._window is not None and self._timer_id is None and self._is_visible():
            self._timer_id = self._window.after(self.REFRESH_MS, self._tick)

    def _cancel_refresh(self) -> None:
        timer_id, self._timer_id = self._timer_id, None
        if timer_id is not None and self._window is not None:
            try:
                self._window.after_cancel(timer_id)
            except tk.TclError:
                pass

    def _tick(self) -> None:
        self._timer_id = None
        if not self._is_visible():
            return
        self._refresh_snapshot()
        self._schedule_refresh()

    def _on_map(self, event: tk.Event) -> None:
        if event.widget is self._window:
            self._resume_refresh()

    def _on_unmap(self, event: tk.Event) -> None:
        if event.widget is self._window:
            self._cancel_refresh()

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self._window:
            self._cancel_refresh()
            self._window = None
            self._item_keys.clear()
            self._items.clear()

    def _on_configure(self, event: tk.Event) -> None:
        if event.widget is self._window:
            width = max(300, event.width - 44)
            for label in (
                self._notice, self._status_label, self._selection_label, self._action_label
            ):
                label.configure(wraplength=width)

    def _refresh_snapshot(self) -> None:
        try:
            snapshot = self.service.snapshot()
            self._enabled = bool(snapshot.get("enabled", False))
            self.enabled_var.set(self._enabled)
            enabled = "已开启" if self._enabled else "已关闭"
            connection = str(snapshot.get("connection") or "连接状态待确认")
            status = str(snapshot.get("status") or "等待后台状态")
            next_check = snapshot.get("next_check_at")
            next_text = f"  ·  下次检查 {_format_time(next_check)}" if next_check else ""
            self.status_var.set(f"{enabled}  ·  {connection}  ·  {status}{next_text}")
            self._update_items(snapshot.get("items") or [])
        except Exception as exc:  # Snapshot failures must not break the Tk event loop.
            self.status_var.set(f"状态读取失败：{exc}")

    def _update_items(self, items: list[dict[str, Any]]) -> None:
        by_key = {str(item["key"]): item for item in items if item.get("key")}
        for key in tuple(self._item_keys):
            if key not in by_key:
                self.tree.delete(self._item_keys.pop(key))
        for position, (key, item) in enumerate(by_key.items()):
            state = str(item.get("state") or "待确认")
            values = (
                str(item.get("title") or item.get("thread_id") or "未命名任务"),
                _KIND_LABELS.get(str(item.get("kind") or "quota"), "未知"),
                _STATE_LABELS.get(state, state),
                _format_time(item.get("reset_at")),
                str(item.get("detail") or ""),
            )
            iid = self._item_keys.get(key)
            if iid is None:
                self._next_iid += 1
                iid = f"resume-{self._next_iid}"
                self._item_keys[key] = iid
                self.tree.insert("", "end", iid=iid, values=values)
            elif tuple(self.tree.item(iid, "values")) != values:
                self.tree.item(iid, values=values)
            self.tree.move(iid, "", position)
        self._items = by_key
        self.summary_var.set(f"任务记录：{len(by_key)}")
        self._selection_changed()

    def _selected_key(self) -> str | None:
        selected = self.tree.selection()
        if not selected:
            return None
        return next((key for key, iid in self._item_keys.items() if iid == selected[0]), None)

    def _selection_changed(self, _event: tk.Event | None = None) -> None:
        key = self._selected_key()
        self.exclude_button.configure(state="normal" if key else "disabled")
        item = self._items.get(key or "")
        if item is None:
            self.selection_var.set("选择任务可查看完整说明。")
            return
        thread_id = str(item.get("thread_id") or "未知")
        self.selection_var.set(f"任务：{thread_id}\n{item.get('detail') or '暂无补充说明。'}")

    def _toggle_enabled(self) -> None:
        try:
            self.service.set_enabled(bool(self.enabled_var.get()))
            self.action_var.set("设置已保存。" if self.enabled_var.get() else "已关闭自动续跑。")
        except Exception as exc:
            self.enabled_var.set(self._enabled)
            self.action_var.set(f"设置失败：{exc}")
        self._refresh_snapshot()

    def _exclude_selected(self) -> None:
        key = self._selected_key()
        if key is None:
            return
        try:
            self.service.exclude(key)
            self.action_var.set("已取消所选任务本次恢复。")
        except Exception as exc:
            self.action_var.set(f"取消失败：{exc}")
        self._refresh_snapshot()

    def _wake(self) -> None:
        try:
            self.service.wake()
            self.action_var.set("已请求检查；后台仍会核实额度与任务状态。")
        except Exception as exc:
            self.action_var.set(f"请求检查失败：{exc}")
        self._refresh_snapshot()
