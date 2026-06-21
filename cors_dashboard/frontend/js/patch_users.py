import sys

with open(r'c:\Users\len\Desktop\other_projects\cors\cors_dashboard\frontend\js\users.js', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. userPermissionSection definition
content = content.replace(
    "const roverPermissionSection = document.getElementById('rover-permission-section');",
    "const roverPermissionSection = document.getElementById('rover-permission-section');\n    const userPermissionSection = document.getElementById('user-permission-section');"
)

# 2. updateRoleSections function
old_update = '''    function updateRoleSections() {
        const show = shouldShowAccessSections(roleSelect.value);
        deviceAssignmentSection.classList.toggle('d-none', !show);
        extraMountpointsSection.classList.toggle('d-none', !show);
        roverPermissionSection.classList.toggle('d-none', !show);
    }'''

new_update = '''    function updateRoleSections() {
        const show = shouldShowAccessSections(roleSelect.value);
        deviceAssignmentSection.classList.toggle('d-none', !show);
        extraMountpointsSection.classList.toggle('d-none', !show);
        
        // Only Admin can see permission sections
        const isAdmin = currentUser.role === 'admin';
        roverPermissionSection.classList.toggle('d-none', !(show && isAdmin));
        if (userPermissionSection) {
            userPermissionSection.classList.toggle('d-none', !(show && isAdmin));
        }
    }'''
content = content.replace(old_update, new_update)

# 3. openEditUser populating fields
old_populate = '''        document.getElementById('is_active').checked = !!user.is_active;
        document.getElementById('can_create_rovers').checked = !!user.can_create_rovers;
        document.getElementById('password').value = '';'''
new_populate = '''        document.getElementById('is_active').checked = !!user.is_active;
        document.getElementById('can_create_rovers').checked = !!user.can_create_rovers;
        if (document.getElementById('can_create_users')) {
            document.getElementById('can_create_users').checked = !!user.can_create_users;
        }
        document.getElementById('password').value = '';'''
content = content.replace(old_populate, new_populate)

# 4. saveUser payload
old_payload = '''            is_active: document.getElementById('is_active').checked,
            can_create_rovers: document.getElementById('can_create_rovers').checked,
            assigned_devices: Array.from(document.querySelectorAll('.device-checkbox:checked')).map(input => input.value),'''
new_payload = '''            is_active: document.getElementById('is_active').checked,
            can_create_rovers: document.getElementById('can_create_rovers').checked,
            can_create_users: document.getElementById('can_create_users') ? document.getElementById('can_create_users').checked : false,
            assigned_devices: Array.from(document.querySelectorAll('.device-checkbox:checked')).map(input => input.value),'''
content = content.replace(old_payload, new_payload)

# 5. Role lockdown for non-admin
old_reset = '''    function resetForm() {
        userForm.reset();
        document.getElementById('user-id').value = '';'''
new_reset = '''    function resetForm() {
        userForm.reset();
        document.getElementById('user-id').value = '';
        if (currentUser.role !== 'admin') {
            roleSelect.value = 'viewer';
            roleSelect.disabled = true;
        } else {
            roleSelect.disabled = false;
        }'''
content = content.replace(old_reset, new_reset)

old_edit_user = '''        document.getElementById('role').value = user.role || 'viewer';
        document.getElementById('is_active').checked = !!user.is_active;'''
new_edit_user = '''        document.getElementById('role').value = user.role || 'viewer';
        if (currentUser.role !== 'admin') {
            roleSelect.disabled = true;
        } else {
            roleSelect.disabled = false;
        }
        document.getElementById('is_active').checked = !!user.is_active;'''
content = content.replace(old_edit_user, new_edit_user)

# 6. saveUser roleSelect might be disabled, so we need to get value correctly or just keep it
old_payload_role = '''            role: document.getElementById('role').value,'''
new_payload_role = '''            role: document.getElementById('role').value || 'viewer','''
content = content.replace(old_payload_role, new_payload_role)

with open(r'c:\Users\len\Desktop\other_projects\cors\cors_dashboard\frontend\js\users.js', 'w', encoding='utf-8') as f:
    f.write(content)
