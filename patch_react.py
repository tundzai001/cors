
import os

file_path = r'c:\Users\len\Desktop\other_projects\cors\cors_dashboard\frontend\js\react-components.js'

with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
for i, line in enumerate(lines):
    # DeviceListItem chip badge
    if '<span className="badge">{device.detected_chip_type || \'UNK\'}</span>' in line:
        indent = line[:line.find('<span')]
        new_lines.append(line)
        new_lines.append(f"{indent}{{device.is_lte && (\n")
        new_lines.append(f"{indent}    <span className=\"badge bg-primary ms-1\" title=\"Kết nối 4G/LTE\">\n")
        new_lines.append(f"{indent}        <i className=\"bi bi-reception-4 me-1\"></i>4G\n")
        new_lines.append(f"{indent}    </span>\n")
        new_lines.append(f"{indent})}}\n")
    # DetailsPanel status badges group
    elif i > 3480 and i < 3500 and '<div className="d-flex align-items-center">' in line and 'badge' not in lines[i-1]:
        indent = line[:line.find('<div')]
        new_lines.append(line)
        new_lines.append(f"{indent}    {{device.is_lte && (\n")
        new_lines.append(f"{indent}        <span className=\"badge bg-primary me-2\" title=\"Trạm kết nối qua 4G/LTE\">\n")
        new_lines.append(f"{indent}            <i className=\"bi bi-reception-4 me-1\"></i>LTE\n")
        new_lines.append(f"{indent}        </span>\n")
        new_lines.append(f"{indent})}}\n")
    else:
        new_lines.append(line)

with open(file_path, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print("Successfully patched react-components.js")
