import sys
import os

MAIN_FILE = r'c:\Users\len\Desktop\other_projects\cors\cors_dashboard\backend\app\main.py'

with open(MAIN_FILE, 'r', encoding='utf-8') as f:
    main_content = f.read()

target = """        # 1. Kiểm tra xem IP có đang bị BAN không
        if client_ip in banned_ips:
            if now < banned_ips[client_ip]:
                # Vẫn đang bị BAN
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Your IP has been temporarily banned due to multiple security violations."}
                )
            else:
                # Đã hết hạn BAN -> Xóa khỏi danh sách để bắt đầu lại (ân xá)
                del banned_ips[client_ip]
                security_rep_scores[client_ip] = 0

        # Helper để ghi nhận vi phạm và thực hiện BAN nếu cần
        def record_violation():
            if client_ip == "unknown": return
            score = security_rep_scores.get(client_ip, 0) + 1
            security_rep_scores[client_ip] = score
            if score >= VIOLATION_THRESHOLD:
                banned_ips[client_ip] = now + BAN_DURATION
                logger.critical(f"🚨 AUTOMATIC BAN: IP {client_ip} has been BANNED for {BAN_DURATION}s after {score} violations.")"""

replacement = """        # 1. Kiểm tra xem IP có đang bị BAN không (Via Redis)
        is_banned = await redis_client.is_ip_banned(client_ip)
        if is_banned:
            return JSONResponse(
                status_code=403,
                content={"detail": "Your IP has been temporarily banned due to multiple security violations."}
            )

        # Helper để ghi nhận vi phạm và thực hiện BAN nếu cần
        async def record_violation():
            if client_ip == "unknown": return
            score = security_rep_scores.get(client_ip, 0) + 1
            security_rep_scores[client_ip] = score
            if score >= VIOLATION_THRESHOLD:
                await redis_client.ban_ip(client_ip)
                # Dọn dẹp score vì đã ban
                security_rep_scores[client_ip] = 0
                logger.critical(f"🚨 AUTOMATIC BAN: IP {client_ip} has been BANNED after {score} violations.")"""

if target in main_content:
    main_content = main_content.replace(target, replacement)
    
    # Need to update call sites of record_violation() to await record_violation()
    main_content = main_content.replace("record_violation()", "await record_violation()")
    # Fix the nested definition calls:
    # "await await record_violation()" might happen if run multiple times, let's just do it directly:
    # Wait, the replace string "record_violation()" might catch the `async def record_violation():`
    # Let's fix that
    main_content = main_content.replace("await async def", "async def")

    with open(MAIN_FILE, 'w', encoding='utf-8') as f:
        f.write(main_content)
    print("Patched SecurityGateMiddleware")
else:
    print("Target not found. Probably different line endings.")
    target_crlf = target.replace('\n', '\r\n')
    if target_crlf in main_content:
        main_content = main_content.replace(target_crlf, replacement.replace('\n', '\r\n'))
        main_content = main_content.replace("record_violation()", "await record_violation()")
        main_content = main_content.replace("await async def", "async def")
        with open(MAIN_FILE, 'w', encoding='utf-8') as f:
            f.write(main_content)
        print("Patched SecurityGateMiddleware (CRLF)")
    else:
        print("Still not found.")
