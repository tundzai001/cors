import re
import os

file_path = 'c:/Users/len/Desktop/other_projects/cors/geodetic/agent_test.py'

# Read with UTF-8
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Pattern with flexible whitespace to be safe
pattern = r'    def update_service_config\(self, cfg: dict\):\s+# Never auto-enable RTCM push from config deployment\.\s+# Stream can only be enabled by SET_RTCM_STREAM_ACTIVE command\.\s+cfg\[\'stream_on_demand\'\] = True\s+cfg\[\'stream_active\'\] = False\s+cfg\.pop\(\'active_mountpoint\', None\)\s+self\.config\[\'services\'\] = cfg\s+self\.rtcm_stream_active = False\s+self\.save_config\(\)'

replacement = """    def update_service_config(self, cfg: dict):
        # Respect user settings from dashboard deployment
        self.config['services'] = cfg
        
        # Sync current engine state
        # Default to False if not provided in payload
        stream_active = bool(cfg.get('stream_active', False))
        self.rtcm_stream_active = stream_active
        
        global rtcm_stream_active_flag
        rtcm_stream_active_flag = stream_active
        
        self.save_config()"""

new_content = re.sub(pattern, replacement, content, flags=re.MULTILINE)

# If regex failed, try a simpler replacement since we know the content
if new_content == content:
    print("Regex failed, trying simple string replace...")
    old_block = """    def update_service_config(self, cfg: dict):
        # Never auto-enable RTCM push from config deployment.
        # Stream can only be enabled by SET_RTCM_STREAM_ACTIVE command.
        cfg['stream_on_demand'] = True
        cfg['stream_active'] = False
        cfg.pop('active_mountpoint', None)
        self.config['services'] = cfg
        self.rtcm_stream_active = False
        self.save_config()"""
    new_content = content.replace(old_block, replacement)

if new_content != content:
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    print("Successfully updated agent_test.py")
else:
    print("Failed to replace content in agent_test.py")
