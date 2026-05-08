# Ch 18 — 實作：Custom GATT Service

> 目標：建一個完整的 BLE sensor service，自定義 128-bit UUID，Characteristic 支援 Read 和 Notify，手機透過 nRF Connect App 連接並訂閱溫度 Notification，每秒收到更新。同時處理 CCCD 和連接參數協商。

---

## 設計目標

```
Sensor Service（128-bit UUID）
└── Temperature Characteristic（128-bit UUID）
    ├── Properties：READ | NOTIFY
    ├── Value：2 bytes（int16_t，單位 0.01°C，例如 2530 = 25.30°C）
    └── CCCD（0x2902）：Client 寫入 0x0001 啟用 Notification
```

---

## 自定義 UUID

128-bit UUID 的命名慣例：用一個 base UUID，Service 和 Characteristic 只改中間的幾個 nibble：

```c
/* 自定義 Sensor Service UUID：
 * 12345678-0000-1000-8000-00805F9B34FB（借用 Bluetooth base UUID 格式示範）
 * 實際產品應該用 UUID generator 產生全隨機值，避免和其他裝置衝突 */

/* NimBLE 128-bit UUID 定義方式（小端序存放）*/
static const ble_uuid128_t sensor_svc_uuid =
    BLE_UUID128_INIT(0xFB, 0x34, 0x9B, 0x5F, 0x80, 0x00,
                     0x00, 0x80, 0x00, 0x10, 0x00, 0x00,
                     0x78, 0x56, 0x34, 0x12);
/* 對應 12345678-0000-1000-8000-00805F9B34FB */

static const ble_uuid128_t temp_chr_uuid =
    BLE_UUID128_INIT(0xFB, 0x34, 0x9B, 0x5F, 0x80, 0x00,
                     0x00, 0x80, 0x00, 0x10, 0x00, 0x00,
                     0x79, 0x56, 0x34, 0x12);
/* 對應 12345679-0000-1000-8000-00805F9B34FB（只改第 4 byte）*/
```

---

## 全域狀態：connection handle 和 attribute handle

```c
#include "host/ble_hs.h"
#include "host/ble_gatt.h"
#include "host/ble_gap.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/timers.h"
#include "esp_log.h"

static const char *TAG = "SENSOR_SVC";

/* 追蹤當前連接的 handle（0xFFFF 表示沒有連接）*/
static uint16_t current_conn_handle = BLE_HS_CONN_HANDLE_NONE;

/* Temperature Characteristic 的 value handle，由 NimBLE 在初始化時分配 */
static uint16_t temp_chr_val_handle;

/* 追蹤 Client 是否有訂閱 Notification */
static bool notify_enabled = false;

/* 模擬溫度讀取（實際應用接 I2C 感測器）*/
static int16_t read_temperature_raw(void)
{
    /* 回傳 25.30°C（乘以 100 的整數）*/
    /* 實際應該呼叫 BME280 或 DS18B20 驅動 */
    static int16_t temp = 2530;
    temp += (int16_t)((esp_random() % 10) - 5);  /* 模擬小幅波動 */
    return temp;
}
```

---

## Characteristic Access Callback

```c
static int temp_chr_access_cb(uint16_t conn_handle, uint16_t attr_handle,
                               struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    int rc;

    switch (ctxt->op) {

    case BLE_GATT_ACCESS_OP_READ_CHR:
        /* Client 發送 Read Request，回傳當前溫度 */
        {
            int16_t temp = read_temperature_raw();
            rc = os_mbuf_append(ctxt->om, &temp, sizeof(temp));
            if (rc != 0) {
                return BLE_ATT_ERR_INSUFFICIENT_RES;
            }
            ESP_LOGI(TAG, "Read temp: %d (%.2f°C)",
                     temp, (float)temp / 100.0f);
        }
        break;

    case BLE_GATT_ACCESS_OP_WRITE_CHR:
        /* 這個 Characteristic 沒有開 WRITE flag，正常不會到這裡
         * 如果要支援 Client 寫入更新頻率，在這裡解析 ctxt->om */
        return BLE_ATT_ERR_WRITE_NOT_PERMITTED;

    default:
        return BLE_ATT_ERR_UNLIKELY;
    }

    return 0;
}
```

---

## GATT Service Table 定義

```c
static const struct ble_gatt_svc_def sensor_gatt_svcs[] = {
    {
        /* Primary Service */
        .type = BLE_GATT_SVC_TYPE_PRIMARY,
        .uuid = &sensor_svc_uuid.u,

        .characteristics = (struct ble_gatt_chr_def[]) {
            {
                /* Temperature Characteristic */
                .uuid       = &temp_chr_uuid.u,
                .access_cb  = temp_chr_access_cb,
                .val_handle = &temp_chr_val_handle,  /* NimBLE 會填入分配的 handle */
                .flags      = BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_NOTIFY,
                /* flags 不包含 WRITE：Client 只能讀和訂閱，不能寫 */
                /* CCCD（0x2902）由 NimBLE 在有 NOTIFY flag 時自動加入 */
            },
            { 0 }
        },
    },
    { 0 }
};

void sensor_gatt_init(void)
{
    int rc;

    /* 計算需要的 attribute 數量（必須在 add 之前呼叫）*/
    rc = ble_gatts_count_cfg(sensor_gatt_svcs);
    if (rc != 0) {
        ESP_LOGE(TAG, "gatts_count_cfg failed: %d", rc);
        return;
    }

    /* 登錄 service */
    rc = ble_gatts_add_svcs(sensor_gatt_svcs);
    if (rc != 0) {
        ESP_LOGE(TAG, "gatts_add_svcs failed: %d", rc);
        return;
    }

    ESP_LOGI(TAG, "Sensor GATT service registered");
}
```

---

## GAP Event Callback 完整版（含 Subscribe 處理）

```c
static int ble_gap_event_handler(struct ble_gap_event *event, void *arg)
{
    switch (event->type) {

    case BLE_GAP_EVENT_CONNECT:
        if (event->connect.status == 0) {
            current_conn_handle = event->connect.conn_handle;
            notify_enabled = false;
            ESP_LOGI(TAG, "Connected, handle=%d", current_conn_handle);

            /* 請求縮短連接間隔（預設可能是 50ms，改成 20ms 讓 Notification 更及時）*/
            struct ble_gap_upd_params conn_params = {
                .itvl_min            = BLE_GAP_CONN_ITVL_MS(20),
                .itvl_max            = BLE_GAP_CONN_ITVL_MS(40),
                .latency             = 0,
                .supervision_timeout = BLE_GAP_SUPERVISION_TIMEOUT_MS(4000),
            };
            ble_gap_update_params(current_conn_handle, &conn_params);
        } else {
            ESP_LOGW(TAG, "Connect failed, status=%d", event->connect.status);
            ble_app_advertise();
        }
        break;

    case BLE_GAP_EVENT_DISCONNECT:
        ESP_LOGI(TAG, "Disconnected, reason=0x%02X",
                 event->disconnect.reason);
        current_conn_handle = BLE_HS_CONN_HANDLE_NONE;
        notify_enabled = false;
        /* 重新廣播 */
        ble_app_advertise();
        break;

    case BLE_GAP_EVENT_SUBSCRIBE:
        if (event->subscribe.attr_handle == temp_chr_val_handle) {
            notify_enabled = (event->subscribe.cur_notify != 0);
            ESP_LOGI(TAG, "Notify %s by conn=%d",
                     notify_enabled ? "enabled" : "disabled",
                     event->subscribe.conn_handle);
        }
        break;

    case BLE_GAP_EVENT_CONN_UPDATE_REQ:
        /* Central 請求更新連接參數，回傳 0 表示接受 */
        return 0;

    case BLE_GAP_EVENT_MTU:
        ESP_LOGI(TAG, "MTU updated: conn=%d, mtu=%d",
                 event->mtu.conn_handle, event->mtu.value);
        break;

    default:
        break;
    }
    return 0;
}
```

---

## Notify 實作：每秒發送溫度

```c
/*
 * send_temp_notification - 向已訂閱的 Central 發送溫度 Notification
 * 必須在 NimBLE host task 的 context 裡呼叫，
 * 或透過 ble_npl_event（NimBLE 的 event queue）從其他 task 排程
 */
static void send_temp_notification(void)
{
    struct os_mbuf *om;
    int16_t temp;
    int rc;

    if (current_conn_handle == BLE_HS_CONN_HANDLE_NONE || !notify_enabled) {
        return;
    }

    temp = read_temperature_raw();

    /* 分配 mbuf，填入溫度資料 */
    om = ble_hs_mbuf_from_flat(&temp, sizeof(temp));
    if (om == NULL) {
        ESP_LOGW(TAG, "mbuf alloc failed");
        return;
    }

    /* ble_gatts_notify_custom：發送 custom Notification
     * om 的所有權轉移給 NimBLE，成功後不要再 free */
    rc = ble_gatts_notify_custom(current_conn_handle,
                                 temp_chr_val_handle, om);
    if (rc != 0) {
        ESP_LOGW(TAG, "notify failed: %d", rc);
        /* rc == BLE_HS_ENOTCONN：連接已斷開 */
        /* rc == BLE_HS_ENOTSUP：沒有啟用 Notify  */
    } else {
        ESP_LOGD(TAG, "Notified temp: %d (%.2f°C)",
                 temp, (float)temp / 100.0f);
    }
}

/* 每秒發送溫度的 FreeRTOS task */
static void sensor_task(void *arg)
{
    while (1) {
        send_temp_notification();
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}
```

**注意**：`send_temp_notification` 裡直接呼叫 NimBLE API 是可以的，因為 NimBLE 的 host API 是 thread-safe 的（有內部 mutex）。但如果呼叫頻繁，建議用 NimBLE 的 event 機制排進 host task queue，避免在 app task 裡 block。

---

## 完整 main.c

```c
#include "nvs_flash.h"
#include "esp_bt.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "host/ble_hs.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"

extern void sensor_gatt_init(void);
extern int  ble_gap_event_handler(struct ble_gap_event *, void *);

static void ble_on_sync(void)
{
    ESP_LOGI(TAG, "BLE sync");
    ble_app_advertise();
}

static void nimble_host_task(void *param)
{
    nimble_port_run();
    nimble_port_freertos_deinit();
}

void app_main(void)
{
    /* NVS init */
    nvs_flash_init();

    /* controller init */
    esp_bt_controller_config_t bt_cfg = BT_CONTROLLER_INIT_CONFIG_DEFAULT();
    esp_bt_controller_mem_release(ESP_BT_MODE_CLASSIC_BT);
    esp_bt_controller_init(&bt_cfg);
    esp_bt_controller_enable(ESP_BT_MODE_BLE);

    /* NimBLE host init */
    nimble_port_init();

    ble_svc_gap_init();
    ble_svc_gatt_init();
    ble_svc_gap_device_name_set("ESP32-Sensor");

    sensor_gatt_init();  /* 登錄自定義 GATT table */

    ble_hs_cfg.sync_cb  = ble_on_sync;
    ble_hs_cfg.sm_io_cap = BLE_SM_IO_CAP_NO_IO;
    ble_hs_cfg.sm_bonding = 1;
    ble_hs_cfg.sm_sc = 1;

    /* 啟動 NimBLE host task */
    nimble_port_freertos_init(nimble_host_task);

    /* 啟動感測器 task */
    xTaskCreate(sensor_task, "sensor", 2048, NULL, 5, NULL);
}
```

---

## 手機端驗證（nRF Connect App）

1. 開啟 nRF Connect，掃描，找到 `ESP32-Sensor`
2. 點 Connect
3. 展開 Services，找到 UUID `12345678-0000-1000-8000-00805F9B34FB`
4. 找到 Temperature Characteristic（`12345679-...`）
5. 點 Read（向下箭頭），應該看到 2 bytes 的溫度值
6. 點訂閱（三個向下箭頭的 Notify 按鈕），CCCD 會自動寫入 `0x0100`
7. 每秒看到溫度值更新

---

## 連接參數說明

| 參數 | 單位 | 典型值 | 影響 |
|------|------|--------|------|
| Connection Interval | 1.25ms | 20~100ms | 越小延遲越低，功耗越高 |
| Slave Latency | 個連接事件 | 0~10 | >0 時 Peripheral 可跳過 N 個事件，省電但增加延遲 |
| Supervision Timeout | 10ms | 1~32s | 超過這個時間沒有任何通訊，判斷連接斷開 |

Supervision Timeout > (1 + Slave Latency) × Connection Interval × 2 是必要條件，否則連接會立刻超時。

---

## 自我檢核

- [ ] 能解釋 128-bit UUID 在 NimBLE 裡的 `BLE_UUID128_INIT` macro 使用的位元組序
- [ ] 能說明 `val_handle` 指標的用途：NimBLE 為什麼要在初始化後填入這個值
- [ ] 能解釋 CCCD（0x2902）是哪個層自動加入的，Client 寫入 0x0001 代表什麼
- [ ] 能說明 `BLE_GAP_EVENT_SUBSCRIBE` 的 `cur_notify` 欄位：0 和 1 各代表什麼
- [ ] 實際燒錄，用 nRF Connect 連接並訂閱 Notification，確認每秒收到溫度更新
- [ ] 把 Slave Latency 設成 4，觀察 Notification 延遲有什麼變化

BLE sensor service 建完。下一章換方向，進入 LoRa 的展頻原理和長距離通訊。

→ [Ch 19 LoRa 原理](./19-lora-protocol.md)
