# Ch 17 — ESP32 BLE 底層

> 目標：搞清楚 ESP32 BLE 的 controller 初始化流程、VHCI 架構、NimBLE host 的初始化序列，以及如何用 NimBLE 底層 API 設定 GAP callback 和定義 GATT service table。這門課整體用 NimBLE，不用 Bluedroid。

---

## 兩種 Host Stack：選哪個

ESP32 的 BLE 架構：一個 controller（閉源，跑在 PRO CPU）+ 一個 host（開源，跑在 APP CPU）。Host stack 有兩個選擇：

| | Bluedroid | NimBLE |
|--|-----------|--------|
| 來源 | Android Bluetooth stack 移植 | Apache NimBLE，專為嵌入式設計 |
| RAM 需求 | ~120KB | ~50~70KB |
| Classic BT 支援 | 有（A2DP、HID、SPP）| 無，只有 BLE |
| 程式碼風格 | callback 層層嵌套，複雜 | 相對線性，callback 結構清楚 |
| 這門課的選擇 | 不用 | 用這個 |

選 NimBLE 的時機：只需要 BLE、RAM 緊繃、或者想看清楚 GAP/GATT 底層怎麼運作。需要 Classic BT（藍牙音頻、HID 鍵盤）或跑 Bluedroid 的既有程式碼，才考慮 Bluedroid。

在 `menuconfig` 裡：`Component config → Bluetooth → Bluetooth Host → NimBLE`

---

## Controller 初始化：esp_bt.h

BLE controller（Link Layer + PHY）由 ESP-IDF 的 `esp_bt` component 管理：

```c
#include "esp_bt.h"
#include "esp_err.h"
#include "nvs_flash.h"

/* controller 設定結構，ESP_BT_CONTROLLER_CONFIG_MAGIC_VAL 確保版本匹配 */
esp_bt_controller_config_t bt_cfg = BT_CONTROLLER_INIT_CONFIG_DEFAULT();

void ble_controller_init(void)
{
    esp_err_t ret;

    /* NVS 需要先初始化，BLE bonding 資訊存在 NVS */
    ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES ||
        ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }

    /* 釋放 Classic BT 的 memory（我們只用 BLE）
     * 這一步不可逆，呼叫後無法再啟用 Classic BT */
    ESP_ERROR_CHECK(esp_bt_controller_mem_release(ESP_BT_MODE_CLASSIC_BT));

    /* 初始化 controller */
    ESP_ERROR_CHECK(esp_bt_controller_init(&bt_cfg));

    /* 啟動 controller，ESP_BT_MODE_BLE = 只開 BLE */
    ESP_ERROR_CHECK(esp_bt_controller_enable(ESP_BT_MODE_BLE));
}
```

`esp_bt_controller_mem_release(ESP_BT_MODE_CLASSIC_BT)` 把 Classic BT 的 memory region 歸還給 heap，可以省下約 30KB RAM。在純 BLE 應用裡這一步幾乎是必做的。

---

## VHCI 架構

Controller 和 Host 之間的通訊走 **VHCI**（Virtual HCI）介面，邏輯上等同於 HCI UART，但不走實體 UART，而是 ESP-IDF 內部的 callback 機制：

```
Host（NimBLE）                    Controller
   nimble_port_run()
        │
        │ ble_hs_hci_rx_evt         VHCI send
        │←──────────────────────────────────│
        │                                    │
        │ esp_vhci_host_send_packet ────────→│ VHCI receive
        │                                    │
```

NimBLE 已經把 VHCI 對接處理好了。你只需要呼叫 `nimble_port_init()`，它內部會設定好 VHCI callback。

---

## NimBLE 初始化完整流程

```c
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "host/ble_hs.h"
#include "host/util/util.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"
#include "esp_log.h"

static const char *TAG = "BLE_INIT";

/* NimBLE host task，必須跑在獨立 FreeRTOS task */
static void nimble_host_task(void *param)
{
    /* nimble_port_run() 進入 NimBLE event loop，不會 return
     * 除非 nimble_port_stop() 被呼叫 */
    nimble_port_run();
    nimble_port_freertos_deinit();
}

/* host 重置事件（第一次啟動或連接中斷後重置也會觸發）*/
static void ble_on_reset(int reason)
{
    ESP_LOGE(TAG, "BLE host reset, reason=%d", reason);
}

/* host 同步完成事件：controller 準備好了，可以開始廣播 */
static void ble_on_sync(void)
{
    ESP_LOGI(TAG, "BLE host synced with controller");

    /* 這裡呼叫 advertising 啟動函式 */
    /* ble_app_advertise(); */
}

void ble_init(void)
{
    /* 1. 初始化 NimBLE port（包含設定 VHCI）*/
    nimble_port_init();

    /* 2. 設定 host callbacks */
    ble_hs_cfg.reset_cb = ble_on_reset;
    ble_hs_cfg.sync_cb  = ble_on_sync;

    /* 3. 設定 GAP 安全模式（不強制配對，Just Works）*/
    ble_hs_cfg.sm_io_cap        = BLE_SM_IO_CAP_NO_IO;
    ble_hs_cfg.sm_bonding       = 1;   /* 允許 bonding */
    ble_hs_cfg.sm_mitm          = 0;   /* 不要求 MITM 防護 */
    ble_hs_cfg.sm_sc            = 1;   /* LE Secure Connections */

    /* 4. 初始化 GAP 和 GATT 服務（NimBLE 內建的 mandatory services）*/
    ble_svc_gap_init();
    ble_svc_gatt_init();

    /* 設定 GAP 裝置名稱 */
    ble_svc_gap_device_name_set("ESP32-Sensor");

    /* 5. 啟動 NimBLE host task（優先級要高於 app task）*/
    nimble_port_freertos_init(nimble_host_task);
}
```

---

## GAP Event Callback

連接/斷線事件都透過 `ble_gap_event_fn` 型態的 callback 通知：

```c
#include "host/ble_gap.h"

static int ble_gap_event_handler(struct ble_gap_event *event, void *arg)
{
    switch (event->type) {

    case BLE_GAP_EVENT_CONNECT:
        if (event->connect.status == 0) {
            /* 連接成功 */
            ESP_LOGI(TAG, "Connected, conn_handle=%d",
                     event->connect.conn_handle);
            /* 可以在這裡協商連接參數 */
        } else {
            /* 連接失敗，重新開始廣播 */
            ESP_LOGW(TAG, "Connect failed, status=%d",
                     event->connect.status);
            /* ble_app_advertise(); */
        }
        break;

    case BLE_GAP_EVENT_DISCONNECT:
        ESP_LOGI(TAG, "Disconnected, reason=%d",
                 event->disconnect.reason);
        /* 重新開始廣播 */
        /* ble_app_advertise(); */
        break;

    case BLE_GAP_EVENT_CONN_UPDATE:
        /* Central 請求更新連接參數 */
        ESP_LOGI(TAG, "Conn update, status=%d",
                 event->conn_update.status);
        break;

    case BLE_GAP_EVENT_NOTIFY_TX:
        /* Notification 發送完成 */
        if (event->notify_tx.status != 0) {
            ESP_LOGW(TAG, "Notify TX failed, status=%d",
                     event->notify_tx.status);
        }
        break;

    case BLE_GAP_EVENT_SUBSCRIBE:
        /* Client 訂閱或取消訂閱 Notification */
        ESP_LOGI(TAG, "Subscribe: conn=%d, attr=%d, reason=%d, "
                 "notify=%d, indicate=%d",
                 event->subscribe.conn_handle,
                 event->subscribe.attr_handle,
                 event->subscribe.reason,
                 event->subscribe.cur_notify,
                 event->subscribe.cur_indicate);
        break;

    default:
        break;
    }
    return 0;
}
```

---

## GATT Server Service Table 定義

NimBLE 用靜態 table（`ble_gatt_svc_def` 陣列）描述所有 service 和 characteristic：

```c
#include "host/ble_gatt.h"

/* UUID 定義（16-bit SIG assigned 範例）*/
/* 自定義 128-bit UUID 在 Ch 18 示範 */
static const ble_uuid16_t battery_svc_uuid = BLE_UUID16_INIT(0x180F);
static const ble_uuid16_t battery_chr_uuid = BLE_UUID16_INIT(0x2A19);

/* Characteristic access callback：read 請求時呼叫 */
static int battery_chr_access(uint16_t conn_handle, uint16_t attr_handle,
                               struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    if (ctxt->op == BLE_GATT_ACCESS_OP_READ_CHR) {
        uint8_t battery_level = 87;  /* 假資料 */
        int rc = os_mbuf_append(ctxt->om, &battery_level, sizeof(battery_level));
        return (rc == 0) ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
    }
    return BLE_ATT_ERR_UNLIKELY;
}

/* GATT service table，以 {0} 結尾 */
static const struct ble_gatt_svc_def gatt_svcs[] = {
    {
        .type = BLE_GATT_SVC_TYPE_PRIMARY,
        .uuid = &battery_svc_uuid.u,
        .characteristics = (struct ble_gatt_chr_def[]) {
            {
                .uuid        = &battery_chr_uuid.u,
                .access_cb   = battery_chr_access,
                .flags       = BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_NOTIFY,
            },
            { 0 }  /* characteristics 陣列結尾 */
        },
    },
    { 0 }  /* services 陣列結尾 */
};

void gatt_server_init(void)
{
    int rc;

    /* 把 service table 登錄到 NimBLE，在 ble_hs_cfg.sync_cb 之前呼叫 */
    rc = ble_gatts_count_cfg(gatt_svcs);
    assert(rc == 0);

    rc = ble_gatts_add_svcs(gatt_svcs);
    assert(rc == 0);
}
```

---

## Advertising 設定與啟動

```c
#include "host/ble_gap.h"
#include "host/ble_hs_adv.h"

static uint8_t own_addr_type;

static void ble_app_advertise(void)
{
    struct ble_gap_adv_params adv_params = {0};
    struct ble_hs_adv_fields fields = {0};
    int rc;

    /* 構建 advertising data */
    fields.flags = BLE_HS_ADV_F_DISC_GEN |   /* LE General Discoverable Mode */
                   BLE_HS_ADV_F_BREDR_UNSUP;  /* BR/EDR Not Supported */

    const char *name = ble_svc_gap_device_name();
    fields.name      = (uint8_t *)name;
    fields.name_len  = strlen(name);
    fields.name_is_complete = 1;

    rc = ble_gap_adv_set_fields(&fields);
    if (rc != 0) {
        ESP_LOGE(TAG, "adv_set_fields failed: %d", rc);
        return;
    }

    /* Advertising 參數 */
    adv_params.conn_mode  = BLE_GAP_CONN_MODE_UND;  /* Connectable Undirected */
    adv_params.disc_mode  = BLE_GAP_DISC_MODE_GEN;  /* General Discoverable */
    adv_params.itvl_min   = BLE_GAP_ADV_ITVL_MS(100);  /* 100ms */
    adv_params.itvl_max   = BLE_GAP_ADV_ITVL_MS(200);  /* 200ms */

    /* 決定地址類型（Public or Random）*/
    rc = ble_hs_id_infer_auto(0, &own_addr_type);
    assert(rc == 0);

    rc = ble_gap_adv_start(own_addr_type, NULL, BLE_HS_FOREVER,
                           &adv_params, ble_gap_event_handler, NULL);
    if (rc != 0) {
        ESP_LOGE(TAG, "adv_start failed: %d", rc);
    }
}
```

---

## 如何用 HCI Log Debug

在 `menuconfig` 裡開啟：
```
Component config → Bluetooth → Bluedroid Enable / NimBLE → NimBLE
  → Enable BLE Host log (BLE_HOST_LOG_LEVEL) → DEBUG
```

或在程式裡：

```c
/* 設定 NimBLE log level，啟用 DEBUG 後會輸出每個 HCI 指令和事件 */
/* 需要在 nimble_port_init() 之前呼叫 */
esp_log_level_set("NimBLE", ESP_LOG_DEBUG);
```

DEBUG level 會輸出 HCI OpCode（例如 `HCI LE Set Advertising Parameters`）和事件 code，適合排查 controller 不回應或 advertising 設定失敗的問題。正常跑的時候記得關掉，不然 log 太多會影響 timing。

---

## NimBLE vs Bluedroid 選擇時機總結

| 情況 | 選擇 |
|------|------|
| 純 BLE 感測器、BLE UART proxy | NimBLE |
| RAM < 100KB（如 ESP32-C3 4MB flash 版）| NimBLE |
| 需要 BT Classic（A2DP 音頻、SPP 序列）| Bluedroid |
| 需要 HID over Classic（遊戲手柄、鍵盤）| Bluedroid |
| 想看懂 GAP/GATT 底層怎麼 work | NimBLE（程式碼更直接）|

---

## 自我檢核

- [ ] 能說明 ESP32 BLE 的 controller 和 host 各跑在哪裡，VHCI 的角色是什麼
- [ ] 知道 `esp_bt_controller_mem_release(ESP_BT_MODE_CLASSIC_BT)` 的意義和不可逆性
- [ ] 能說明 NimBLE 初始化的五個步驟（controller init → port init → cfg → svc init → task）
- [ ] 能解釋 `ble_on_sync` callback 什麼時候觸發，為什麼要在這裡才啟動 advertising
- [ ] 知道 `ble_gatt_svc_def` table 的結構，以及雙層 `{0}` 結尾的必要性
- [ ] 能說明 `BLE_GAP_EVENT_SUBSCRIBE` 事件裡 `cur_notify` 的含義

下一章實作一個完整的 custom GATT sensor service，讓手機訂閱溫度 Notification。

→ [Ch 18 實作：Custom GATT Service](./18-ble-gatt-service.md)
