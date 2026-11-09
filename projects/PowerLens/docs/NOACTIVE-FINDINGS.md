# NoActive 系统应用冻结 — 排查结论

> 2026-11-09 14:45　2410DPN6CC / haotian / Android 16 / KernelSU
> 结论：**NoActive 一直在正常工作，交接文档的判断有误**

---

## 一、推翻旧结论

**交接文档说**：「dex 有『已配置冻结系统应用』→ 设计上支持，UI 不开放；但**未能稳定复现冻结**」

**实测证据表明这是错的。** LSPosed 日志证明 NoActive 完整工作：

```
[2026-11-09T11:41:05.360] NoActive-Log [信息] NoActive运行环境正常
[2026-11-09T11:41:05.373] NoActive-Log [信息] 当前版本 317
[2026-11-09T11:41:05.373] NoActive-Log [信息] 安卓版本 16
[2026-11-09T11:41:05.376] NoActive-Log [信息] 冻结方式 V2 支持
[2026-11-09T11:41:05.377] NoActive-Log [信息] 开始Hook 系统框架
    ...（60+ 处 hook 全部成功）
[2026-11-09T11:41:05.417] NoActive-Log [信息] 系统框架 Hook完毕
[2026-11-09T11:41:09.326] NoActive-Log [信息] 系统启动完成
[2026-11-09T11:41:09.641] NoActive-Log [信息] 开始Hook 系统界面
[2026-11-09T11:41:09.652] NoActive-Log [信息] 系统界面 Hook结束
[2026-11-09T11:41:16.349] NoActive-Log [信息] 下载管理程序 Hook结束

运行期日志：
[2026-11-09T11:41:46.342] ReKernel通知Binder(SYNC) 临时解冻 com.tencent.mobileqq
[2026-11-09T11:44:04.420] ReKernel通知Binder(SYNC) 临时解冻 com.tencent.mobileqq
[2026-11-09T12:15:17.551] 广播冷启动 临时解冻 com.jingdong.app.mall
[2026-11-09T11:53:52.510] 深度睡眠成功
[2026-11-09T11:55:00.817] 退出深度睡眠
[2026-11-09T12:35:26.968] 深度睡眠成功
[2026-11-09T12:59:51.951] 退出深度睡眠
[2026-11-09T13:10:24.883] 深度睡眠成功
[2026-11-09T14:27:19.337] 深度睡眠成功
[2026-11-09T14:30:05.876] 退出深度睡眠
```

**关键证据**：
- 「ReKernel通知Binder(SYNC) **临时解冻** com.tencent.mobileqq」——
  能"解冻"就说明 QQ 之前**是被冻结的**
- 「深度睡眠成功 / 退出深度睡眠」反复出现——这是 NoActive 的冻结生命周期

---

## 二、真正的问题：`userAppSet` 是空的

```
MasterConfig.json（原始状态）
{
	"backgroundOptimize":false,
	"commandInject":false,
	"dozeType":"locked",
	"freezeBinder":false,
	"memoryOptimize":false,
	"netLink":0,
	"rotationInterval":360,
	"rotationThaw":false,
	"skipXposed":true,
	"startLimit":false,
	"systemAppSet":[
		
	],
	"userAppSet":[
		
	]        ← 空！NoActive 没有用户指定的冻结对象
}
```

`systemAppSet` 和 `userAppSet` 都空，所以 NoActive 只冻结它**自动管理**的应用
（系统 CachedAppOptimizer 队列里的），用户没法主动扩展。

---

## 三、已完成的修改

往 `userAppSet` 加了一个测试包 `com.coolapk.market`：

```
"userAppSet":[
	"com.coolapk.market"
]
```

**权限正确**：
```
-rw------- 1 system system u:object_r:system_data_file:s0 296 14:43 MasterConfig.json
```

**备份**：`MasterConfig.json.bak`（root:root，原始空名单）

---

## 四、待生效

NoActive 监听配置文件变化，但实测**修改后没有立即 reload**（日志停在 14:30）。

生效方式（按可靠性排序）：
1. **重启手机**（最彻底）
2. 打开 NoActive App，让它自己 reload 配置
3. `kill system_server`（软重启，风险高，不推荐）

---

## 五、两套冻结机制的关系（重要）

这台机器上**同时有两套冻结**，别混淆：

| 机制 | 触发者 | 配置位置 | 冻结对象 |
|---|---|---|---|
| **系统 CachedAppOptimizer** | Android 框架 | `freezer_cutoff_adj=900` | adj ≥ 900 的 cached 进程 |
| **NoActive** | LSPosed hook | `MasterConfig.json` 的 userAppSet | 名单内的应用 |
| **freezer.sh**（自研） | cron 脚本 | `/data/local/tmp/freezer.conf` | 名单内（当前仅 gallery） |

当前实测有 **14 个进程被冻结**，无法直接区分来自哪套（都需要看 cgroup 归属）。

`freezer.sh` 是交接文档里"替代方案"，实测可用（日志有 freeze/thaw 记录），
但名单只有 `com.miui.gallery` 一个。

---

## 六、工具

```
/data/local/tmp/
├─ noactive-check.sh     粗查（会被 device_config 刷屏，不推荐）
├─ na-q.sh               精确查询
├─ noactive-real.sh      查 NoActive 是否工作
├─ noactive-final.sh     完整验证 + 结论
├─ noactive-add.sh       追加名单（awk 方式，有 bug）
└─ noactive-write.sh     重写配置（脚本内 cp 会丢 root 权限，有 bug）

PC 侧：D:\CODEX\w\scripts\phone\02-diagnose\ 同名文件
```

**已知问题**：所有手机端脚本在 `su -c "sh script"` 下，
内部命令（cp/chown/awk/ls）会丢失 root 权限。
**可靠做法**：PC 侧生成文件 → `adb push` → 单条 `su -c "cp ..."`。

---

## 七、下一步

### 要真正启用 NoActive 用户名单

1. 用 PC 侧生成 JSON（已验证可靠）：
   ```
   D:\CODEX\tmp\MasterConfig.json
   ```
2. `adb push` 到 `/data/local/tmp/mc.json`
3. `su -c "cp -f /data/local/tmp/mc.json /data/system/NoActive_fJIecxAm/config/MasterConfig.json"`
4. `su -c "chown system:system ..."` 和 `chmod 600`
5. **重启手机**让它生效

### 推荐名单（按内存占用 + 后台常驻 + 无即时通讯需求）

```
com.coolapk.market
com.baidu.BaiduMap
tv.danmaku.bili
com.icbc
com.novel.jingxiao
com.xingin.xhs
com.taobao.idlefish
com.jingdong.app.mall
com.xunmeng.pinduoduo
com.sankuai.meituan
com.dianping.v1
com.taobao.taobao
com.eg.android.AlipayGphone
com.miui.gallery
com.ss.android.ugc.aweme
```

**不要加**：微信、QQ（通讯类，冻结会延迟消息）；
systemui / launcher / 电话 / settings（系统关键）。

### 需要验证的

`userAppSet` 加进去之后，冻结是否真的发生在 NoActive 侧还是被系统
CachedAppOptimizer 抢先了。验证方法：
- 冻结后看 `dumpsys activity processes | grep -A2 coolapk` 的 `curAdj`
- 如果 curAdj < 900 却被冻结 → 是 NoActive 干的
- 如果 curAdj ≥ 900 → 是系统干的