# ==============================================================================
# == backend/app/mqtt.py - v2.0.0 - Cầu nối MQTT -> WebSocket Hoàn chỉnh      ==
# ==============================================================================
#
# CHỨC NĂNG:
# - Kết nối đến MQTT Broker một cách non-blocking.
# - Lắng nghe (subscribe) các topic dữ liệu từ các thiết bị Pi.
# - Phân loại và xử lý các loại tin nhắn:
#   1. Dữ liệu thô (NMEA)
#   2. Dữ liệu trạng thái (status - JSON)
#   3. Dữ liệu cấu hình (config_state - JSON)
# - Chuyển tiếp (broadcast) dữ liệu đã được xử lý đến các client UI
#   đang kết nối qua WebSocket.

import json
import logging
import asyncio
import os
import paho.mqtt.client as mqtt
from sqlalchemy.ext.asyncio import async_sessionmaker

# Import các module cục bộ cần thiết
from . import crud, schemas
from .websocket import manager
from .database import engine, settings
from . import nmea_parser

# --- KHỞI TẠO CÁC ĐỐI TƯỢỢNG ---
# Tạo một session factory riêng để sử dụng trong luồng MQTT
AsyncSessionLocal_MQTT = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

# Biến toàn cục để giữ tham chiếu đến event loop của FastAPI
main_loop = None

# --- CÁC HÀM XỬ LÝ SỰ KIỆN MQTT ---

def on_connect(client, userdata, flags, rc):
    """
    Callback được gọi khi backend kết nối thành công đến MQTT Broker.
    """
    if rc == 0:
        logging.info("✓ Đã kết nối thành công đến MQTT Broker.")
        # Định nghĩa tất cả các topic cần lắng nghe
        topics_to_subscribe = [
            ("pi/devices/+/status", 1),
            ("pi/devices/+/service_config_state", 1),
            ("pi/devices/+/base_config_state", 1),
            ("pi/devices/+/raw_data", 0)  # Dữ liệu NMEA
        ]
        client.subscribe(topics_to_subscribe)
        logging.info(f"✓ Backend đã lắng nghe các topic cần thiết.")
    else:
        logging.error(f"❌ LỖI: Không thể kết nối đến MQTT Broker, mã lỗi: {rc}")

def on_disconnect(client, userdata, rc, properties=None):
    """
    Callback được gọi khi mất kết nối với MQTT Broker.
    """
    if rc != 0:
        logging.warning(f"MQTT bị ngắt kết nối không mong muốn. RC: {rc}. Tự động kết nối lại...")
    else:
        logging.info("MQTT đã ngắt kết nối bình thường.")

def on_message(client, userdata, msg):
    """
    Callback được gọi MỖI KHI backend nhận được một tin nhắn từ MQTT Broker.
    Hàm này chạy trong thread riêng của Paho-MQTT, nhiệm vụ của nó là
    chuyển việc xử lý sang event loop chính của FastAPI một cách an toàn.
    """
    logging.debug(f"===> Backend nhận được tin nhắn MQTT. Topic: '{msg.topic}'")
    
    if main_loop and main_loop.is_running():
        # Đặt coroutine `handle_message_async` vào event loop để thực thi
        asyncio.run_coroutine_threadsafe(
            handle_message_async(msg.topic, msg.payload), 
            main_loop
        )
    else:
        logging.warning("Event loop chính chưa sẵn sàng để xử lý tin nhắn MQTT.")


async def handle_message_async(topic: str, payload: bytes):
    """
    Hàm async xử lý logic chính của tin nhắn MQTT.
    Được thực thi trong event loop của FastAPI.
    """
    logging.debug(f"--> Bắt đầu xử lý bất đồng bộ cho topic '{topic}'")

    topic_parts = topic.split('/')
    if len(topic_parts) < 4: 
        logging.warning(f"Nhận được topic MQTT không hợp lệ: {topic}")
        return
    
    message_type = topic_parts[-1]
    serial = topic_parts[2]
    
    try:
        # --- ƯU TIÊN 1: Xử lý dữ liệu thô (raw_data / NMEA) trước tiên ---
        # Dữ liệu này không phải là JSON, nên phải xử lý riêng và thoát sớm.
        if message_type == "raw_data":
            try:
                line = payload.decode('ascii', errors='ignore')
                parsed_data = nmea_parser.parse(line)
                if parsed_data:
                    # Gửi dữ liệu đã phân tích đến UI qua WebSocket
                    await manager.broadcast({
                        "type": "nmea_update",
                        "serial": serial,
                        "data": parsed_data
                    })
            except Exception as e:
                logging.error(f"Lỗi khi phân tích dữ liệu NMEA cho '{serial}': {e}")
            return # Quan trọng: Thoát sớm sau khi xử lý raw_data

        # --- ƯU TIÊN 2: Xử lý tất cả các loại tin nhắn dạng JSON ---
        # Nếu không phải raw_data, chúng ta giả định nó là JSON.
        data = json.loads(payload.decode())
        
        # --- Xử lý tin nhắn 'status' ---
        if message_type == "status":
            async with AsyncSessionLocal_MQTT() as session:
                device_obj = await crud.update_or_create_device(session, device_data=data)
                if device_obj:
                    device_schema = schemas.Device.from_orm(device_obj)
                    #logging.info(f"Đang broadcast status_update cho '{serial}' đến {len(manager.active_connections)} UI client(s).")
                    await manager.broadcast({
                        "type": "status_update", 
                        "data": device_schema.model_dump()
                    })
                else:
                    logging.error(f"Không thể cập nhật/tạo thiết bị '{serial}' trong DB từ MQTT.")

        # --- Xử lý tin nhắn 'base_config_state' ---
        elif message_type == "base_config_state":
            await manager.broadcast({
                "type": "base_config_state",
                "serial": serial,
                "data": data
            })

        # --- Xử lý tin nhắn 'service_config_state' ---
        elif message_type == "service_config_state":
            await manager.broadcast({
                "type": "service_config_state", 
                "serial": serial, 
                "data": data
            })

    except json.JSONDecodeError:
        logging.warning(f"Nhận được tin nhắn không phải JSON trên topic '{topic}'. Payload: {payload[:50]}...")
    except Exception as e:
        logging.error(f"Lỗi nghiêm trọng khi xử lý tin nhắn MQTT từ topic '{topic}':", exc_info=True)

# --- KHỞI TẠO VÀ CẤU HÌNH MQTT CLIENT ---

# Tạo client ID duy nhất để tránh xung đột
client_id = f"backend-client-{os.getpid()}"

# Xử lý tương thích với các phiên bản Paho-MQTT khác nhau
if hasattr(mqtt, 'CallbackAPIVersion'):
    mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id)
else:
    mqtt_client = mqtt.Client(client_id)

# Gán các hàm callback
mqtt_client.on_connect = on_connect
mqtt_client.on_disconnect = on_disconnect
mqtt_client.on_message = on_message

# Cấu hình tự động kết nối lại
mqtt_client.reconnect_delay_set(min_delay=1, max_delay=120)

# --- CÁC HÀM ĐIỀU KHIỂN VÒNG LẶP MQTT ---

def start_mqtt_loop():
    """
    Khởi động MQTT client một cách NON-BLOCKING.
    Hàm này được gọi một lần khi FastAPI khởi động.
    """
    global main_loop
    
    try:
        main_loop = asyncio.get_running_loop()
        logging.info("✓ Đã lấy được event loop của FastAPI để đồng bộ hóa MQTT.")
    except RuntimeError:
        logging.warning("⚠ Không có event loop đang chạy, MQTT sẽ chạy trong thread riêng mà không đồng bộ.")
        main_loop = None
    
    try:
        logging.info(f"Đang kết nối đến MQTT Broker: {settings.MQTT_HOST}:{settings.MQTT_PORT}...")
        
        # Sử dụng connect_async() để không làm block event loop của FastAPI
        mqtt_client.connect_async(
            settings.MQTT_HOST, 
            settings.MQTT_PORT, 
            keepalive=60
        )
        
        # Bắt đầu network loop trong một thread riêng do Paho-MQTT quản lý
        mqtt_client.loop_start()
        logging.info("✓ Vòng lặp mạng MQTT đã được khởi động trong background.")
        
    except Exception as e:
        logging.error(f"!!! Lỗi nghiêm trọng khi khởi động MQTT: {e}")
        logging.error("Backend sẽ tiếp tục chạy nhưng chức năng MQTT sẽ không hoạt động.")

def stop_mqtt_loop():
    """
    Dừng MQTT client một cách an toàn.
    Hàm này được gọi khi FastAPI tắt.
    """
    logging.info("Đang dừng kết nối MQTT...")
    try:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        logging.info("✓ Kết nối MQTT đã được dừng an toàn.")
    except Exception as e:
        logging.error(f"Lỗi khi dừng MQTT: {e}")

def publish_message(topic: str, payload: str, qos: int = 1):
    """
    Hàm tiện ích để gửi tin nhắn từ backend đến MQTT Broker.
    """
    try:
        if not mqtt_client.is_connected():
            logging.warning(f"Không thể publish vì chưa kết nối MQTT. Topic: {topic}")
            return

        result = mqtt_client.publish(topic, payload, qos=qos)
        
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            logging.debug(f"✓ Đã publish thành công đến topic '{topic}'")
        else:
            logging.error(f"!!! Lỗi khi publish đến topic '{topic}': {mqtt.error_string(result.rc)}")
            
    except Exception as e:
        logging.error(f"Ngoại lệ khi publish tin nhắn MQTT: {e}", exc_info=True)