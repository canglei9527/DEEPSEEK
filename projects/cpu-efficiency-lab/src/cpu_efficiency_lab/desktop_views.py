"""Desktop presentation: measured-data analysis, scheduler review and collection."""

from tkinter import Canvas, ttk
from tkinter.scrolledtext import ScrolledText


BG = '#f1f5f6'
PANEL = '#ffffff'
TEXT = '#17262c'
MUTED = '#60747b'
ACCENT = '#087f70'
GRID = '#dce5e7'


def build(app):
    root = app.root
    root.configure(background=BG)
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('.', font=('Microsoft YaHei UI', 10), background=BG, foreground=TEXT)
    style.configure('TFrame', background=BG)
    style.configure('Panel.TFrame', background=PANEL)
    style.configure('TLabel', background=BG, foreground=TEXT)
    style.configure('Panel.TLabel', background=PANEL, foreground=TEXT)
    style.configure('Muted.TLabel', foreground=MUTED)
    style.configure('Title.TLabel', font=('Microsoft YaHei UI', 20, 'bold'))
    style.configure('Heading.TLabel', font=('Microsoft YaHei UI', 12, 'bold'))
    style.configure('Metric.TLabel', background=PANEL, foreground=ACCENT,
                    font=('Microsoft YaHei UI', 14, 'bold'))
    style.configure('TButton', background='#e2eaec', foreground=TEXT, borderwidth=0, padding=(11, 7))
    style.map('TButton', background=[('active', '#d2e1e2'), ('disabled', '#e8eded')],
              foreground=[('disabled', '#8a999d')])
    style.configure('Accent.TButton', background=ACCENT, foreground='#ffffff')
    style.map('Accent.TButton', background=[('active', '#076b60'), ('disabled', '#a9c6c2')])
    style.configure('Selected.TButton', background='#d5ebe6', foreground='#075d53')
    style.map('Selected.TButton', background=[('active', '#c5e2dc')])
    style.configure('TEntry', fieldbackground=PANEL, foreground=TEXT, padding=7, insertcolor=TEXT)
    style.configure('TCombobox', fieldbackground=PANEL, foreground=TEXT, padding=5)
    style.map('TCombobox', fieldbackground=[('readonly', PANEL)], foreground=[('readonly', TEXT)])
    style.configure('TNotebook', background=BG, borderwidth=0)
    style.configure('TNotebook.Tab', padding=(18, 9), background='#e4ebed', foreground=MUTED)
    style.map('TNotebook.Tab', background=[('selected', PANEL)], foreground=[('selected', TEXT)])

    frame = ttk.Frame(root, padding=(20, 14))
    frame.pack(fill='both', expand=True)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(1, weight=1)
    header = ttk.Frame(frame)
    header.grid(row=0, column=0, sticky='ew', pady=(0, 10))
    ttk.Label(header, text='CPU Efficiency Lab', style='Title.TLabel').pack(side='left')
    ttk.Label(header, text='實測分析  ·  配置審閱  ·  設備採集', style='Muted.TLabel').pack(side='left', padx=20)
    app.progress = ttk.Progressbar(header, mode='indeterminate', length=110)
    app.progress.pack(side='right', padx=10)
    ttk.Label(header, textvariable=app.status, style='Muted.TLabel').pack(side='right')

    app.tabs = ttk.Notebook(frame)
    app.tabs.grid(row=1, column=0, sticky='nsew')
    curves, scheduler, device = (ttk.Frame(app.tabs, padding=14) for _ in range(3))
    for page, title in ((curves, '01  分析'), (scheduler, '02  調度'), (device, '03  設備')):
        app.tabs.add(page, text=title)

    build_analysis(app, curves)
    build_scheduler(app, scheduler)
    build_device(app, device)

    footer = ttk.Frame(frame)
    footer.grid(row=2, column=0, sticky='ew', pady=(8, 3))
    ttk.Label(footer, text='任務輸出', style='Muted.TLabel').pack(side='left')
    ttk.Label(footer, text='結果僅新建，不覆蓋既有文件', style='Muted.TLabel').pack(side='right')
    app.log = ScrolledText(frame, height=5, wrap='word', background='#ffffff', foreground=MUTED,
                           insertbackground=TEXT, relief='flat', font=('Consolas', 9), padx=10, pady=7)
    app.log.grid(row=3, column=0, sticky='ew')
    app.tabs.select(curves)
    app.root.after_idle(lambda: app.tabs.select(curves))
    draw_preview(app)
    draw_combo_preview(app)


def build_analysis(app, parent):
    ttk.Label(parent, text='八核心能效', style='Heading.TLabel').pack(anchor='w')
    ttk.Label(parent, text='按實測 core 分組；曲線僅連接已測頻點。', style='Muted.TLabel').pack(anchor='w', pady=(2, 7))
    input_row(app, parent, '單核 TSV', app.single_path, False)
    input_row(app, parent, '組合 TSV', app.combo_path, False)
    row = ttk.Frame(parent)
    row.pack(fill='x', pady=(4, 8))
    input_row(app, row, '結果目錄', app.output_parent, True)
    actions = ttk.Frame(parent)
    actions.pack(fill='x', pady=(0, 7))
    button(app, actions, '重新分析', app.generate_efficiency, accent=True).pack(side='left')
    button(app, actions, '載入報告', app.load_efficiency).pack(side='left', padx=7)
    button(app, actions, '打開報告', app.open_latest).pack(side='left')
    button(app, actions, '歷史測點報告', app.historical_report).pack(side='left', padx=7)
    ttk.Label(actions, textvariable=app.result_status, style='Muted.TLabel').pack(side='left', padx=10)

    app.analysis_tabs = ttk.Notebook(parent)
    app.analysis_tabs.pack(fill='both', expand=True, pady=(2, 6))
    core_page = ttk.Frame(app.analysis_tabs, padding=10)
    combo_page = ttk.Frame(app.analysis_tabs, padding=10)
    app.analysis_tabs.add(core_page, text='逐核曲線')
    app.analysis_tabs.add(combo_page, text='頻率組合')

    selector = ttk.Frame(core_page)
    selector.pack(fill='x', pady=(0, 7))
    ttk.Label(selector, text='核心', style='Muted.TLabel').pack(side='left', padx=(0, 8))
    app.core_buttons = []
    for core in range(8):
        b = button(app, selector, 'CPU%d' % core, lambda value=core: select_core(app, value))
        b.pack(side='left', padx=2)
        app.core_buttons.append(b)

    metrics = ttk.Frame(core_page, style='Panel.TFrame', padding=(12, 7))
    metrics.pack(fill='x', pady=(0, 5))
    app.core_metric = ttk.Label(metrics, text='尚未載入報告', style='Metric.TLabel')
    app.core_metric.pack(side='left')
    app.core_detail = ttk.Label(metrics, text='', style='Muted.TLabel')
    app.core_detail.pack(side='right')
    app.preview = Canvas(core_page, background=PANEL, highlightthickness=0, height=300)
    app.preview.pack(fill='both', expand=True)
    app.preview.bind('<Configure>', lambda event: draw_preview(app))

    ttk.Label(combo_page, text='policy0 × policy6 · nJ/op 越低越好', style='Heading.TLabel').pack(anchor='w')
    ttk.Label(combo_page, text='單次實測紀錄；缺測留空，不能據此推斷全局最優。', style='Muted.TLabel').pack(anchor='w', pady=(2, 6))
    app.combo_metrics = ttk.Frame(combo_page, style='Panel.TFrame', padding=(10, 7))
    app.combo_metrics.pack(fill='x', pady=(0, 5))
    app.combo_metric_labels = []
    for _ in range(3):
        label = ttk.Label(app.combo_metrics, text='尚未載入報告', style='Panel.TLabel')
        label.pack(side='left', expand=True, anchor='w', padx=5)
        app.combo_metric_labels.append(label)
    app.combo_preview = Canvas(combo_page, background=PANEL, highlightthickness=0, height=300)
    app.combo_preview.pack(fill='both', expand=True)
    app.combo_preview.bind('<Configure>', lambda event: draw_combo_preview(app))
    ttk.Label(parent, text='計量邊界：歷史數據無配對底噪，顯示整機電池側能耗；最佳值僅為已觀測最低點，非芯片調度建議。',
              style='Muted.TLabel', wraplength=1100).pack(anchor='w', pady=(3, 0))


def build_scheduler(app, parent):
    ttk.Label(parent, text='官方配置離線審閱', style='Heading.TLabel').pack(anchor='w')
    ttk.Label(parent, text='分析已保存的配置導出，不代表設備當前運行狀態。', style='Muted.TLabel').pack(anchor='w', pady=(2, 8))
    input_row(app, parent, '配置導出', app.export_path, True)
    input_row(app, parent, '單核 TSV', app.single_path, False)
    input_row(app, parent, '組合 TSV', app.combo_path, False)
    actions = ttk.Frame(parent)
    actions.pack(fill='x', pady=8)
    button(app, actions, '分析已導出配置', app.generate_scheduler).pack(side='left')
    button(app, actions, '打開最近結果', app.open_latest_folder).pack(side='left', padx=8)

    ttk.Separator(parent).pack(fill='x', pady=8)
    ttk.Label(parent, text='KernelSU 實驗候選', style='Heading.TLabel').pack(anchor='w')
    card = ttk.Frame(parent, style='Panel.TFrame', padding=12)
    card.pack(fill='x', pady=7)
    for text in (
        '僅新增已導出 CpuPolicyPack 至兩個後台 task-profile 聚合；這不是替換內核調度器。',
        '現有曲線缺少配對底噪與芯片身份，無法支持體質微調；省電、延遲與運行時加載均未驗證。',
        '只生成帶設備指紋/源配置哈希檢查的 systemless ZIP，不安裝、不改設備；卸載或禁用後重啟為預期回滾路徑。',
        '僅供審閱候選。使用前仍需獨立設備授權、兼容掛載模塊、啟動/回滾驗證及能效對照。'):
        ttk.Label(card, text=text, style='Panel.TLabel', wraplength=1050).pack(anchor='w', pady=3)
    button(app, parent, '生成實驗 KSU ZIP', app.generate_module, accent=True).pack(anchor='w', pady=3)


def build_device(app, parent):
    ttk.Label(parent, text='設備與採集', style='Heading.TLabel').pack(anchor='w')
    row = ttk.Frame(parent)
    row.pack(fill='x', pady=(7, 10))
    ttk.Label(row, text='ADB 設備', width=13).pack(side='left')
    app.device_box = ttk.Combobox(row, textvariable=app.device, state='readonly')
    app.device_box.pack(side='left', fill='x', expand=True)
    button(app, row, '刷新', app.refresh_devices).pack(side='left', padx=(8, 0))
    actions = ttk.Frame(parent)
    actions.pack(fill='x', pady=(0, 10))
    button(app, actions, '只讀預檢並保存', app.preflight, accent=True).pack(side='left')
    button(app, actions, '只讀調度狀態', app.scheduler_status).pack(side='left', padx=7)
    button(app, actions, '導出調度配置', app.scheduler_sources).pack(side='left')
    button(app, actions, '採集電池讀數', app.capture).pack(side='left', padx=7)
    input_row(app, parent, '已保存預檢', app.preflight_path, False)
    row = ttk.Frame(parent)
    row.pack(fill='x', pady=5)
    button(app, row, '按預檢生成測量計劃', app.plan).pack(side='left')
    button(app, row, '分析已有低噪聲實驗', app.analyze).pack(side='left', padx=7)
    ttk.Separator(parent).pack(fill='x', pady=10)
    ttk.Label(parent, text='CPU 實驗', style='Heading.TLabel').pack(anchor='w')
    ttk.Label(parent, text='需要逐次授權；矩陣與兩小時掃描在恢復驗證門禁解除前保持禁用。',
              style='Muted.TLabel').pack(anchor='w', pady=3)
    row = ttk.Frame(parent)
    row.pack(fill='x', pady=6)
    button(app, row, '溫控 CPU 掃頻', app.sweep).pack(side='left')
    for label, bounded in (('矩陣掃頻', False), ('兩小時掃描', True)):
        b = button(app, row, label, lambda value=bounded: app.matrix(value))
        b.pack(side='left', padx=7)
        if not app.recovery_verified:
            b.state(['disabled'])
            app.permanently_disabled.add(b)


def button(app, parent, text, command, accent=False):
    style = 'Accent.TButton' if accent else 'TButton'
    item = ttk.Button(parent, text=text, command=command, style=style)
    app.task_buttons.append(item)
    return item


def input_row(app, parent, label, variable, directory):
    row = ttk.Frame(parent)
    row.pack(fill='x', pady=3)
    ttk.Label(row, text=label, width=13).pack(side='left')
    ttk.Entry(row, textvariable=variable).pack(side='left', fill='x', expand=True)
    button(app, row, '選擇目錄' if directory else '選擇文件',
           lambda: app.choose_input(variable, directory)).pack(side='left', padx=(8, 0))


def select_core(app, core):
    app.selected_core = core
    draw_preview(app)


def draw_preview(app):
    canvas = app.preview
    canvas.delete('all')
    width, height = max(500, canvas.winfo_width()), max(240, canvas.winfo_height())
    report = app.current_efficiency
    selected = getattr(app, 'selected_core', 0)
    from .efficiency_report import COLORS
    for core, item in enumerate(app.core_buttons):
        item.configure(style='Selected.TButton' if core == selected else 'TButton')
    cores = {row['core']: row for row in report['cores']} if report else {}
    core = cores.get(selected)
    if core is None:
        canvas.create_text(width / 2, height / 2, text='選擇 CPU0–CPU7 並載入逐核報告',
                           fill=MUTED, font=('Microsoft YaHei UI', 14))
        app.core_metric.configure(text='尚未載入報告')
        app.core_detail.configure(text='')
        return

    best = core['best_observed']
    app.core_metric.configure(text='CPU%d   %.1f MHz   %.4f nJ/op' %
                              (selected, best['freq_khz'] / 1000, best['battery_nj_per_op']))
    app.core_detail.configure(text='%.2f Mop/s  ·  %.3f W  ·  %d 頻點  ·  單次/頻點' %
                              (best['mops'], best['avg_w'], len(core['points'])))
    points = core['points']
    left, top, right, bottom = 78, 32, width - 28, height - 52
    x0, x1 = min(p['freq_khz'] for p in points), max(p['freq_khz'] for p in points)
    y0, y1 = min(p['battery_nj_per_op'] for p in points), max(p['battery_nj_per_op'] for p in points)
    margin = max((y1 - y0) * .12, .1)
    y0, y1 = max(0, y0 - margin), y1 + margin
    for tick in range(5):
        y = top + (bottom - top) * tick / 4
        value = y1 - (y1 - y0) * tick / 4
        canvas.create_line(left, y, right, y, fill=GRID)
        canvas.create_text(left - 9, y, text='%.2f' % value, anchor='e', fill=MUTED,
                           font=('Consolas', 9))
        x = left + (right - left) * tick / 4
        freq = (x0 + (x1 - x0) * tick / 4) / 1000
        canvas.create_text(x, bottom + 18, text='%.0f' % freq, fill=MUTED,
                           font=('Consolas', 9))
    canvas.create_text(18, top, text='nJ/op', anchor='w', fill=MUTED,
                       font=('Microsoft YaHei UI', 9))
    canvas.create_text((left + right) / 2, height - 10, text='頻率 MHz', fill=MUTED,
                       font=('Microsoft YaHei UI', 9))

    def point_xy(point):
        return (left + (point['freq_khz'] - x0) / max(1, x1 - x0) * (right - left),
                top + (y1 - point['battery_nj_per_op']) / max(.001, y1 - y0) * (bottom - top))

    coordinates = [coordinate for point in points for coordinate in point_xy(point)]
    if len(coordinates) >= 4:
        canvas.create_line(*coordinates, fill=COLORS[selected], width=2)
    for point in points:
        x, y = point_xy(point)
        radius = 6 if point == best else 3
        canvas.create_oval(x - radius, y - radius, x + radius, y + radius,
                           fill=COLORS[selected], outline='#ffffff' if point == best else COLORS[selected],
                           width=2 if point == best else 1,
                           tags=('best-point' if point == best else 'measurement-point'))


def draw_combo_preview(app):
    canvas = app.combo_preview
    canvas.delete('all')
    width, height = max(500, canvas.winfo_width()), max(230, canvas.winfo_height())
    report = app.current_efficiency
    if not report:
        canvas.create_text(width / 2, height / 2, text='載入報告後顯示頻率組合與缺測格',
                           fill=MUTED, font=('Microsoft YaHei UI', 14))
        for label in app.combo_metric_labels:
            label.configure(text='尚未載入報告')
        return

    stats = (('最低觀測 nJ/op', report['best_combo_observed'], 'battery_nj_per_op'),
             ('最高吞吐 Mop/s', report['highest_throughput_combo'], 'total_ops_per_s'),
             ('最低整機 W', report['lowest_power_combo'], 'avg_w'))
    for label, (title, point, key) in zip(app.combo_metric_labels, stats):
        value = point[key] / 1e6 if key == 'total_ops_per_s' else point[key]
        label.configure(text='%s  %.3f\n%.1f + %.1f MHz' %
                        (title, value, point['freq_small_khz'] / 1000, point['freq_big_khz'] / 1000))

    rows = report['combo_records']
    xs = sorted({row['freq_small_khz'] for row in rows})
    ys = sorted({row['freq_big_khz'] for row in rows}, reverse=True)
    cells = {(row['freq_small_khz'], row['freq_big_khz']): row for row in rows}
    best = report['best_combo_observed']
    best_pair = best['freq_small_khz'], best['freq_big_khz']
    left, top = 95, 36
    cell_w = max(74, min(138, (width - left - 24) / max(1, len(xs))))
    cell_h = min(56, max(42, (height - top - 34) / max(1, len(ys))))
    values = [row['battery_nj_per_op'] for row in rows]
    lo, hi = min(values), max(values)
    canvas.create_text(left / 2, top - 15, text='policy6\nMHz', fill=MUTED, font=('Microsoft YaHei UI', 9))
    for col, freq in enumerate(xs):
        x = left + col * cell_w + cell_w / 2
        canvas.create_text(x, top - 10, text='%.1f' % (freq / 1000), fill=MUTED,
                           font=('Consolas', 9))
    for row_index, freq in enumerate(ys):
        y = top + row_index * cell_h
        canvas.create_text(left - 8, y + cell_h / 2, text='%.1f' % (freq / 1000), anchor='e',
                           fill=MUTED, font=('Consolas', 9))
        for col, small_freq in enumerate(xs):
            x = left + col * cell_w
            point = cells.get((small_freq, freq))
            if point is None:
                fill, value_text, outline = '#e8edef', '未測', GRID
            else:
                ratio = (point['battery_nj_per_op'] - lo) / max(.001, hi - lo)
                fill = '#%02x%02x%02x' % (231 - int(50 * ratio), 244 - int(27 * ratio), 239 - int(10 * ratio))
                value_text, outline = '%.2f' % point['battery_nj_per_op'], GRID
            if (small_freq, freq) == best_pair:
                outline = ACCENT
            canvas.create_rectangle(x + 2, y + 2, x + cell_w - 2, y + cell_h - 2,
                                    fill=fill, outline=outline,
                                    width=3 if (small_freq, freq) == best_pair else 1)
            canvas.create_text(x + cell_w / 2, y + cell_h / 2, text=value_text, fill=TEXT,
                               font=('Consolas', 9, 'bold' if (small_freq, freq) == best_pair else 'normal'))
    canvas.create_text(left + len(xs) * cell_w / 2, top + len(ys) * cell_h + 13,
                       text='policy0 頻率 MHz  ·  綠框為最低觀測點', fill=MUTED,
                       font=('Microsoft YaHei UI', 9))
