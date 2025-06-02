#include <esp_log.h>
#include <esp_err.h>
#include <nvs.h>
#include <nvs_flash.h>
#include <driver/gpio.h>
#include <esp_event.h>

#include "settings.h"
#include "application.h"
#include "system_info.h"

#define TAG "main"

extern "C" void app_main(void)
{
    // Initialize the default event loop
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    // Initialize NVS flash for WiFi configuration
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND)
    {
        ESP_LOGW(TAG, "Erasing NVS flash to fix corruption");
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    // ✅ 使用 Settings 管理配网标志和计数器
    Settings settings("wifi", true);
    int reboot_count = settings.GetInt("reboot_count", 0);
    reboot_count++;
    settings.SetInt("reboot_count", reboot_count);
    ESP_LOGI(TAG, "Reboot count: %d", reboot_count);

    if (reboot_count % 2 == 3)
    {
        // ✅ 每两次重启，强制进入 WiFi 配网模式
        ESP_LOGW(TAG, "Triggering WiFi config mode (force_ap = 1)");
        settings.SetInt("force_ap", 1);
    }

    // 启动主程序
    Application::GetInstance().Start();
}
