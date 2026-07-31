# Ch 37 — Device model：kobject/sysfs/bus/driver/device

> **目標**：理解 Linux 統一裝置模型（unified device model）的四個核心物件——`kobject`、`sysfs`、`bus`、`device`/`driver`——以及「driver 註冊 → bus match → probe 初始化裝置」這條驅動生命週期的骨幹。學完你能自己建一個 kobject、掛一個 sysfs attribute、讀寫 `/sys` 觸發你的 `show`/`store`，並在腦中畫出 `/sys` 目錄樹如何對應 kernel 物件階層。這是後面整個 Part 7 驅動篇的地基。

## 為什麼需要 device model？

先想一個規模問題。一顆現代 kernel 要管理的裝置數量是天文數字：CPU、記憶體控制器、PCI/PCIe 上的顯卡與網卡、USB 樹上一路掛下去的鍵盤滑鼠隨身碟、I2C/SPI 上的感測器、platform bus 上 SoC 內建的一堆週邊……每個裝置背後可能有一個驅動，而同一個驅動可能對應好幾個裝置實例（一台機器插三張同型號網卡）。

在 Linux 2.6 之前，這些東西是**各自為政**的。每個匯流排子系統（bus subsystem）自己維護一份「我上面掛了哪些裝置」的清單，用各自發明的資料結構；每種裝置的驅動用自己的方式跟裝置配對；電源管理想知道「關機時該用什麼順序 suspend」時，根本沒有一個統一的地方能查到「誰是誰的 parent」。結果就是重複的程式碼、對不上的抽象、以及熱插拔（hotplug）與電源管理難以做對。

2.6 引入的 **unified device model** 就是要解決這件事。它給出一套統一的框架回答三個問題：

- **有哪些裝置、哪些驅動？** 用統一的 `struct device` / `struct device_driver` 登記。
- **哪個 driver 配對哪個 device？** 每個 bus 定義一套 `match` 規則，配上就呼叫 driver 的 `probe()`。
- **裝置之間的階層與電源關係是什麼？** 用 parent 指標把裝置串成一棵樹，這棵樹決定 suspend/resume 的順序、也決定熱插拔時 uevent 怎麼傳播。

而這一切的底層原子（atom）、以及它暴露給 userspace 的介面，就是 **kobject** 與 **sysfs**。你在 `/sys` 下看到的那整棵目錄樹，不是憑空生出來的檔案，而是 kernel 內部物件階層的**鏡像**。理解 device model，等於理解 `/sys` 為什麼長那樣。

## 先建立直覺

在碰任何 struct 之前，先把四個角色的關係在腦中擺好。我喜歡用「一個組織」來類比：

- **kobject** 是最底層的「員工證」。每個要出現在組織裡的東西都得有一張——它記錄你的名字、你的上級是誰（parent）、有幾個人正引用你（引用計數），以及你在公佈欄（sysfs）上對應哪個位置。它本身不做事，是被**嵌進**別的結構裡當骨架用的。
- **device / driver** 是「職位」與「員工」。`device` 是一個具體的職位（某條 bus 上的一個裝置實例），`driver` 是能勝任某類職位的員工。兩者各自內嵌一張 kobject 員工證。
- **bus** 是「部門」。它定義「什麼樣的員工能坐什麼樣的職位」——也就是 `match` 規則。PCI 部門比對 vendor/device ID，device tree 世界比對 `compatible` 字串。
- **sysfs** 是「公佈欄」。組織裡每張員工證都在公佈欄上有一格（一個 `/sys` 目錄），格子裡的每張紙條（attribute）都能讀、有些能寫——寫下去會**真的觸發**對應員工的動作。

一張圖把 kobject 樹和 sysfs 目錄的鏡像關係畫出來：

```
   kernel 內部：kobject 樹                    userspace 看到：/sys 目錄
   （parent 指標串起來）                      （kobject 名字 = 目錄名）

        kobj("devices")                              /sys/devices/
          │  parent                                     │
          ├── kobj("pci0000:00")          ◄══════►      ├── pci0000:00/
          │     │                          鏡像          │     │
          │     └── kobj("0000:00:1f.2")  ◄══════►      │     └── 0000:00:1f.2/
          │            │  (一個 SATA 控制器 device)      │            ├── vendor   ← attribute
          │            │                                 │            ├── device   ← 讀它 = 呼叫 show()
          │            └── (attributes)                  │            └── power/
          └── kobj("platform")            ◄══════►      └── platform/

   每個 kobject ──對應──► 一個 /sys 目錄
   每個 attribute ──對應──► 目錄裡一個檔案（讀→show()，寫→store()）
   parent 指標 ──決定──► 目錄的巢狀層級
```

記住這張圖：**kobject 提供「樹 + 引用計數 + sysfs 對應」這三件事**，其他所有結構（device、driver、bus、class）都是把 kobject 當地基蓋上去的。

## kobject：device model 的原子

源碼在 `include/linux/kobject.h`。v6.12 的 `struct kobject` 精簡後長這樣：

```c
struct kobject {
    const char              *name;      // 這個物件在 sysfs 裡的目錄名
    struct list_head        entry;      // 掛進所屬 kset 的鏈結
    struct kobject          *parent;    // 上一層 kobject → 決定 sysfs 巢狀
    struct kset             *kset;      // 所屬的 kset（一組同類 kobject）
    const struct kobj_type  *ktype;     // 這類 kobject 的行為（release、sysfs_ops）
    struct kernfs_node      *sd;        // 對應的 sysfs 節點（kernfs 的節點）
    struct kref             kref;       // 引用計數（接 Ch 24 的 atomic）
    unsigned int state_initialized:1;
    unsigned int state_in_sysfs:1;
    unsigned int state_add_uevent_sent:1;
    unsigned int state_remove_uevent_sent:1;
    unsigned int uevent_suppress:1;
    // ...
};
```

逐欄看它為什麼這樣設計：

- **`kref`（引用計數）**：kobject 的生命週期由 `kref` 管，這正是 Ch 24 講的引用計數模式。`kobject_get()` 加一、`kobject_put()` 減一，減到 0 時呼叫 `ktype->release()` 釋放。為什麼裝置需要引用計數？因為一個裝置可能同時被驅動、被開啟它的檔案描述符、被 sysfs 讀取者引用——**沒有任何單一擁有者能決定何時該 free**。誰都可能是最後一個放手的，所以用計數。這也是為什麼你幾乎不會直接 `kfree` 一個 device，而是 `put`。
- **`parent`（階層）**：一個裸指標，指向上一層 kobject。無數個 kobject 用 parent 串成一棵樹，這棵樹**直接決定** sysfs 的目錄巢狀關係。`0000:00:1f.2` 的 parent 是 `pci0000:00`，所以它在 `/sys/devices/pci0000:00/0000:00:1f.2/`。
- **`sd`（sysfs 節點）**：型別是 `struct kernfs_node *`。這裡有個歷史細節值得知道——早期叫 `sysfs_dirent`，後來 sysfs 的底層被抽象成 **kernfs**（一個可重用的 in-memory 檔案系統核心，`cgroup` 的檔案介面也用它）。所以 v6.12 這欄是 `kernfs_node`。kobject 透過它掛進 `/sys`（接 Ch 33 的 VFS）。
- **`ktype`（型別）**：指向 `struct kobj_type`，定義「這一類 kobject 的共同行為」：

```c
struct kobj_type {
    void (*release)(struct kobject *kobj);            // kref 歸零時怎麼釋放
    const struct sysfs_ops *sysfs_ops;                // 讀/寫 attribute 的分派
    const struct attribute_group **default_groups;    // 建立時自動掛的一組 attribute
    // ...
};
```

注意 **`default_groups`**——如果你讀過舊教材看到 `default_attrs`，那是幾年前就移除的欄位，v6.12 只有 `default_groups`（attribute 用 group 打包）。這是很常見的踩雷點。

- **`kset`**：`struct kset` 是「一組相關 kobject 的集合」，而且它**自己也內嵌一個 kobject**：

```c
struct kset {
    struct list_head list;                    // 集合裡所有成員
    spinlock_t list_lock;
    struct kobject kobj;                       // kset 本身也是一個 kobject
    const struct kset_uevent_ops *uevent_ops; // 這組成員發 uevent 時的鉤子
};
```

kset 內嵌 kobject 這個設計很關鍵：它讓「一組東西」本身也能出現在 sysfs 裡當一個目錄（例如 `/sys/bus/pci/devices/` 這個目錄背後就是一個 kset）。而 `uevent_ops` 是熱插拔通知的掛鉤點，等下講 uevent 會回來。

### 建立一個 kobject 的兩種方式

`kobject.h` 提供的核心 API：

- `kobject_init(kobj, ktype)`：初始化（設好 kref = 1、state），但**還沒進 sysfs**。
- `kobject_add(kobj, parent, "name")`：把已初始化的 kobject 掛進 sysfs 樹（在 parent 底下建目錄）。
- `kobject_init_and_add(...)`：上面兩步合一，最常用。
- `kobject_create_and_add("name", parent)`：連 `struct kobject` 的配置都幫你做（用一個內建的 dynamic ktype），回傳指標。動手實作那節會用它。
- `kobject_put(kobj)`：引用計數減一，歸零則釋放。**注意**：`kobject_init_and_add` 就算失敗，也必須用 `kobject_put` 清理，不能直接 `kfree`——因為它可能已經部分登記進 sysfs 了。

## sysfs：把物件階層暴露成檔案

`/sys` 是一個**虛擬檔案系統**——它裡面沒有真實檔案，每次你 `cat` 一個檔案，都是 kernel 當場呼叫某個函式把值算出來給你。源碼在 `fs/sysfs/`，但真正的機制核心在 kernfs（`fs/kernfs/`），sysfs 只是 kernfs 的一個薄封裝。

關鍵問題是：**讀 `/sys/.../vendor` 這個檔案，怎麼變成呼叫驅動的函式？** 答案在 attribute 與 sysfs_ops 這對機制。

`include/linux/sysfs.h` 裡的 attribute 是 sysfs 檔案的抽象：

```c
struct attribute {
    const char      *name;   // 檔名
    umode_t         mode;    // 權限（0444 唯讀、0644 可讀寫…）
};

struct sysfs_ops {
    ssize_t (*show)(struct kobject *, struct attribute *, char *);
    ssize_t (*store)(struct kobject *, struct attribute *, const char *, size_t);
};
```

`sysfs_ops` 掛在 `ktype` 上。當你 `cat` 一個 sysfs 檔案，VFS 的 `read` 一路走下來，最終落到 sysfs 的 kernfs handler，它找出「這個檔案對應哪個 kobject、哪個 attribute」，然後呼叫 `kobj->ktype->sysfs_ops->show(kobj, attr, buf)`。你的 `show` 函式把值 `sprintf` 進 `buf`，回傳寫了幾個 byte，這些 byte 就是 `cat` 印出來的東西。寫（`echo x > file`）則走 `store`。

實務上你很少直接用裸 `struct attribute`，而是用包一層的 `struct kobj_attribute`（定義在 `kobject.h`），它的 show/store 簽章更方便（第二個參數直接給你 `struct kobj_attribute *`，你可以在裡面放 context）：

```c
struct kobj_attribute {
    struct attribute attr;
    ssize_t (*show)(struct kobject *kobj, struct kobj_attribute *attr, char *buf);
    ssize_t (*store)(struct kobject *kobj, struct kobj_attribute *attr,
                     const char *buf, size_t count);
};
```

配合 `__ATTR(name, mode, show, store)` 巨集宣告，以及 `sysfs_create_file()` / `sysfs_create_group()` 把它掛上去。這正是 kernel 對 userspace 暴露「裝置狀態 + 可調參數」的**標準介面**：`/sys/class/net/eth0/mtu`、`/sys/devices/.../power/control`、CPU 頻率調速器的 `scaling_governor`——全都是某個驅動的 `store` 在背後接手（電源相關的接 Ch 42，網路調參接 Ch 43）。這比為每個參數發明一個 ioctl 乾淨太多：一個檔案、標準權限、`cat`/`echo` 就能操作、還自帶 udev 事件。

## bus / device / driver 三元組

到這裡 kobject 與 sysfs 的地基有了。現在看蓋在上面的驅動框架，三個核心結構分散在 `include/linux/device.h` 及其子檔：

**`struct bus_type`**（`include/linux/device/bus.h`）——一種匯流排：

```c
struct bus_type {
    const char *name;                                          // "pci"、"platform"、"usb"…
    int (*match)(struct device *dev, const struct device_driver *drv);
    int (*probe)(struct device *dev);
    int (*remove)(struct device *dev);
    int (*uevent)(const struct device *dev, struct kobj_uevent_env *env);
    // ...
};
```

`match` 是靈魂：它定義「這條 bus 上，一個 device 和一個 driver 算不算配對」。注意 v6.12 的簽章裡 `drv` 是 `const`（6.x 的 const 清理過的結果）。platform bus 的 match（`drivers/base/platform.c` 的 `platform_match`）比對 device tree 的 `compatible` 字串（接 Ch 39）；PCI 的 match（`drivers/pci/pci-driver.c` 的 `pci_bus_match`）比對 vendor/device ID（接 Ch 40）。

**`struct device`**（`include/linux/device.h`）——一個裝置實例：

```c
struct device {
    struct device       *parent;      // 階層：這個裝置的上級（決定 suspend 順序）
    struct kobject      kobj;         // 內嵌的員工證
    const struct bus_type *bus;       // 掛在哪條 bus 上（注意是 const）
    struct device_driver *driver;     // 目前綁定的驅動（沒綁定則 NULL）
    void (*release)(struct device *dev);
    dev_t               devt;         // char/block device 的 major:minor（接 Ch 38）
    const struct class  *class;       // 跨 bus 的功能分類（接下節）
    // ...
};
```

**`struct device_driver`**（`include/linux/device/driver.h`）——一個驅動：

```c
struct device_driver {
    const char          *name;
    const struct bus_type *bus;              // 這個驅動屬於哪條 bus
    int  (*probe)(struct device *dev);       // 配對成功時呼叫：初始化這個裝置
    void (*remove)(struct device *dev);      // 裝置移除/驅動卸載時呼叫
    const struct of_device_id   *of_match_table;   // device tree 比對表（Ch 39）
    const struct acpi_device_id *acpi_match_table; // ACPI 比對表
    // ...
};
```

留意三者的內嵌 kobject 關係：**每個 `device` 內嵌一個 `kobject`**（所以每個裝置在 `/sys/devices/` 下有目錄）；`bus_type` 與 `device_driver` 也各自透過內部的 private 結構持有 kobject，所以 `/sys/bus/` 與 `/sys/bus/*/drivers/` 下也各有目錄。整個 `/sys` 就是這些 kobject 的投影。

### probe 機制：驅動怎麼「開始」

這是本章最重要的一段。一個驅動不是 `insmod` 完就開始跑——它跑起來的觸發點是 **match 成功後被呼叫的 `probe()`**。整條路徑跨越 `drivers/base/` 的三個檔案：

```
   driver_register()              ← 你的驅動呼叫（drivers/base/driver.c）
        │
        ▼
   bus_add_driver()               ← 把 driver 掛上 bus（drivers/base/bus.c）
        │
        ▼
   driver_attach() ── 對 bus 上"每一個"現存 device 試一遍 ──►  __driver_attach()
                                                                （drivers/base/dd.c）
                                     │
                                     ▼
                          driver_match_device(drv, dev)      ← static inline in base.h
                                     │  呼叫 bus->match(dev, drv)
                          ┌──────────┴──────────┐
                       不match                  match！
                          │                       │
                        跳過                       ▼
                                        driver_probe_device()   ← drivers/base/dd.c
                                                  │
                                                  ▼
                                          really_probe()
                                                  │
                                    ┌─────────────┴──────────────┐
                                    ▼                            ▼
                        dev->driver = drv               call_driver_probe():
                        （綁定）                          bus->probe(dev)  優先
                                                          else drv->probe(dev)
                                                  │
                                         probe 成功 → 裝置就緒、attribute 出現在 /sys
                                         probe 回傳錯誤 → 解除綁定、dev->driver = NULL
```

幾個容易記錯、我上面已經按 v6.12 校正過的點：

- `driver_register` 在 `drivers/base/driver.c`（不是 bus.c）。
- `__driver_attach`、`really_probe`、`driver_probe_device`、`device_bind_driver` 全在 `drivers/base/dd.c`（dd = driver/device binding）。
- `driver_match_device` 是 `drivers/base/base.h` 裡的 `static inline`，它薄薄包一下 `bus->match`。
- **`really_probe` 不直接呼叫 `drv->probe`**，而是透過 `call_driver_probe`：**先看 `bus->probe`，有就用它，沒有才 fallback 到 `drv->probe`**。platform 與 PCI 這類 bus 自己提供 `.probe`（`platform_probe`、`pci_device_probe`），所以實際上走的是 `bus->probe`，由 bus 再去呼叫你驅動的 probe。這層間接是為了讓 bus 能在呼叫驅動前後做共通處理（如 pinctrl、runtime PM 準備）。

對稱地，**這條路徑是雙向觸發的**：不只「新驅動來，掃過所有現存裝置」，也「新裝置來（`device_add`，在 `drivers/base/core.c`），掃過所有現存驅動」。所以無論是先有裝置後有驅動、還是先有驅動後插裝置，match 都會發生。這解釋了為什麼你熱插一個 USB 隨身碟，對應的 storage 驅動會自動 probe——`device_add` 觸發了對現存驅動的掃描。

## device 階層與電源、熱插拔

前面說 `parent` 指標把裝置串成樹。這棵樹不是為了好看，它承載兩個真實語意：

**電源順序（接 Ch 42）**：suspend 時，child 必須先於 parent 進睡眠；resume 時反過來，parent 先醒。想想 USB——你不能在 USB 控制器已經斷電之後，還叫掛在它下面的隨身碟去存資料。device model 的樹讓 PM 核心用一次拓撲排序就得到正確的 suspend/resume 順序，不需要每個子系統自己維護。

**熱插拔通知 udev（uevent）**：當裝置被 add/remove、或狀態改變，kernel 產生一個 **uevent**，透過 netlink socket 送到 userspace 的 **udev** daemon。udev 據此建立 `/dev` 節點、載入韌體、跑規則腳本。uevent 的內容由 `bus->uevent` / `class` 的 uevent 鉤子填（前面 `kset->uevent_ops` 就是這個機制的掛點）。這是 kernel 與 userspace 熱插拔協作的標準管道——你等下用 `udevadm monitor` 就能親眼看到這些事件。

## class：跨 bus 的功能分類

最後一個角色。bus 是「實體上掛在哪」，但 userspace 常常想問的是「**所有網路裝置在哪**」「所有 block 裝置在哪」——不管它們實體上掛在 PCI、USB 還是 platform。這就是 `struct class`（`include/linux/device/class.h`，`class_register()` 登記）要回答的。

`/sys/class/net/` 底下是所有網路介面（不管是 PCI 網卡還是 USB 網卡還是虛擬的 loopback）；`/sys/class/block/` 是所有區塊裝置。這些通常是**符號連結**，指回 `/sys/devices/` 下真正的裝置目錄。所以同一個裝置會出現在兩個視角：`/sys/devices/...`（實體拓撲）與 `/sys/class/...`（功能分類）。char device（Ch 38）註冊時常搭配建立一個 class，就是為了讓裝置出現在 `/sys/class` 下、方便 udev 建 `/dev` 節點。

## 動手：建一個 kobject + sysfs attribute

先用眼睛逛，再動手寫。

### 逛 sysfs

在你 QEMU 的 shell（或任何 Linux）裡：

```bash
ls /sys/bus                    # 所有 bus：platform, pci, usb, i2c...
ls /sys/bus/platform/devices   # platform bus 上的裝置實例
ls /sys/bus/platform/drivers   # platform bus 上註冊的驅動
ls /sys/devices                # 實體拓撲樹的根
ls /sys/class                  # 功能分類：net, block, tty, input...

# 看一個裝置的 attribute（實際是呼叫 driver 的 show）
cat /sys/class/net/lo/mtu      # loopback 的 MTU
# 找一個「driver ↔ device 綁定」的證據：
ls -l /sys/bus/platform/devices/*/driver   # 這個符號連結存在 = 已被某 driver probe 綁定
```

那個 `driver` 符號連結就是 probe 成功的鐵證：它從 device 目錄指回綁定它的 driver 目錄。沒綁定的裝置沒有這個連結。

### 寫一個 kobject + attribute 模組

這個模組在 `/sys/kernel/` 下建一個目錄 `myobj`，裡面放一個可讀寫的 `value` 檔案。讀它觸發你的 `show`，寫它觸發你的 `store`。

```c
// myobj.c
#include <linux/init.h>
#include <linux/module.h>
#include <linux/kobject.h>
#include <linux/sysfs.h>

static int my_value;                       // 我們用 sysfs 暴露的「裝置狀態」
static struct kobject *my_kobj;            // 我們的 kobject（會出現在 /sys/kernel/myobj）

// 讀 /sys/kernel/myobj/value 時被呼叫
static ssize_t value_show(struct kobject *kobj, struct kobj_attribute *attr,
                          char *buf)
{
    return sysfs_emit(buf, "%d\n", my_value);   // sysfs_emit：安全寫入 PAGE_SIZE buffer
}

// 寫 /sys/kernel/myobj/value 時被呼叫
static ssize_t value_store(struct kobject *kobj, struct kobj_attribute *attr,
                           const char *buf, size_t count)
{
    int ret = kstrtoint(buf, 10, &my_value);    // 把 userspace 寫入的字串轉成整數
    if (ret < 0)
        return ret;                             // 回傳負值 = 寫入失敗（echo 會報錯）
    pr_info("myobj: value set to %d\n", my_value);
    return count;                               // 回傳「吃掉幾個 byte」= 全部
}

// __ATTR(名字, 權限, show, store) → 產生名為 value_attribute 的 kobj_attribute
static struct kobj_attribute value_attr = __ATTR(value, 0644, value_show, value_store);

static int __init myobj_init(void)
{
    // 在 kernel_kobj（對應 /sys/kernel）底下建立我們的目錄
    my_kobj = kobject_create_and_add("myobj", kernel_kobj);
    if (!my_kobj)
        return -ENOMEM;

    // 把 value 檔案掛進這個 kobject 的目錄
    if (sysfs_create_file(my_kobj, &value_attr.attr)) {
        kobject_put(my_kobj);               // 失敗要 put，不能 kfree
        return -ENOMEM;
    }
    pr_info("myobj: loaded, see /sys/kernel/myobj/value\n");
    return 0;
}

static void __exit myobj_exit(void)
{
    kobject_put(my_kobj);                   // put 到 0 → 自動移除 sysfs 目錄與檔案
    pr_info("myobj: unloaded\n");
}

module_init(myobj_init);
module_exit(myobj_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("kobject + sysfs attribute demo for Ch 37");
```

用 Ch 0 的 Makefile 編出 `myobj.ko`，載入後測試：

```bash
insmod myobj.ko
ls /sys/kernel/myobj/            # 看到 value
cat /sys/kernel/myobj/value      # 觸發 value_show → 印 0
echo 42 > /sys/kernel/myobj/value  # 觸發 value_store
cat /sys/kernel/myobj/value      # 印 42
dmesg | tail                     # 看到 "value set to 42"
echo abc > /sys/kernel/myobj/value  # kstrtoint 失敗 → shell 報 "Invalid argument"
rmmod myobj
```

你剛剛做的，就是每個驅動暴露可調參數的完整機制的縮影。`kobject_create_and_add(..., kernel_kobj)` 用的 `kernel_kobj` 是 kernel 內建、對應 `/sys/kernel` 的 kobject。

### 看 uevent

另開一個 terminal（或在有完整 udev 的環境），跑：

```bash
udevadm monitor --kernel --property
```

然後 `insmod` / `rmmod` 一個裝置驅動、或插拔一個 USB——你會看到 `add`/`remove` 的 uevent 連同 `SUBSYSTEM=`、`DEVPATH=`、`ACTION=` 等屬性刷出來。這就是 device model 的樹在裝置變動時，透過 uevent 通知 userspace 的實況（最小 initramfs 沒有 udev daemon，這步建議在完整發行版上做）。

## 對比與取捨

| 暴露 kernel 狀態/參數的方式 | 適合 | 缺點 |
|---|---|---|
| **sysfs attribute** | 單純的狀態/參數，一值一檔，`cat`/`echo` 可操作、自帶權限與 udev 事件 | 一檔一值的慣例，複雜結構化資料不適合；每次 show 重算 |
| **ioctl** | 複雜、二進位、需原子性的多欄位操作（如 GPU 命令） | 每個 ioctl 是私有 API，難自我描述、難用 shell 操作、易出安全漏洞 |
| **procfs（`/proc`）** | 歷史遺留的雜項資訊（`/proc/meminfo`…） | 沒有 device model 的結構，格式各異、難解析；新功能不建議放這 |
| **debugfs（`/sys/kernel/debug`）** | 除錯用、不保證穩定的旁路介面 | 非穩定 ABI，正式功能不該依賴 |
| **netlink** | 大量、非同步、事件流（uevent、網路設定） | 需要 daemon 端配合，比讀檔案重 |

一句話取捨：**簡單參數用 sysfs、複雜二進位操作用 ioctl、事件流用 netlink**。device model 讓 sysfs 成為前者的預設答案。

## 踩雷集錦

1. **錯誤直覺：「`insmod` 驅動就會開始跑」。** 正確認識：驅動的進入點是 `probe()`，而 `probe()` 只在 **match 成功**時被呼叫。你 `insmod` 一個 PCI 驅動，如果機器上沒有 vendor/device ID 對得上的裝置，`probe` 永遠不會被呼叫，你的驅動「載入了但什麼也沒做」。這是新手 debug 驅動最常卡的地方——先確認 match 有沒有成功（看 `/sys/bus/.../drivers/你的驅動/` 下有沒有指向裝置的連結）。

2. **錯誤直覺：「kobject 用完 `kfree` 掉」。** 正確認識：kobject 由 `kref` 管生命週期，永遠用 `kobject_put()`，讓計數歸零時 `release()` 去釋放。直接 `kfree` 會在別人還持有引用時造成 use-after-free。而且 `kobject_init_and_add` **即使失敗**也要 `kobject_put` 清理，不能 `kfree`——它可能已經部分進了 sysfs。

3. **錯誤直覺：「用 `default_attrs` 掛預設 attribute」。** 正確認識：v6.12 的 `struct kobj_type` 只有 `default_groups`（`const struct attribute_group **`），`default_attrs` 幾年前就移除了。照舊教材寫 `default_attrs` 會編不過。

4. **錯誤直覺：「show 函式可以隨便寫多少 byte 進 buf」。** 正確認識：sysfs 的 `buf` 是**一個 page（PAGE_SIZE，通常 4KB）**，show 必須在這範圍內、且慣例上一個 attribute 只輸出一個值加換行。寫爆 buf 是 kernel bug。用 `sysfs_emit()`（v5.10+）而非裸 `sprintf`，它幫你做邊界檢查。

5. **錯誤直覺：「store 回傳 0 表示成功」。** 正確認識：store 要回傳**消費掉的 byte 數**（通常就是 `count`），回傳 0 會讓 userspace 的 write 陷入無限迴圈（以為還沒寫完一直重試）。失敗時回傳負的 errno。

6. **錯誤直覺：「really_probe 直接呼叫我 driver 的 probe」。** 正確認識：對 platform/PCI 這類 bus，走的是 `bus->probe`（`platform_probe`/`pci_device_probe`），bus 再呼叫你的 probe。所以你設中斷點 debug probe 流程時，別只停在自己的 probe，也看看 `call_driver_probe` 與 `bus->probe`。

## 進階：再往深一層

- **deferred probe（延遲探測）**：`probe` 可以回傳 `-EPROBE_DEFER`，告訴 driver core「我依賴的資源（如某個 regulator、clock）還沒好，晚點再叫我」。core 會把這個裝置放進 deferred 佇列，等有新驅動註冊成功時重試。這在 SoC 開機、裝置間有依賴時極常見（韌體工程師會天天遇到）。機制在 `drivers/base/dd.c` 的 `driver_deferred_probe_*`。
- **`container_of` 是怎麼從 kobject 拿回外層結構的**：kobject 是被**內嵌**的。sysfs 的 show 回呼給你一個 `struct kobject *`，你要拿回外層的 `struct device`（或你自己的結構），靠的是 `container_of(kobj, struct device, kobj)`——用欄位在結構裡的固定偏移量反推起始位址。這個模式在 Ch 5 講過，device model 到處都是它。理解它，你才看得懂驅動源碼裡那些 `to_platform_device(dev)`、`kobj_to_dev(kobj)` 巨集。
- **面試常問**：「sysfs、procfs、debugfs 差在哪？」——sysfs 是 device model 的結構化投影（一檔一值、有嚴格慣例、穩定 ABI）；procfs 是 process/雜項資訊的歷史遺留；debugfs 是不保證穩定的除錯旁路。另一題：「熱插拔一個裝置，從 kernel 到 `/dev` 節點出現，中間發生什麼？」——`device_add` → 建 kobject/sysfs 目錄 → 產生 uevent → netlink 送 udev → udev 依規則建 `/dev` 節點、觸發 probe。能把這條鏈講完整，代表你真的懂 device model。
- **`class` 正在吃掉 `bus` 的一些角色**：近年 kernel 把很多原本各 bus 自建的東西統一到 class/device 核心。讀新驅動時你會看到大量 `device_create()`、`class_create()`——這些都是本章結構的便利包裝。

## 動手練習

1. **證明 probe 由 match 觸發**：找一個系統上實際綁定的 platform 裝置（`ls -l /sys/bus/platform/devices/*/driver` 找有連結的），順著連結找到它的驅動；再找一個**沒有** `driver` 連結的裝置，想想為什麼它沒被 probe。把你的推理寫下來。

2. **gdb 停在 probe 流程**：用 Ch 0 的 QEMU + gdb，`break really_probe`，開機過程中會停很多次（每個裝置 match 成功都停一次）。用 `backtrace` 看是誰呼叫它、`print dev->kobj.name` 看是哪個裝置正在被 probe。感受「開機時 driver core 掃過整棵裝置樹」的規模。

3. **擴充你的 sysfs 模組**：在動手那節的 `myobj` 上再加一個唯讀 attribute（例如 `name`，`show` 回傳固定字串，`store` 設成 NULL、mode 用 0444），驗證寫它會被拒絕（`echo x >` 得到 Permission denied）。再用 `sysfs_create_group` 一次掛一組 attribute，比較和逐個 `sysfs_create_file` 的差別。

4. **弄壞它**：把 `store` 的回傳從 `count` 改成 `0`，重編載入，`echo 5 > value`，觀察 shell 是否卡住（無限重試 write）。理解「回傳消費 byte 數」這個約定為什麼重要。改回來。

## 本章重點整理

- **kobject** 是 device model 的原子，提供三件事：`kref` 引用計數（生命週期）、`parent` 指標（階層樹）、`sd`（對應一個 sysfs 目錄）。其他所有結構（device/driver/bus/kset/class）都內嵌 kobject 蓋上去。
- **sysfs** 把 kobject 樹鏡射成 `/sys` 目錄；讀寫一個 attribute 檔案，透過 `ktype->sysfs_ops` 分派到驅動的 `show`/`store`，這是 kernel 對 userspace 暴露狀態與可調參數的標準介面。
- **bus/device/driver 三元組 + probe**：driver 註冊 → bus 用 `match` 配對 device → 配上就走 `driver_probe_device` → `really_probe` → `call_driver_probe`（`bus->probe` 優先，fallback `drv->probe`）初始化裝置。這是驅動「開始」的核心，雙向觸發（新驅動掃現存裝置、新裝置掃現存驅動）。
- device 的 `parent` 樹決定 suspend/resume 順序與 uevent 傳播；**class** 提供跨 bus 的功能分類（`/sys/class/net` 等），通常以符號連結指回 `/sys/devices`。

## 自我檢核

- [ ] 不看筆記，能畫出 kobject 樹與 `/sys` 目錄的鏡像關係，並說出 kobject 提供的三件事
- [ ] 能解釋 `cat /sys/class/net/lo/mtu` 從 VFS read 到驅動 `show` 之間發生什麼
- [ ] 面試被問「一個驅動 `insmod` 之後為什麼可能什麼都沒做」，你能用 match/probe 機制回答
- [ ] 能說出 `really_probe` 為什麼不直接呼叫 `drv->probe`（`bus->probe` 優先的理由）
- [ ] 能寫出一個建 kobject + 可讀寫 sysfs attribute 的模組，並解釋為什麼失敗清理要用 `kobject_put` 不能 `kfree`
- [ ] 能講清楚 sysfs / procfs / debugfs / ioctl 各自的定位與取捨

## 延伸閱讀

### 官方文件

- **[Documentation/driver-api/driver-model/](https://www.kernel.org/doc/html/latest/driver-api/driver-model/index.html)**
  - **讀哪裡**：`overview.rst`、`bus.rst`、`driver.rst`、`device.rst`、`binding.rst` 這幾篇，短而精。這是 device model 各結構與 bind 流程的官方權威說明
  - **和本章的關聯**：本章的三元組與 probe 流程就是它的濃縮；讀源碼卡住時回來對照

- **[Documentation/core-api/kobject.rst](https://www.kernel.org/doc/html/latest/core-api/kobject.html)**
  - **讀哪裡**：整篇。作者是 Greg KH（driver core 維護者），把 kobject/kset/ktype 的關係與生命週期講得最清楚
  - **能學到什麼**：為什麼 kobject 要用引用計數、`kobject_put` 的正確用法、內嵌與 `container_of` 的慣例

### 深入文章

- **[LWN: The zen of kobjects](https://lwn.net/Articles/51437/)** — Greg KH / Jonathan Corbet
  - **讀哪裡**：整篇。雖然年代較早，kobject 的核心設計哲學（為什麼是這樣的原子）沒變，把「kobject 到底在解決什麼」講透
  - **前提**：讀完本章「kobject」那節

- **[Documentation/filesystems/sysfs.rst](https://www.kernel.org/doc/html/latest/filesystems/sysfs.html)**
  - **讀哪裡**：attribute、show/store、`sysfs_emit` 慣例那幾節
  - **為什麼值得讀**：本章動手那節的 sysfs 規矩（一檔一值、PAGE_SIZE 邊界、回傳值約定）在這裡有官方版本，寫真實驅動前該過一遍

### 書籍

- **《Linux Device Drivers, 3rd Ed.》（LDD3）** — Corbet / Rubini / Kroah-Hartman
  - **讀哪裡**：Ch 14「The Linux Device Model」。這是 device model 最經典的整章講解，kobject/kset/bus/device 一路到 sysfs 都有
  - **注意**：書對應 2.6 早期，函式名（如 `default_attrs` vs `default_groups`）與細節以本章的 v6.12 為準，但架構與心智模型完全適用

device model 的地基有了。下一章我們蓋上第一種具體裝置——character device，看 `/dev` 節點怎麼透過 `dev_t`（major/minor）連到你的 `file_operations`，把一個裝置變成能 `open`/`read`/`write` 的檔案。

→ [Ch 38 Character device 與 misc device](./38-char-misc-device.md)
