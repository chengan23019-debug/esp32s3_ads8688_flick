idf_component_register(SRCS "ads8688.c" "ads8688_fast.c"
                       INCLUDE_DIRS "include"
                       REQUIRES driver esp_driver_spi esp_driver_gpio esp_timer hal)

