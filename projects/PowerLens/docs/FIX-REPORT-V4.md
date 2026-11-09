# PowerLens 修复报告 V4 —— 功耗数字全部错误

日期：2026-11-09　设备：Xiaomi 15 Pro (2410DPN6CC/haotian, Android 16, KernelSU)

## 触发问题

> 功耗显示完全不对。你告诉我从 12 点到现在耗电最多的是一个"安全服务"？
> 而且这个安全服务在一小时前还是完全没有的。

用户直觉是对的。这一句话背后压着 **4 个独立 bug**，其中一个是灾难级的。

---

## BUG 1（灾难级）：`parse_dur` 无边界 —— 所有时长虚高 9 倍

### 现象

`dims.prev` 里 u0a341（B 站）前台时长 = **23835760 ms = 6h37m**。
而 dumpsys 原文写的是 `fg: 545 (43m 42s 223ms)` = 2622223 ms。
**虚高 9.09 倍**。

### 根因

`paren_after()` 找到右括号后，把括号**之后**的内容也一起交给 `parse_dur()`：

```c
const char *rp = strchr(q, ')');
return parse_dur_bounded(q + 1, rp);   // 修复后
```

修复前是 `parse_dur(q + 1)`，一路读到行尾 `\0`。而明细行是这样的：

```
fg: 545 (43m 42s 223ms) bg: 34.3 (15s 496ms) fgs: 184 (5h 58m 53s 606ms) cached: 0.0195 (98ms)
```

于是 "fg" 解析出了**整行所有字段的时长之和**：
43m42s223ms + 15s496ms + 5h58m53s606ms + 98ms ≈ 6h37m ✓ 完全吻合。

后果：**每一个 fg/bg/fgs 排名、每一个占比、每一个 mAh 分摊全错**。
排序也是错的，所以"谁是第一"本身就是错的。

### 教训

解析"括号内内容"时，指针交给下游函数之前必须带**右边界**。
`strchr` 找到 `)` 却不使用它，等于白找。

---

## BUG 2：`bg: 1.46`（无时长）抓到了下一个字段的括号

```
UID u0a351: 30.3 fg: 1.47 (5m 44s) bg: 1.46 fgs: 16.9 (7h 51m)
```

`bg` 后面根本没有括号。旧代码用 `strchr(k, '(')` 往后找，
会跨过 `bg` 一路找到 **fgs 的 (7h 51m)** —— bg 被灌成 7h51m。

**修复规则**：key 之后只允许跨过 `数字 / . / - / 空格`，
下一个字符**必须直接**是 `(`，否则返回 0。

```c
const char *q = k + klen;
while (*q == ' ' || *q == '-' || *q == '.' || isdigit(*q)) q++;
if (*q != '(') return 0;                 // 本字段没有时长
```

---

## BUG 3：`-d DIR` 让 `argv[++i]` 自增两次

```c
snprintf(out,  sizeof out,  "%s/dims.tsv",  argv[++i]);
snprintf(prev, sizeof prev, "%s/dims.prev", argv[++i]);   // ← 第二次，越界
```

第二次取到 `NULL`，`prev` 变成 `"(null)/dims.prev"`，
在测试目录里基线永远存不上（`cp: No such file or directory`）。
这也解释了**为什么这个 bug 在生产环境没暴露** ——
生产不带 `-d`，只有自测带。自测目录静默失败，验证结果全是假绿。

---

## BUG 4：`(on battery, screen off/doze)` 标记与数据同行

dumpsys 实际长这样：

```
(on battery, screen off/doze) cpu=6.72 (2m 22s 207ms) wakelock=0.09 (3s 619ms)
```

旧代码 `if (strstr(line, "(on battery, screen off/doze)")) { inOffDoz = 1; continue; }`
遇到标记就跳过整行 —— **后面的 cpu=6.72 永远读不到**。
所以 `cpuOff` / `wlOff` 一直是 0，"息屏耗电"这一整列从未真正工作过。

**修复**：设完标志**不要 continue**，继续解析本行剩余字段。

---

## BUG 5：`strchr(col,'\t')` 命中自身 —— mAh 数组错位 8 列

v3 引入 per-UID mAh 后，测试输出：

```
u0a341  Δcpu = 124659 µAh（124 mAh / 31 秒）
```

物理上不可能（全机 8 小时才 2377 mAh）。实际值是
`135000(cur cpu µAh) − 10341(uid)` —— **整行错位了 8 列**。

`for (t=0; t<8; t++) col = strchr(col,'\t');` 第一次找到 tab₁，
第二次从 tab₁ 位置再 `strchr` **又找到同一个 tab₁**（它匹配自身），
于是 8 次循环只跳过了 1 个 tab。

**修复**：抽 `skip_cols()`，每次找到 tab 后**立刻 +1 越过它**。

---

## BUG 6：AOSP UID 表在小米上完全错 → 幽灵"安全服务"

这是用户第一眼看到的错。我硬编码了 AOSP 的 1000-1100 UID 语义表：

```c
{1041, "security"}      // AOSP 里 1041 确实叫 security
```

但小米 HyperOS 重排过这张表。`/proc` 实测：**uid 1041 = `audioserver`**
（vendor.qti.audiohal + audioserver），跟 security 毫无关系。
1077、1078 之类的偏移更离谱。

于是界面上凭空冒出一个"安全服务"，
而且因为它 CPU 不为 0，被顶到了耗电第一名 ——
"一小时前还完全没有"是因为**那时它还没被采样到**。

**修复原则**：
1. 只硬编码 3 个确定项（0 / 1000 / 2000）。
2. 其余一律 `getenforce` 之外走 **`/proc/<pid>/status` 的 Name 反查**，
   带打分（不含 `:`、含 `.`、长度合理）。
3. 查不到就诚实显示 `sys:uid<N>`，**绝不猜一个名字出来**。

猜名字 = 制造幽灵。用户能发现这次，下次就发现不了。

---

## 核心架构变更：不再猜权重，直接用 dumpsys 的模型电量

### 旧逻辑（错的）

```java
weight = cpu秒 + 息屏cpu秒 + 唤醒锁秒×0.6 + 流量MB×0.44
it.mah = 实测放电 × (weight / Σweight)
```

后果（4.5h 区间，实测放电 1207 mAh）：

| UID | 真实 CPU | 分到 |
|---|---|---|
| sys:logd | 1 分钟 | **463.5 mAh (38.4%)** |
| audioserver | 1 分钟 | **347.2 mAh** |

**结构性错误**：屏幕(560 mAh) + 基带(1005 mAh) + 待机底噪这些
"平台级"功耗，被强行摊到了少数几个**恰好有 CPU 时间**的进程头上。

### 新逻辑

`dumpsys batterystats` 本身就给了每个 UID 的系统模型电量：

```
Estimated power use (mAh):
  Capacity: 6100, Computed drain: 2768, actual drain: 2768
  screen: 560  cpu: 2359  mobile_radio: 1005  audio: 41.0 ...

UID u0a341: ...
    screen=71.6 cpu=135 audio=22.0 video=38.2 mobile_radio=558 ...
```

这是系统按硬件功耗模型算好的、跨 UID 可加总回 Global 的值。

```java
useMah = dims.hasMah();
weight(uid) = dumpsys 模型 mAh(uid)
it.mah = 实测放电 × (模型mAh(uid) / Σ模型mAh)
```

**份额交给系统的功耗模型，总量锚定真实电池计数器** ——
两头都物理正确。旧格式 dims.tsv 自动退回权重法（`hasMah()==false`）。

### 补齐"没有 CPU 却有电量"的 UID

plcollect 走 `/proc/uid_cputime`，只统计有 CPU 时间的 UID。
但纯硬件型消耗（屏幕、基带待机、传感器）对应的 UID 整段区间 CPU=0，
会被整个漏掉。新增：遍历 `dims.byUid`，把 procfs 里不存在、
但 dumpsys 有电量的 UID 补进列表（CPU/IO 留空，只带模型电量）。

### 物理上界自检

`dims.global` 存全机各字段累计值。任何单 UID 的单字段增量
都不可能超过全机同字段增量：

```c
if (g_havePrevGlobal && dd > gDelta[k]) dd = gDelta[k];
```

超出即判定基线异常并钳制 —— **宁可少报，绝不报出物理上不可能的数**。
这次连续抓出两个真 bug（BUG 5 的错位、BUG 2 的跨字段误匹配），
没有这道自检它们会继续静默污染数据。

---

## BUG 7：`isAggregate` 把最大耗电应用藏了

```java
|| (uid >= 10000 && uid < 10500);   // 注释写着"系统服务 UID 段"
```

Android 的 AID 划分里 **10000..19999 是 user 0 安装的应用**
（u0aXXX → 10000+XXX）。`10341` 正是 B 站，不是什么系统服务。

结果：勾选"采集·应用"时，实测区间里**电量第一名被直接隐藏**。

修复为按 AID 规范判定"是不是应用 UID"，其余全归聚合容器：

```java
private static boolean isAppUid(int uid) {
    if (uid >= 10000 && uid < 20000) return true;          // user 0 应用
    if (uid >= 100000 && uid < 1000000) {                  // u1..u9
        int sub = uid % 100000;
        return sub >= 10000 && sub < 20000;
    }
    if (uid >= 1000000) {                                  // u10+ 分身
        int sub = uid % 100000;
        return sub >= 10000 && sub < 20000;
    }
    return false;
}
```

---

## BUG 8：LINEBUF=1024 截断长明细行

`u0a341` 的全时段明细行有 1500+ 字符：

```
screen=71.6 cpu=135 (44m 10s) cpu:fg=108 (...) cpu:bg=4.55 (...) audio=22.0 (...) video=38.2 (...) mobile_radio=558 ...
```

`fgets` 在 1024 处把它劈成两段，后半段以 `audio:cached=...` 开头，
被当成**另一条数据行**再次解析 → 同一 UID 的值重复污染。
`LINEBUF` 提到 8192 并放入静态区。

---

## BUG 9：headline 重复累加

dumpsys 的 fg/bg/fgs 是**状态量**（累计时长），同一个 UID 的 headline
在统计周期内可能重复出现。旧代码 `+=` 会把 5h 灌成 20h。
改为**首次出现即采纳**（`== 0` 守卫）。

---

## BUG 10：HashMap 跨线程并发

`uidPkgCache` 会被 `pl-refresh` 后台线程（补齐 UID 时调 `uidToPkg`）
和 UI 线程（`AppAdapter.getView` 调 `label`/`icon`）同时读写。
HashMap 在并发写时可能形成链表环，`get` 会**死循环卡死**。
全部换成 `ConcurrentHashMap`。

同时 `pkgNameFromUid` 原是非静态内部类 AppAdapter 的方法，
在 refresh 线程里调不到，提升为 `MainActivity.uidToPkg()`，
并改为**优先走 PackageManager**（App 自身权限即可，不需 root）。

---

## 存储侧修复（本轮之前已合入，一并记录）

- **15 mAh/min 跳变上限杀掉了真实耗电**：实测 110 mAh/6.3 min = 1043 mA
  被当跳变丢弃，整段区间少算 12%。上限提到 30 mAh/min，绝对下限 30 mAh。
- **电流分母必须剔除充电时长**：`ratePerHourUahActive` 只用纯放电时间，
  充电时段稀释可达 30%+。状态栏明示"其中充电 X 小时（已从电流分母剔除）"。

---

## 实测结果

`dumpsys` 与 pldims v3 交叉核对：

| UID | v3 采集 | dumpsys 原文 |
|---|---|---|
| u0a341 | cpu=135.0 screen=72.0 radio=571.0 | `screen=71.6 cpu=135 mobile_radio=558` ✓ |
| uid 0   | cpu=759.0 | `cpu=754` ✓ |
| uid 1000 | cpu=713.0 wl=297786ms | `cpu=709 wakelock=1.87 (4m 57s)` ✓ |

Global：`cpu 2377 / screen 567 / mobile_radio 1009`，
`Computed drain 2768 = actual drain 2768`。

界面（修复后）：
**实测放电 1207.0 mAh · 272.7 mA · 4.40 %/h · 1.05 W**，
区间模型电量摊开显示 屏幕/基带/CPU 三大头。

---

## 方法论小结（下次直接照做）

1. **用户说"这数字一小时前还不存在"，就是发现了 bug。** 先查数据的产生路径，
   不要先解释数据为什么合理。
2. **解析括号/引号内的值必须带右边界。** 找到定界符却不用它 = 白找。
3. **`strchr` 会匹配自身**，手工跳列必须"找到即 +1 越过"。
4. **`argv[++i]` 不要写两次。**
5. **格式与数据同在一行时不能 `continue`。**
6. **绝不硬编码 OEM 的 UID/包名语义表。** 只留最确定的几项，其余运行时反查，
   查不到就诚实显示 —— 猜名字等于制造幽灵条目。
7. **归因优先取现成的模型值。** `dumpsys` 早就给了 per-UID mAh，
   我却用手工权重猜了三个版本。先读全字段，再决定是否需要自己算。
8. **给每个增量值加物理上界自检**，它能自动暴露上游的解析错误。
9. **后台线程与 UI 共用的缓存一律 ConcurrentHashMap。**
10. **缓冲区大小要按实测最长行 ×2 设定**，截断的半行会被当作新行重复解析。
