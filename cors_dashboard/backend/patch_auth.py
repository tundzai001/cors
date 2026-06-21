import sys

filepath = r'c:\Users\len\Desktop\other_projects\cors\cors_dashboard\backend\app\auth.py'

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

old_str = '''def require_permission(permission: str):
    async def permission_checker(current_user: models.User = Depends(get_current_user)) -> models.User:
        user_permissions = ROLE_PERMISSIONS.get(current_user.role, [])
        if permission not in user_permissions:
            logger.warning(f"Permission denied: User '{current_user.username}' (role: {current_user.role}) tried to access '{permission}'")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Bạn không có quyền '{permission}'.")
        return current_user
    return permission_checker

def has_permission(user: models.User, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(user.role, [])

def get_user_permissions(user: models.User) -> list[str]:
    return ROLE_PERMISSIONS.get(user.role, [])'''

new_str = '''def require_permission(permission: str):
    async def permission_checker(current_user: models.User = Depends(get_current_user)) -> models.User:
        user_permissions = get_user_permissions(current_user)
        if permission not in user_permissions:
            logger.warning(f"Permission denied: User '{current_user.username}' (role: {current_user.role}) tried to access '{permission}'")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Bạn không có quyền '{permission}'.")
        return current_user
    return permission_checker

def has_permission(user: models.User, permission: str) -> bool:
    return permission in get_user_permissions(user)

def get_user_permissions(user: models.User) -> list[str]:
    perms = list(ROLE_PERMISSIONS.get(user.role, []))
    if user.role == Role.COORDINATOR and getattr(user, 'can_create_users', False):
        if Permission.MANAGE_USERS not in perms:
            perms.append(Permission.MANAGE_USERS)
    return perms'''

if old_str in content:
    content = content.replace(old_str, new_str)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    print('Patched successfully')
else:
    print('Failed to patch, string not found')
