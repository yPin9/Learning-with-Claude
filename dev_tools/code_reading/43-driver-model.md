# Ch 43 — driver model：bus、device、driver 與 probe

> **目標**：讀懂 driver core 怎麼把 device 和 driver 配對、`probe` 在什麼時候被誰呼叫；能把 `module_platform_driver(...)` 展開成真正的程式碼；拿到任何一個陌生 driver，能用固定的四步順序（driver struct → probe → 它註冊了什麼介面 → 中斷／I/O 路徑）在十分鐘內畫出它的骨架。

> **鴻溝**：本章主要練**間接鴻溝**（probe 是被 driver core 間接呼叫的，`rg` 找不到直接呼叫者）與**慣用法鴻溝**（`module_platform_driver`、`devm_*`、`of_match_ptr` 這類巨集）。

> **環境**：Linux kernel v6.18 原始碼；巨集展開用本課 `~/code_reading/build`（x86_64，`CONFIG_OF` 未開、`CONFIG_ACPI=y`）的編譯參數跑 `gcc -E`；sysfs 觀察在 WSL2 的 kernel `6.18.58.1-microsoft-standard-WSL2` 上進行。

## 為什麼需要這個？

`drivers/` 是 kernel 原始碼裡最大的目錄，你遲早會讀到 driver。而第一次讀 driver 的人幾乎都卡在同一個地方：**找不到程式從哪裡開始跑**。driver 沒有 `main`，`probe` 函式在整個 tree 裡沒有任何直接呼叫者，`module_platform_driver(foo_driver);` 這一行看起來什麼都沒做，然後 driver 就「自己動起來了」。

這是典型的間接鴻溝：控制流被 driver core 接管了。Spinellis 在原書 Ch 9.2「Control Models」把這類架構歸為**事件驅動**與**系統管理者**（system manager）的組合：元件不主動執行，而是向一個管理者登記，由管理者在適當時機回呼它。driver model 就是 kernel 裡規模最大的一個這種管理者。

在 driver model 出現之前（2.4 時代），PCI、USB、各種 bus 各自維護自己的 device 串列與 driver 串列，各自寫配對邏輯，電源管理要每個 bus 自己處理關機順序，sysfs 也不存在。2.5 開發週期中，由 Patrick Mochel 主導把這些共通部分抽成統一的 driver core（`drivers/base/`），隨 2.6 發布。它統一了三件事：**配對**（哪個 driver 管哪個 device）、**生命週期**（probe/remove、電源管理順序）、**呈現**（sysfs）。

讀懂 driver core 的回報很高：PCI driver、USB driver、I2C driver、platform driver 的骨架完全一樣，差別只在 bus 層的 match 規則和 probe 的參數型別。學會一種，其他都能套。

## 先建立直覺

把 driver core 想成一家婚友社：

```
                 ┌──────────────── bus_type（婚友社分店）────────────────┐
                 │  .match()  比對條件：先比 DT compatible，再比 ACPI ID， │
                 │            再比 id_table，最後比名字                   │
                 │  .probe()  安排第一次見面                              │
                 └───────────────────────────────────────────────────────┘
        device 名單                                       driver 名單
   ┌──────────────────┐                            ┌──────────────────┐
   │ MSFT1002:00      │  ◄── match? ── probe ──►    │ vmgenid          │
   │ (ACPI 列舉出來的) │                            │ (模組或 built-in) │
   ├──────────────────┤                            ├──────────────────┤
   │ serial8250       │                            │ serial8250       │
   └──────────────────┘                            └──────────────────┘
     來源：firmware（DT、ACPI）、                     來源：module_init /
     bus 列舉（PCI、USB）、程式碼手動建立              initcall 時註冊
```

兩份名單可以以任何順序變長。**任何一邊加入新成員時，婚友社就拿他去和另一份名單的每個人比對**。比對成功就呼叫 driver 的 `probe`；`probe` 回傳 0 代表「配對成功，以後這個 device 歸我管」。

這個模型有一個重要推論：**probe 有兩個觸發點**——新 driver 註冊時（掃描既有 device），以及新 device 加入時（掃描既有 driver）。讀 driver 時你永遠不需要關心是哪一個，因為 `probe` 的參數和語意都一樣。

## 三個角色：bus、device、driver

三個核心 struct 都在 `include/linux/device/` 底下（`bus.h`、`driver.h`）與 `include/linux/device.h`。我們用 platform bus 當具體例子，因為它是最簡單的 bus：沒有可列舉的硬體協定，device 來自 Device Tree、ACPI 或程式碼。

```c
/* drivers/base/platform.c:1475（v6.18） */
const struct bus_type platform_bus_type = {
	.name		= "platform",
	.dev_groups	= platform_dev_groups,
	.match		= platform_match,
	.uevent		= platform_uevent,
	.probe		= platform_probe,
	.remove		= platform_remove,
	.shutdown	= platform_shutdown,
	.dma_configure	= platform_dma_configure,
	.dma_cleanup	= platform_dma_cleanup,
	.pm		= &platform_dev_pm_ops,
};
```

`bus_type` 本身就是一張 ops table（Ch 42）：`match` 決定配對規則，`probe`／`remove` 是 bus 層的包裝，它們會再呼叫具體 driver 的版本。

platform bus 的 driver 是一個「衍生類別」：

```c
/* include/linux/platform_device.h:236（v6.18，節錄） */
struct platform_driver {
	int (*probe)(struct platform_device *);
	void (*remove)(struct platform_device *);
	void (*shutdown)(struct platform_device *);
	int (*suspend)(struct platform_device *, pm_message_t state);
	int (*resume)(struct platform_device *);
	struct device_driver driver;
	const struct platform_device_id *id_table;
	bool prevent_deferred_probe;
	...
```

`struct device_driver driver;` 是嵌入的基底類別。driver core 只認識 `device_driver`，platform bus 用 `to_platform_driver()`（`container_of`）轉回來。device 那一側同理：`struct platform_device` 嵌入 `struct device dev`，用 `to_platform_device()` 互轉。PCI 是 `struct pci_driver` 嵌 `device_driver`、`struct pci_dev` 嵌 `device`，I2C、USB 都一樣。**認出這個對稱結構，你就認得所有 bus 的 driver。**

## 配對：`platform_match` 的四道比對

```c
/* drivers/base/platform.c:1335（v6.18） */
static int platform_match(struct device *dev, const struct device_driver *drv)
{
	struct platform_device *pdev = to_platform_device(dev);
	struct platform_driver *pdrv = to_platform_driver(drv);

	/* When driver_override is set, only bind to the matching driver */
	if (pdev->driver_override)
		return !strcmp(pdev->driver_override, drv->name);

	/* Attempt an OF style match first */
	if (of_driver_match_device(dev, drv))
		return 1;

	/* Then try ACPI style match */
	if (acpi_driver_match_device(dev, drv))
		return 1;

	/* Then try to match against the id table */
	if (pdrv->id_table)
		return platform_match_id(pdrv->id_table, pdev) != NULL;

	/* fall-back to driver name match */
	return (strcmp(pdev->name, drv->name) == 0);
}
```

這段的順序就是優先順序：

| 順序 | 依據 | driver 端提供 | device 端提供 |
|---|---|---|---|
| 0 | `driver_override` | （由使用者經 sysfs 寫入） | `/sys/bus/platform/devices/*/driver_override` |
| 1 | Device Tree | `driver.of_match_table`（`compatible` 字串陣列） | DT 節點的 `compatible` 屬性 |
| 2 | ACPI | `driver.acpi_match_table`（ACPI HID/CID） | ACPI namespace 裡的裝置 `_HID`／`_CID` |
| 3 | id table | `platform_driver.id_table` | `pdev->name` |
| 4 | 名字 | `driver.name` | `pdev->name` |

ARM／RISC-V 開發板通常走第 1 道（DT），x86 筆電與 VM 通常走第 2 道（ACPI），程式碼手動建立的 device（例如 `platform_device_register_simple("serial8250", ...)`）走第 4 道。

## probe 的兩個觸發點

把 Ch 28「找賦值點」的方法套在 `probe` 上：`rg 'hisi_rng_probe'` 只找到定義和 `.probe = hisi_rng_probe`。下一步是找「誰呼叫 `drv->probe`」，一路往上追，會得到這張圖：

```
 觸發點 A：driver 註冊                       觸發點 B：device 加入
 module_init → __platform_driver_register    device_add               drivers/base/core.c
   drivers/base/platform.c:861                 └ bus_probe_device(dev) core.c:3689
   └ driver_register      driver.c:225            bus.c:528
      └ bus_add_driver    bus.c:647                └ device_initial_probe   dd.c:1078
         └ driver_attach  bus.c:678 → dd.c:1232       └ __device_attach      dd.c:1001
            └ bus_for_each_dev(..., __driver_attach)     └ bus_for_each_drv(..., __device_attach_driver)
               └ __driver_attach     dd.c:1157              dd.c:922
                     │                                        │
                     └────────── driver_match_device ─────────┘   base.h:166
                                   → drv->bus->match(dev, drv)  = platform_match
                                         │ 配對成功
                                         ▼
                                 driver_probe_device      dd.c:825
                                  └ __driver_probe_device dd.c:780
                                     └ really_probe       dd.c:605
                                        └ call_driver_probe  dd.c:574
                                           └ dev->bus->probe(dev)  = platform_probe  platform.c:1379
                                              └ drv->probe(pdev)   = hisi_rng_probe
```

兩條路在 `driver_match_device` 匯合：

```c
/* drivers/base/base.h:166（v6.18） */
static inline int driver_match_device(const struct device_driver *drv,
				      struct device *dev)
{
	return drv->bus->match ? drv->bus->match(dev, drv) : 1;
}
```

`call_driver_probe` 決定 probe 的回傳值怎麼被解讀，這段是除錯 driver 時最值得記住的：

```c
/* drivers/base/dd.c:574（v6.18） */
static int call_driver_probe(struct device *dev, const struct device_driver *drv)
{
	int ret = 0;

	if (dev->bus->probe)
		ret = dev->bus->probe(dev);
	else if (drv->probe)
		ret = drv->probe(dev);

	switch (ret) {
	case 0:
		break;
	case -EPROBE_DEFER:
		/* Driver requested deferred probing */
		dev_dbg(dev, "Driver %s requests probe deferral\n", drv->name);
		break;
	case -ENODEV:
	case -ENXIO:
		dev_dbg(dev, "probe with driver %s rejects match %d\n",
			drv->name, ret);
		break;
	default:
		/* driver matched but the probe failed */
		dev_err(dev, "probe with driver %s failed with error %d\n",
			drv->name, ret);
		break;
	}

	return ret;
}
```

三種失敗被區別對待：`-EPROBE_DEFER` 是「我依賴的東西（時脈、regulator、GPIO）還沒準備好，晚點再叫我」；`-ENODEV`／`-ENXIO` 是「match 對了但這不是我的硬體」，安靜地拒絕；其他錯誤才印 `dev_err`。所以 `dmesg` 裡看不到某個 driver 的錯誤，**不代表它沒有失敗**——可能是被 defer 了或回了 `-ENODEV`，要開 dynamic debug 才看得到。

被 defer 的 device 什麼時候重試？`driver_bound()`（`dd.c:399`）在任何 device 綁定成功時呼叫 `driver_deferred_probe_trigger()`（`dd.c:419`），把 deferred 清單裡的 device 全部再試一次。理由是：某個 device 綁定成功，可能就是別人等待的那個資源。

### 用 user-space 模型驗證觸發時機

把上面的規則縮成一個 100 行的模型，觀察兩個觸發點與 deferred probe 的互動：

```c
/* minibus.c — driver core 的 match/probe 觸發時機，縮成 user-space 模型 */
#include <stdio.h>
#include <string.h>

#define EPROBE_DEFER 517
#define MAXN 8

struct driver;
struct device { const char *name; const char *compatible; struct driver *drv; };
struct driver { const char *name; const char *compatible; int (*probe)(struct device *); };

static struct device *devices[MAXN]; static int ndev;
static struct driver *drivers[MAXN]; static int ndrv;
static struct device *deferred[MAXN]; static int ndefer;
static int bound_happened;   /* 對應 kernel 的 driver_deferred_probe_trigger() */

/* bus->match：先比 compatible（像 of_match_table），再退回比名字 */
static int bus_match(struct device *dev, struct driver *drv)
{
    if (dev->compatible && drv->compatible)
        return strcmp(dev->compatible, drv->compatible) == 0;
    return strcmp(dev->name, drv->name) == 0;
}

static void try_bind(struct device *dev, struct driver *drv)
{
    if (dev->drv || !bus_match(dev, drv))
        return;
    printf("  match: %s <-> %s, calling probe\n", dev->name, drv->name);
    int ret = drv->probe(dev);
    if (ret == 0) {
        dev->drv = drv;
        bound_happened = 1;
        printf("  bound: %s\n", dev->name);
    } else if (ret == -EPROBE_DEFER) {
        printf("  %s: probe deferred\n", dev->name);
        deferred[ndefer++] = dev;
    }
}

static void retry_deferred(void)          /* 只有「某處綁定成功」才重試 */
{
    while (bound_happened && ndefer) {
        struct device *list[MAXN];
        int n = ndefer;

        bound_happened = 0;
        printf("  (a device got bound -> retry deferred list)\n");
        memcpy(list, deferred, sizeof(list));
        ndefer = 0;
        for (int i = 0; i < n; i++)
            for (int j = 0; j < ndrv; j++)
                try_bind(list[i], drivers[j]);
    }
    bound_happened = 0;
}

static void device_add(struct device *dev)      /* 觸發點一：新裝置出現 */
{
    printf("device_add(%s)\n", dev->name);
    devices[ndev++] = dev;
    for (int j = 0; j < ndrv; j++)
        try_bind(dev, drivers[j]);
    retry_deferred();
}

static void driver_register(struct driver *drv) /* 觸發點二：新 driver 出現 */
{
    printf("driver_register(%s)\n", drv->name);
    drivers[ndrv++] = drv;
    for (int i = 0; i < ndev; i++)
        try_bind(devices[i], drv);
    retry_deferred();
}

static int clk_ready;
static int clk_probe(struct device *d) { (void)d; clk_ready = 1; return 0; }
static int rng_probe(struct device *d) { (void)d; return clk_ready ? 0 : -EPROBE_DEFER; }

int main(void)
{
    struct device rng = { "rng0", "acme,rng", NULL };
    struct device clk = { "clk0", "acme,clk", NULL };
    struct driver rng_drv = { "acme-rng", "acme,rng", rng_probe };
    struct driver clk_drv = { "acme-clk", "acme,clk", clk_probe };

    driver_register(&rng_drv);   /* driver 先到，還沒有裝置：什麼都不發生 */
    device_add(&rng);            /* 裝置到了，match 成功，但時脈還沒好 → defer */
    device_add(&clk);            /* 時脈裝置到了，但它的 driver 還沒來 */
    driver_register(&clk_drv);   /* 時脈 driver 到了 → clk 綁定 → 重試 rng */
    return 0;
}
```

```
$ gcc -Wall -Wextra -O2 -o minibus minibus.c && ./minibus
driver_register(acme-rng)
device_add(rng0)
  match: rng0 <-> acme-rng, calling probe
  rng0: probe deferred
device_add(clk0)
driver_register(acme-clk)
  match: clk0 <-> acme-clk, calling probe
  bound: clk0
  (a device got bound -> retry deferred list)
  match: rng0 <-> acme-rng, calling probe
  bound: rng0
```

四個事件以「最不利」的順序發生，最後兩個 device 都綁定成功，而且沒有任何一方需要知道對方的存在。這就是為什麼 driver 作者**不能**假設 probe 順序：開機時的 probe 順序取決於 initcall 順序、firmware 列舉順序、模組載入時機，driver 唯一能依賴的同步機制是 `-EPROBE_DEFER`。

（這個模型省略了 kernel 的鎖、async probe、device links 與 deferred probe 的 workqueue；它只示範觸發規則。）

## 展開 `module_platform_driver`

接下來讀一個真實的 driver。v6.18 的 `drivers/char/hw_random/hisi-rng.c` 全檔 117 行，是理想的教材。最後幾行：

```c
/* drivers/char/hw_random/hisi-rng.c:98（v6.18） */
static const struct of_device_id hisi_rng_dt_ids[] __maybe_unused = {
	{ .compatible = "hisilicon,hip04-rng" },
	{ .compatible = "hisilicon,hip05-rng" },
	{ }
};
MODULE_DEVICE_TABLE(of, hisi_rng_dt_ids);

static struct platform_driver hisi_rng_driver = {
	.probe		= hisi_rng_probe,
	.driver		= {
		.name	= "hisi-rng",
		.of_match_table = of_match_ptr(hisi_rng_dt_ids),
	},
};

module_platform_driver(hisi_rng_driver);
```

`module_platform_driver` 的定義是兩層巨集：

```c
/* include/linux/platform_device.h:291（v6.18） */
#define module_platform_driver(__platform_driver) \
	module_driver(__platform_driver, platform_driver_register, \
			platform_driver_unregister)

/* include/linux/device/driver.h:257（v6.18） */
#define module_driver(__driver, __register, __unregister, ...) \
static int __init __driver##_init(void) \
{ \
	return __register(&(__driver) , ##__VA_ARGS__); \
} \
module_init(__driver##_init); \
static void __exit __driver##_exit(void) \
{ \
	__unregister(&(__driver) , ##__VA_ARGS__); \
} \
module_exit(__driver##_exit);
```

與其在腦中展開，不如讓編譯器展開（Ch 13、Ch 50 的方法）。本課 build 裡 `drivers/char/hw_random/core.o` 已經編好，它的 `.core.o.cmd` 記錄了完整的 gcc 命令列；把 `-c -o ...` 換成 `-E -P`、把模組名稱相關的 `-D` 改成 hisi_rng，就能預處理 hisi-rng.c（它在 x86 config 裡不會被編譯，但預處理不受 config 影響）：

```bash
cd ~/code_reading/build
CMD=$(sed -n '1s/^savedcmd_[^=]*:= //p' drivers/char/hw_random/.core.o.cmd \
  | sed -e 's/-Wp,-MMD,[^ ]* //' -e 's/ -c -o .*$//' \
        -e 's/rng-core/hisi-rng/; s/"core"/"hisi_rng"/; s/"rng_core"/"hisi_rng"/; s/kmod_rng_core/kmod_hisi_rng/')
eval "$CMD -E -P ~/code_reading/linux/drivers/char/hw_random/hisi-rng.c" > hisi-builtin.i
eval "$CMD -DMODULE -E -P ~/code_reading/linux/drivers/char/hw_random/hisi-rng.c" > hisi-module.i
```

兩個檔案的尾端（排版過，內容未改）分別是：

```c
/* 編成模組（-DMODULE） */
static const struct of_device_id hisi_rng_dt_ids[] __attribute__((__unused__)) = { ... };
static typeof(hisi_rng_dt_ids) __mod_device_table__kmod_hisi_rng__of__hisi_rng_dt_ids
        __attribute__ ((used, alias("hisi_rng_dt_ids")));
static struct platform_driver hisi_rng_driver = {
 .probe = hisi_rng_probe,
 .driver = {
  .name = "hisi-rng",
  .of_match_table = ((void *)0),
 },
};
static int __attribute__((__section__(".init.text"))) hisi_rng_driver_init(void)
{ return __platform_driver_register(&(hisi_rng_driver), (&__this_module)); }
...
int init_module(void) __attribute__((__copy__(hisi_rng_driver_init))) __attribute__((alias("hisi_rng_driver_init")));
...
void cleanup_module(void) __attribute__((__copy__(hisi_rng_driver_exit))) __attribute__((alias("hisi_rng_driver_exit")));
```

```c
/* 編進 kernel（built-in） */
static int __attribute__((__section__(".init.text"))) hisi_rng_driver_init(void)
{ return __platform_driver_register(&(hisi_rng_driver), ((struct module *)0)); }
... asm(".section \".initcall6.init\", \"a\" ... "
        "__initcall__kmod_hisi_rng__425_113_hisi_rng_driver_init6" ":" ...
        ".long hisi_rng_driver_init - . ...");
```

從展開結果可以讀出五件原始碼上看不到的事：

1. **一行巨集生成兩個函式**：`hisi_rng_driver_init` 與 `hisi_rng_driver_exit`，名字是 `__driver##_init` 拼出來的。在 `rg` 裡找 `hisi_rng_driver_init` 會找不到定義，這是 Ch 14 講過的 token pasting 陷阱。
2. **模組與 built-in 的入口不同**：模組版把 init 函式取別名成 `init_module`，由 `insmod` 時的 `do_init_module` 呼叫；built-in 版把函式位址放進 `.initcall6.init` section（6 是 `device_initcall` 等級），開機時 `do_initcalls` 依等級順序呼叫。driver 程式碼完全不用管這個差異。
3. **`THIS_MODULE` 在 built-in 時是 `NULL`**：`(struct module *)0` vs `&__this_module`。
4. **`.of_match_table` 變成了 `NULL`**：因為本課 x86 config 沒開 `CONFIG_OF`，`of_match_ptr()` 被定義成回傳 `NULL`，陣列本身被標成 `__maybe_unused` 避免警告。在這個 config 下，這個 driver **只能靠名字配對**。這是讀 driver 時最容易被騙的地方之一：原始碼上明明有 match table，實際上依 config 可能不存在。
5. **`MODULE_DEVICE_TABLE` 生成一個別名符號** `__mod_device_table__kmod_hisi_rng__of__hisi_rng_dt_ids`。它不參與執行，是給建構工具讀的：`modpost` 從這個符號產生 `MODULE_ALIAS("of:N*T*Chisilicon,hip04-rng*")` 之類的字串寫進模組，`depmod` 再彙整成 `modules.alias`。當 kernel 發現一個 device 卻沒有 driver 時，它發出帶 `MODALIAS=` 的 uevent，udev 用 `modprobe` 查 `modules.alias` 載入對應模組。**這就是「插上裝置、driver 自動載入」的完整機制。**

## 讀 driver 的固定順序

不管什麼 driver，照這四步讀：

```
① 找 driver struct     最後幾行的 xxx_driver 與 module_*_driver
   │                    → 它掛在哪個 bus？靠什麼配對（of / acpi / id_table / name）？
② 讀 probe             配置了什麼（devm_*）、拿了什麼資源（MMIO、IRQ、clock）、
   │                    把狀態存在哪（private struct、dev_set_drvdata）
③ 它註冊了什麼介面      probe 最後通常有一個 xxx_register()：
   │                    這決定了「誰會來呼叫這個 driver」（/dev 節點？netdev？hwrng？）
④ 中斷與 I/O 路徑       request_irq 的 handler、子系統回呼的 ops（read/xmit/...）
                         裡面的 readl/writel 就是真正碰硬體的地方
```

### 套用在 hisi-rng 上

**① driver struct**：platform bus；配對靠 DT 的 `"hisilicon,hip04-rng"`／`"hisilicon,hip05-rng"`（`CONFIG_OF` 關掉時退回名字 `"hisi-rng"`）。沒有 `.remove`——等一下會看到為什麼不需要。

**② probe**：

```c
/* drivers/char/hw_random/hisi-rng.c:73（v6.18） */
static int hisi_rng_probe(struct platform_device *pdev)
{
	struct hisi_rng *rng;
	int ret;

	rng = devm_kzalloc(&pdev->dev, sizeof(*rng), GFP_KERNEL);
	if (!rng)
		return -ENOMEM;

	rng->base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(rng->base))
		return PTR_ERR(rng->base);

	rng->rng.name = pdev->name;
	rng->rng.init = hisi_rng_init;
	rng->rng.cleanup = hisi_rng_cleanup;
	rng->rng.read = hisi_rng_read;

	ret = devm_hwrng_register(&pdev->dev, &rng->rng);
	if (ret)
		return dev_err_probe(&pdev->dev, ret, "failed to register hwrng\n");

	return 0;
}
```

三個 `devm_` 呼叫依序是：配置私有狀態（`struct hisi_rng`，第 29 行定義，嵌入了 `struct hwrng rng`）、把 DT 給的第 0 個 MMIO 區域 map 進來、向 hwrng 子系統註冊。每一個 `devm_` 資源都綁在 `&pdev->dev` 上，**當 probe 失敗或 device 解除綁定時自動依反向順序釋放**。這就是為什麼這個 driver 不需要 `.remove`，也不需要 Ch 17 那種 goto 錯誤階梯。

`devm_hwrng_register` 的實作揭露了 devm 的原理：

```c
/* drivers/char/hw_random/core.c:614（v6.18） */
int devm_hwrng_register(struct device *dev, struct hwrng *rng)
{
	struct hwrng **ptr;
	int error;

	ptr = devres_alloc(devm_hwrng_release, sizeof(*ptr), GFP_KERNEL);
	if (!ptr)
		return -ENOMEM;

	error = hwrng_register(rng);
	if (error) {
		devres_free(ptr);
		return error;
	}

	*ptr = rng;
	devres_add(dev, ptr);
	return 0;
}
```

`devres_alloc` 配置一個小節點，記下「釋放時要呼叫 `devm_hwrng_release`」；`devres_add` 把它掛到 device 的 devres 串列上。解除綁定時，driver core 的 `device_unbind_cleanup`（`drivers/base/dd.c:548`）第一行就是 `devres_release_all(dev)`，倒著走這個串列逐一呼叫 release。**devm 就是掛在 device 物件上的 RAII**（Ch 45）。

**③ 註冊的介面**：`devm_hwrng_register` → hwrng 子系統。往 `drivers/char/hw_random/core.c` 看，hwrng core 註冊了一個 misc device：

```c
/* drivers/char/hw_random/core.c:287（v6.18） */
static const struct file_operations rng_chrdev_ops = {
	.owner		= THIS_MODULE,
	.open		= rng_dev_open,
	.read		= rng_dev_read,
	.llseek		= noop_llseek,
};
/* core.c:296 */
static struct miscdevice rng_miscdev = {
	.minor		= HWRNG_MINOR,
	.name		= RNG_MODULE_NAME,
	.nodename	= "hwrng",
	.fops		= &rng_chrdev_ops,
	...
```

所以 user space 看到的是 `/dev/hwrng`。WSL 上也有這個節點（`crw------- 1 root root 10, 183 ... /dev/hwrng`），10 是 misc 的 major，183 是 `HWRNG_MINOR`；但 `cat /sys/class/misc/hw_random/rng_available` 回 `none`——節點由 hwrng core 建立，WSL 的 VM 裡沒有任何 hwrng driver 綁定。

**④ I/O 路徑**：`read(/dev/hwrng)` → `rng_dev_read` → `rng_get_data`（`core.c:179`）→ `rng->read(rng, buffer, size, wait)` → `hisi_rng_read`：

```c
/* drivers/char/hw_random/hisi-rng.c:64（v6.18） */
static int hisi_rng_read(struct hwrng *rng, void *buf, size_t max, bool wait)
{
	struct hisi_rng *hrng = to_hisi_rng(rng);
	u32 *data = buf;

	*data = readl_relaxed(hrng->base + RNG_RAN_NUM);
	return 4;
}
```

`to_hisi_rng` 是第 23 行定義的 `container_of`——從子系統給的 `struct hwrng *` 找回 driver 的私有 struct（Ch 42 的向下轉型）。`readl_relaxed(base + 0x10)` 是唯一真正碰硬體的一行。這個 driver 沒有中斷，資料是同步輪詢讀出的。

四步讀完，整個 driver 的骨架可以寫成一行：**「DT 配對 → map MMIO → 把三個 callback 交給 hwrng core → hwrng core 在 user 讀 `/dev/hwrng` 時回呼 `read`，它讀一個暫存器。」**

## 第二個例子：正在你電腦上運作的 vmgenid

hisi-rng 的硬體你沒有。下面這個 driver 此刻就綁定在 WSL 的 VM 上。

```
$ for d in /sys/bus/platform/devices/*; do
>   echo "$d -> $(readlink $d/driver 2>/dev/null) modalias=$(cat $d/modalias 2>/dev/null)"; done
/sys/bus/platform/devices/MSFT1000:00 -> ../../../../../bus/platform/drivers/vmbus modalias=acpi:MSFT1000:VMBUS:
/sys/bus/platform/devices/MSFT1002:00 -> ../../../bus/platform/drivers/vmgenid modalias=acpi:MSFT1002:VM_GEN_COUNTER:VMGENCTR:HYPER_V_GEN_COUNTER_V1:
/sys/bus/platform/devices/PNP0003:00 ->  modalias=acpi:PNP0003:
/sys/bus/platform/devices/PNP0B00:00 -> ../../../bus/platform/drivers/rtc_cmos modalias=acpi:PNP0B00:
/sys/bus/platform/devices/alarmtimer.0.auto -> ../../../../../../bus/platform/drivers/alarmtimer modalias=platform:alarmtimer
/sys/bus/platform/devices/serial8250 -> ../../../bus/platform/drivers/serial8250 modalias=platform:serial8250
$ cat /sys/bus/platform/devices/MSFT1002:00/firmware_node/path
\_SB_.GENC
```

`MSFT1002:00` 是 Hyper-V 在 ACPI namespace 的 `\_SB_.GENC` 提供的「VM Generation ID」裝置，modalias 列出它的 HID（`MSFT1002`）與幾個 CID。`PNP0003:00` 沒有 driver 綁定——match 沒有成功，device 就一直在名單上等。`serial8250` 的 modalias 是 `platform:` 前綴，代表它是程式碼建立、靠名字配對的。

vmgenid 的 driver 在 `drivers/virt/vmgenid.c`（181 行）。照四步讀：

**① driver struct**：

```c
/* drivers/virt/vmgenid.c:155（v6.18） */
static const struct of_device_id vmgenid_of_ids[] = {
	{ .compatible = "microsoft,vmgenid", },
	{ },
};
MODULE_DEVICE_TABLE(of, vmgenid_of_ids);

static const struct acpi_device_id vmgenid_acpi_ids[] = {
	{ "VMGENCTR", 0 },
	{ "VM_GEN_COUNTER", 0 },
	{ }
};
MODULE_DEVICE_TABLE(acpi, vmgenid_acpi_ids);

static struct platform_driver vmgenid_plaform_driver = {
	.probe      = vmgenid_add,
	.driver     = {
		.name   = "vmgenid",
		.acpi_match_table = vmgenid_acpi_ids,
		.of_match_table = vmgenid_of_ids,
	},
};

module_platform_driver(vmgenid_plaform_driver)
```

同時有 DT 和 ACPI 兩張表。WSL 上的配對走的是 `platform_match` 第 2 道：裝置的 CID 列表裡有 `VM_GEN_COUNTER`，和 `vmgenid_acpi_ids` 的第二筆相同。（`vmgenid_plaform_driver` 的拼字錯誤是原始碼就有的，不是本書筆誤。）

**② probe**（`vmgenid_add`，第 135 行）依 `dev->of_node` 分兩條路：有 DT 節點就走 `vmgenid_add_of`，否則走 `vmgenid_add_acpi`。這是同時支援兩種 firmware 的 driver 的標準寫法。

**③ 註冊的介面**：它不註冊任何 user-space 介面，它的「客戶」是 kernel 的亂數子系統（`add_vmfork_randomness`）。這提醒我們第 ③ 步的答案不一定是 `/dev` 節點。

**④ 中斷／事件路徑**，兩條路各一種：

```c
/* drivers/virt/vmgenid.c:85（ACPI 路徑） */
	status = acpi_install_notify_handler(device->handle, ACPI_DEVICE_NOTIFY,
					     vmgenid_acpi_handler, dev);

/* drivers/virt/vmgenid.c:126（DT 路徑） */
	ret = devm_request_irq(&pdev->dev, ret, vmgenid_of_irq_handler,
			       IRQF_SHARED, "vmgenid", &pdev->dev);
```

兩個 handler 最後都呼叫 `vmgenid_notify`（第 26 行）：比較新舊 generation ID，不同就代表 VM 被複製或從快照還原了，要把新 ID 混進亂數池，避免兩個 VM 產生相同的亂數序列。注意 ACPI 路徑的 `acpi_install_notify_handler` **沒有 devm 版本**，這個 driver 也沒有 `.remove`，handler 註冊後就不會被移除——這和 `module_platform_driver` 允許卸載模組的語意之間有張力。讀 driver 時，這種「資源註冊了卻找不到對應的釋放」是值得記下來、去 git log 考古（Ch 37）的線索。

## 對比與取捨

| 面向 | platform bus | PCI | USB |
|---|---|---|---|
| device 從哪來 | DT、ACPI、程式碼建立 | 掃描設定空間（硬體可列舉） | hub 回報插入事件 |
| match 依據 | compatible、ACPI ID、名字 | vendor/device ID（`pci_device_id`） | VID/PID、class（`usb_device_id`） |
| probe 參數 | `struct platform_device *` | `struct pci_dev *, const struct pci_device_id *` | `struct usb_interface *, const struct usb_device_id *` |
| 資源取得 | `platform_get_resource`、`platform_get_irq` | BAR（`pci_iomap`）、MSI | URB |
| 註冊巨集 | `module_platform_driver` | `module_pci_driver` | `module_usb_driver` |

| 資源管理方式 | 優點 | 缺點 |
|---|---|---|
| 手動（probe 裡 goto 階梯 + `.remove`） | 釋放時機完全可控 | 錯誤路徑容易漏，`.remove` 與 probe 要手動保持對稱 |
| `devm_*` | probe 失敗與解除綁定都自動釋放，程式碼短 | 釋放時機綁在「device 解除綁定」，不是「最後一個 user 關閉檔案」；混用不當會產生 use-after-free |

## 踩雷集錦

1. **以為 probe 只在開機時呼叫一次**：probe 在每次配對成功時呼叫。`echo MSFT1002:00 > /sys/bus/platform/drivers/vmgenid/unbind` 再 `bind`（需要 root），probe 就會再跑一次。driver 裡不能有「只初始化一次」的全域假設。
2. **假設 probe 順序**：觸發順序取決於 initcall 等級、firmware 列舉順序、模組載入時機。依賴其他資源的 driver 必須用 `-EPROBE_DEFER`，而不是假設對方已經 probe 完。上面的 `minibus` 實驗示範了最不利的順序也能收斂。
3. **看到 `of_match_table` 就以為一定會走 DT 配對**：`of_match_ptr()` 在 `CONFIG_OF=n` 時是 `NULL`。上面的 `gcc -E` 結果裡，hisi-rng 的 `.of_match_table` 實際上是 `((void *)0)`。判斷前先查 `.config`。
4. **`devm_kzalloc` 的記憶體被 user-space 介面長期持有**：devm 記憶體在**解除綁定時**釋放，但如果 driver 建立了 `/dev` 節點，user 可能還開著檔案。解除綁定後再 `read`，handler 拿到的私有 struct 已經被釋放。正確做法是讓 file 持有 refcount 的物件用 kref 管理，而不是 devm。這是 driver 的常見 use-after-free 來源。
5. **在 `dmesg` 沒看到錯誤就以為 probe 成功**：`call_driver_probe` 對 `-EPROBE_DEFER`、`-ENODEV`、`-ENXIO` 只印 `dev_dbg`。檢查 `/sys/bus/<bus>/devices/<dev>/driver` 這個 symlink 是否存在才是確認綁定的可靠方法；被 defer 的 device 可以在 `/sys/kernel/debug/devices_deferred` 看到（需要 debugfs）。

## 進階：再往深一層

- **async probe**：`__driver_attach` 裡的 `driver_allows_async_probing(drv)` 讓慢速 driver 在 workqueue 裡 probe，加速開機。driver 用 `.probe_type = PROBE_PREFER_ASYNCHRONOUS` 選擇。讀到 probe 裡有長時間等待的 driver，看看它有沒有開這個。
- **device links 與 fw_devlink**：`-EPROBE_DEFER` 是「試了才知道」的被動機制。較新的 kernel 透過 `fw_devlink` 從 DT／ACPI 的引用關係（例如 `clocks = <&clk0>`）預先建立 device link，讓 consumer 在 supplier probe 完之前根本不被嘗試。`driver_bound()` 裡的 `device_links_driver_bound(dev)` 就是這個機制的一部分。
- **把 driver model 當 Ch 44 的事件系統讀**：bus 會在綁定、解除綁定時發 notifier（`bus_notify(dev, BUS_NOTIFY_BOUND_DRIVER)`，在 `driver_bound` 裡），IOMMU、ACPI 等子系統訂閱它。這是 observer 模式（Ch 45）。
- **[Ch 55](./55-cold-read-driver-probe.md)** 會冷讀一個比 hisi-rng 複雜得多的 probe（有 clock、reset、IRQ、DMA），用的就是本章的四步法。

## 動手練習

1. **讀一個有中斷的小 driver**：在 `drivers/char/hw_random/` 或 `drivers/gpio/` 找一個 probe 裡有 `devm_request_irq` 的 driver（`rg -l devm_request_irq drivers/char/hw_random drivers/gpio | xargs wc -l | sort -n | head`），用四步法寫出一行骨架摘要。
2. **找出 `platform_device` 的三種來源**：分別找一個 DT 建立的（`of_platform_populate`）、ACPI 建立的（`acpi_create_platform_device`）、程式碼建立的（`platform_device_register_simple`）呼叫點，各說明在 `platform_match` 裡會走第幾道比對。
3. **展開另一個 `module_*_driver`**：用本章的 `gcc -E` 方法展開一個 PCI driver 的 `module_pci_driver(...)`，找出它和 platform 版本唯一的差別。
4. **擴充 minibus**：在 `minibus.c` 加入 `driver_override`（device 上的字串欄位，設了就只比名字），以及 `-ENODEV`（probe 拒絕後繼續試下一個 driver）。
5. **（需要 root，本段未實測）** 在你有 root 的 Linux 上對某個 platform device 做 `unbind`／`bind`，用 `dmesg` 和 `/sys/bus/platform/devices/*/driver` 觀察 probe 被再次呼叫。WSL 預設使用者沒有 sudo 時無法進行。

## 本章重點整理

- driver core 維護 device 與 driver 兩份名單，任何一邊加入新成員就觸發配對；配對由 `bus->match` 決定，成功後經 `really_probe → call_driver_probe → bus->probe → drv->probe` 呼叫到 driver。
- `platform_match` 的優先順序：`driver_override` → DT compatible → ACPI ID → id_table → 名字。
- `module_platform_driver` 展開成 init/exit 兩個函式；模組時別名成 `init_module`，built-in 時進 `.initcall6.init`；`MODULE_DEVICE_TABLE` 生成給 modpost 用的別名符號，是自動載入模組的基礎。
- devm 是掛在 device 上的 RAII：資源在 probe 失敗或解除綁定時由 `devres_release_all` 反向釋放。
- 讀 driver 的固定順序：driver struct → probe → 註冊的介面 → 中斷／I/O 路徑。

## 自我檢核

- [ ] 不看筆記，畫出從 `module_init` 到 driver 的 `probe` 被呼叫的路徑，並畫出另一條從 `device_add` 出發的路徑，標出兩者在哪裡匯合。
- [ ] 如果一個 driver 在你的板子上沒有被 probe，你會依序檢查哪幾件事？（提示：config、match table、modalias、deferred）
- [ ] 能解釋為什麼 hisi-rng 不需要 `.remove`，而 vmgenid 的 ACPI notify handler 這件事值得追問？
- [ ] 如果面試官問「devm 和手動釋放各有什麼風險？」，你能舉出 devm 造成 use-after-free 的具體情境嗎？
- [ ] 打開 `/sys/bus/platform/devices/`，挑一個 device，說出它是被哪一道比對綁定的、證據是什麼。

## 延伸閱讀

### 官方文件

- **[The Linux Kernel Device Model](https://docs.kernel.org/driver-api/driver-model/overview.html)** 與同目錄的 **binding**、**platform**、**devres** 幾頁（tree 內：`Documentation/driver-api/driver-model/{overview,binding,platform,devres}.rst`）
  - **讀哪裡**：overview 建立三角關係的整體圖；binding.rst 講配對的觸發時機，正好對應本章「兩個觸發點」；platform.rst 講 platform device 的來源與命名；devres.rst 列出所有 devm 介面並解釋設計動機。
  - **注意**：其中部分內容寫於 2.6 時代，函式名稱以 v6.18 原始碼為準。
- **[ACPI Based Device Enumeration](https://docs.kernel.org/firmware-guide/acpi/enumeration.html)**（tree 內：`Documentation/firmware-guide/acpi/enumeration.rst`）
  - **讀哪裡**：platform device 如何從 ACPI namespace 建立、`acpi_match_table` 怎麼寫；讀完可以解釋 WSL 上 `MSFT1002:00` 的由來。
- **[Linux and the Devicetree](https://docs.kernel.org/devicetree/usage-model.html)**（tree 內：`Documentation/devicetree/usage-model.rst`）
  - **讀哪裡**：DT 節點如何變成 platform device、`compatible` 的比對語意。適合要讀 ARM 板子 driver 的人。

### 書籍

- **《Linux Device Drivers》（3rd ed.）** — Corbet, Rubini, Kroah-Hartman（O'Reilly, 2005，作者已開放免費線上閱讀）
  - **讀哪幾章**：Ch 14「The Linux Device Model」，講 kobject、bus、device、driver、class 的關係與 sysfs。基於 2.6.10，API 細節（例如 `bus_type` 已是 `const`、`remove` 回傳 `void`、devm 當時還不存在）大量改變，但三角關係與 probe 的概念完全相同。
- **《Code Reading: The Open Source Perspective》** — Diomidis Spinellis（Addison-Wesley, 2003）
  - **讀哪幾章**：Ch 9.2「Control Models」中關於事件驅動與 system manager 的部分；本章的 driver core 是這兩種控制模型在 kernel 裡的具體實現。

### 部落格 / 技術文章

- **[LWN.net Kernel Index](https://lwn.net/Kernel/Index/)** — Jonathan Corbet 等
  - **讀哪裡**：索引裡的「Device drivers」、「Device model」、「Deferred probing」等條目；LWN 對 deferred probe 與 fw_devlink 的引入都有專文，可以補本章「進階」一節的背景。
  - **前提知識**：本章的觸發流程圖。

driver core 是一個巨大的事件分派器：device 出現、driver 出現、綁定、解除綁定，各自觸發一組 callback。下一章把這種「由事件推動」的結構一般化，並和另一種同樣常見的結構——state machine——一起看：怎麼從程式碼裡認出它們、怎麼把它們畫成圖。

→ [Ch 44 state machine 與事件驅動架構](./44-state-machines-event-driven.md)
