#include <WiFi.h>
#include <micro_ros_arduino.h>
#include <rcl/rcl.h>
#include <rclc/rclc.h>
#include <rclc/executor.h>
#include <std_msgs/msg/int32_multi_array.h>
#include <time.h>
#include <sys/time.h>

char ssid[]       = "1ab";
char password[]   = "gvyg6249";
char agent_ip[]   = "172.22.194.168";
size_t agent_port = 8888;

#define TARGET_PAYLOAD_BYTE 1024
#define QUEUE_DEPTH         100
#define NODE_ID             7
#define PUBLISH_PERIOD_MS   100

#define STR_(x) #x
#define STR(x)  STR_(x)
#define NODE_NAME "esp32_node_" STR(NODE_ID)
#define ARRAY_SIZE (TARGET_PAYLOAD_BYTE / 4)

rcl_publisher_t publisher;
std_msgs__msg__Int32MultiArray msg;
rclc_support_t support;
rcl_node_t node;
rclc_executor_t executor;

int32_t memory_buffer[ARRAY_SIZE];
int32_t seq_id = 0;
unsigned long waktu_sebelumnya = 0;

void sync_ntp() {
    configTime(0, 0, agent_ip);
    Serial.print("[NTP] Sinkronisasi");
    time_t now = 0;
    int retry = 0;
    while (now < 1600000000 && retry < 40) {
        delay(500);
        now = time(nullptr);
        Serial.print(".");
        retry++;
    }
    if (now >= 1600000000) {
        Serial.printf(" OK (unix=%ld)\n", (long)now);
    } else {
        Serial.println(" GAGAL! Cek server NTP (chrony) di PC. ESP32 restart...");
        delay(2000);
        ESP.restart();
    }
}

void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println("\n=================================================");
    Serial.printf("BEST EFFORT | %s | PAYLOAD %d B | QD %d | 10 Hz\n",
                  NODE_NAME, TARGET_PAYLOAD_BYTE, QUEUE_DEPTH);
    Serial.println("=================================================");

    WiFi.begin(ssid, password);
    Serial.print("[WiFi] Konek");
    while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
    Serial.printf(" OK (IP %s)\n", WiFi.localIP().toString().c_str());

    sync_ntp();

    set_microros_wifi_transports(ssid, password, agent_ip, agent_port);
    rcl_allocator_t allocator = rcl_get_default_allocator();

    while (rclc_support_init(&support, 0, NULL, &allocator) != RCL_RET_OK) {
        Serial.println("[ROS] Menunggu Micro-ROS Agent...");
        delay(1000);
    }
    if (rclc_node_init_default(&node, NODE_NAME, "", &support) != RCL_RET_OK) {
        Serial.println("[ROS] Gagal init node"); while (true) delay(1000);
    }

    rmw_qos_profile_t qos = rmw_qos_profile_default;
    qos.reliability = RMW_QOS_POLICY_RELIABILITY_BEST_EFFORT;
    qos.history     = RMW_QOS_POLICY_HISTORY_KEEP_LAST;
    qos.depth       = QUEUE_DEPTH;

    rcl_publisher_options_t pub_ops = rcl_publisher_get_default_options();
    pub_ops.qos = qos;

    if (rcl_publisher_init(&publisher, &node,
            ROSIDL_GET_MSG_TYPE_SUPPORT(std_msgs, msg, Int32MultiArray),
            "esp32_counter", &pub_ops) != RCL_RET_OK) {
        Serial.println("[ROS] Gagal init publisher"); while (true) delay(1000);
    }

    msg.data.data     = memory_buffer;
    msg.data.capacity = ARRAY_SIZE;
    msg.data.size     = ARRAY_SIZE;
    msg.layout.dim.data     = NULL;
    msg.layout.dim.capacity = 0;
    msg.layout.dim.size     = 0;
    msg.layout.data_offset  = 0;
    for (size_t i = 0; i < ARRAY_SIZE; i++) memory_buffer[i] = 0;

    rclc_executor_init(&executor, &support.context, 0, &allocator);
    waktu_sebelumnya = millis();
    Serial.println("[OK] SIAP KIRIM DATA");
}

void loop() {
    rclc_executor_spin_some(&executor, RCL_MS_TO_NS(5));

    if (millis() - waktu_sebelumnya >= PUBLISH_PERIOD_MS) {
        waktu_sebelumnya = millis();
        seq_id++;

        struct timeval tv;
        gettimeofday(&tv, NULL);

        msg.data.data[0] = seq_id;
        msg.data.data[1] = (int32_t)tv.tv_sec;
        msg.data.data[2] = (int32_t)(tv.tv_usec / 1000);
        msg.data.data[3] = NODE_ID;

        rcl_ret_t rc = rcl_publish(&publisher, &msg, NULL);
        if (seq_id % 50 == 0) {
            Serial.printf("[%s] paket #%d %s\n", NODE_NAME, seq_id,
                          rc == RCL_RET_OK ? "OK" : "GAGAL");
        }
    }
}
