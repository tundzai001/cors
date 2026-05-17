
import os

file_path = r'c:\Users\len\Desktop\other_projects\cors\cors_dashboard\backend\app\main.py'

with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
skip = 0
for i, line in enumerate(lines):
    if skip > 0:
        skip -= 1
        continue
    
    # Check for Survey-In block
    if 'commands_to_send = command_builder.build_base_survey_in_command(' in line and 'survey_accuracy' in lines[min(i+1, len(lines)-1)]:
        indent = line[:line.find('commands_to_send')]
        new_lines.append(f"{indent}if sensor_type == 'Unicorecomm':\n")
        new_lines.append(f"{indent}    commands_to_send = command_builder.build_geotek_lte_unicore_config(\n")
        new_lines.append(f"{indent}        setup_method='SURVEY_IN', duration=survey_duration\n")
        new_lines.append(f"{indent}    )\n")
        new_lines.append(f"{indent}else:\n")
        new_lines.append(f"{indent}    commands_to_send = command_builder.build_base_survey_in_command(\n")
        new_lines.append(f"{indent}        sensor_type, survey_duration, survey_accuracy\n")
        new_lines.append(f"{indent}    )\n")
        skip = 2 # Skip the next 2 lines of the old call
    # Check for Fixed LLA block
    elif 'commands_to_send = command_builder.build_base_fixed_lla_command(' in line and 'accuracy' in lines[min(i+1, len(lines)-1)]:
        indent = line[:line.find('commands_to_send')]
        new_lines.append(f"{indent}if sensor_type == 'Unicorecomm':\n")
        new_lines.append(f"{indent}    commands_to_send = command_builder.build_geotek_lte_unicore_config(\n")
        new_lines.append(f"{indent}        setup_method='FIXED_LLA', lat=coords['lat'], lon=coords['lon'], alt=coords['alt']\n")
        new_lines.append(f"{indent}    )\n")
        new_lines.append(f"{indent}else:\n")
        new_lines.append(f"{indent}    commands_to_send = command_builder.build_base_fixed_lla_command(\n")
        new_lines.append(f"{indent}        sensor_type, coords['lat'], coords['lon'], coords['alt'], accuracy\n")
        new_lines.append(f"{indent}    )\n")
        skip = 2 # Skip the next 2 lines of the old call
    else:
        new_lines.append(line)

with open(file_path, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print("Successfully patched main.py")
