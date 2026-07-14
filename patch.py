import os

file_path = r"C:\Users\len\Desktop\other_projects\cors\geodetic\geotek_daithanh_aitogy.py"

with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

target1 = """                    if self.mqtt_client and self.mqtt_client.is_connected():
                        self.raw_publish_buffer.extend(data)
                        if time.time() - self.last_raw_publish >= 1.0:
                            try:
                                self.mqtt_client.publish(f"pi/devices/{self.serial}/raw_data", bytes(self.raw_publish_buffer), qos=0)
                            except Exception as e:
                                self.log("WARNING", f"Failed to publish raw_data: {e}")
                            self.raw_publish_buffer.clear()
                            self.last_raw_publish = time.time()"""

target2 = """                    if self.mqtt_status_client and self.mqtt_status_client.is_connected():
                        self.raw_publish_buffer.extend(payload_bytes)
                        if time.time() - self.last_raw_publish >= 1.0:
                            try:
                                self.mqtt_status_client.publish(f"pi/devices/{self.serial}/raw_data", bytes(self.raw_publish_buffer), qos=0)
                            except Exception as e:
                                self.log("WARNING", f"Failed to publish raw_data: {e}")
                            self.raw_publish_buffer.clear()
                            self.last_raw_publish = time.time()"""

replacement1 = """                    # if self.mqtt_client and self.mqtt_client.is_connected():
                    #     self.raw_publish_buffer.extend(data)
                    #     if time.time() - self.last_raw_publish >= 1.0:
                    #         try:
                    #             self.mqtt_client.publish(f"pi/devices/{self.serial}/raw_data", bytes(self.raw_publish_buffer), qos=0)
                    #         except Exception as e:
                    #             self.log("WARNING", f"Failed to publish raw_data: {e}")
                    #         self.raw_publish_buffer.clear()
                    #         self.last_raw_publish = time.time()"""

replacement2 = """                    # if self.mqtt_status_client and self.mqtt_status_client.is_connected():
                    #     self.raw_publish_buffer.extend(payload_bytes)
                    #     if time.time() - self.last_raw_publish >= 1.0:
                    #         try:
                    #             self.mqtt_status_client.publish(f"pi/devices/{self.serial}/raw_data", bytes(self.raw_publish_buffer), qos=0)
                    #         except Exception as e:
                    #             self.log("WARNING", f"Failed to publish raw_data: {e}")
                    #         self.raw_publish_buffer.clear()
                    #         self.last_raw_publish = time.time()"""

if target1 in content:
    content = content.replace(target1, replacement1)
    print("Replaced target1 successfully.")
else:
    print("Could not find target1.")

if target2 in content:
    content = content.replace(target2, replacement2)
    print("Replaced target2 successfully.")
else:
    print("Could not find target2.")

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)
