"""Alerts behavior for the Zalo service."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import List
from ..zalo_assistant import payload_fingerprint, zalo_repository

logger = logging.getLogger("app.zalo_bot_service")


class AlertsMixin:
    @staticmethod
    def _format_system_alert(
        newly_offline: List[dict], newly_online: List[dict],
        new_alerts: List[dict], visible_devices: List[dict],
    ) -> str:
        now_str = datetime.now().strftime("%H:%M %d/%m/%Y")
        lines = [f"🚨 CẢNH BÁO HỆ THỐNG — {now_str}", "━━━━━━━━━━━━━━━━━━━━━━"]
        if newly_offline:
            lines.extend(("", f"❌ MỚI OFFLINE ({len(newly_offline)}):"))
            lines.extend(f"• {item.get('name') or 'Trạm chưa đặt tên'}" for item in newly_offline)
        if new_alerts:
            lines.extend(("", f"⚠️ LỖI MỚI ({len(new_alerts)}):"))
            for item in new_alerts:
                name = item.get("rover_username") or item.get("device_name") or item.get("device_serial") or "Hệ thống"
                lines.append(f"• {name}: {item.get('title') or item.get('message') or 'Cảnh báo'}")
        if newly_online:
            lines.extend(("", f"✅ PHỤC HỒI ({len(newly_online)}):"))
            lines.extend(f"• {item.get('name') or 'Trạm chưa đặt tên'} đã online trở lại" for item in newly_online)
        online = sum(
            1 for item in visible_devices
            if str(item.get("status") or "unknown").lower() not in {"offline", "unknown"}
        )
        offline = sum(
            1 for item in visible_devices
            if str(item.get("status") or "unknown").lower() == "offline"
        )
        lines.extend(("", "━━━━━━━━━━━━━━━━━━━━━━", f"📊 {online} online | {offline} offline"))
        return "\n".join(lines)

    async def _error_check_loop(self):
        while True:
            try:
                if not self._get_devices_cb or not self._get_alerts_cb:
                    await asyncio.sleep(120)
                    continue

                devices = await self._get_devices_cb()
                alerts = await self._get_alerts_cb()

                # Detect changes
                newly_offline = []
                newly_online = []
                new_alerts = []

                current_statuses = {d.get("serial"): d.get("status", "unknown") for d in devices}
                current_alerts_map = {a.get("id"): a for a in alerts}
                current_alerts_ids = set(current_alerts_map.keys())

                if self._status_snapshot_ready:
                    for serial, status in current_statuses.items():
                        prev_status = self.prev_statuses.get(serial)
                        if prev_status and prev_status != status:
                            if status == "offline":
                                newly_offline.append(next((d for d in devices if d.get("serial") == serial), {}))
                            elif prev_status == "offline" and status != "offline":
                                newly_online.append(next((d for d in devices if d.get("serial") == serial), {}))

                if self._alert_snapshot_ready:
                    new_alert_ids = current_alerts_ids - self.prev_alerts
                    for aid in new_alert_ids:
                        new_alerts.append(current_alerts_map[aid])

                self.prev_statuses = current_statuses
                self.prev_alerts = current_alerts_ids
                self._status_snapshot_ready = True
                self._alert_snapshot_ready = True

                if newly_offline or newly_online or new_alerts:
                    alert_severities = {
                        str(alert.get("severity") or "warning").lower() for alert in new_alerts
                    }
                    critical_alert = bool(alert_severities.intersection({
                        "critical", "fatal", "error", "high", "severe",
                    }))
                    severity = (
                        "critical" if newly_offline or critical_alert
                        else "warning" if new_alerts else "info"
                    )
                    subjects = set()
                    for device in newly_offline + newly_online:
                        subjects.update(filter(None, (device.get("name"), device.get("serial"))))
                    for alert in new_alerts:
                        subjects.update(filter(None, (
                            alert.get("rover_username"), alert.get("device_name"), alert.get("device_serial")
                        )))
                    event_key = payload_fingerprint({
                        "window": int(time.time() // 120),
                        "offline": sorted(str(item.get("serial")) for item in newly_offline),
                        "online": sorted(str(item.get("serial")) for item in newly_online),
                        "alerts": sorted(str(item.get("id")) for item in new_alerts),
                    })
                    binding_scopes = (
                        await self._get_binding_scopes_cb() if self._get_binding_scopes_cb else None
                    )
                    targets = await zalo_repository.reserve_notification_targets(
                        event_key, severity, subjects, binding_scopes=binding_scopes,
                    )
                    for target in targets:
                        chat_id = target["chat_id"]
                        scope_values = {
                            str(value).strip().casefold() for value in target.get("scope_values") or []
                        }
                        if target.get("scope_mode") == "selected":
                            target_devices = [
                                item for item in devices
                                if str(item.get("serial") or "").casefold() in scope_values
                                or str(item.get("name") or "").casefold() in scope_values
                            ]
                            target_offline = [item for item in newly_offline if item in target_devices]
                            target_online = [item for item in newly_online if item in target_devices]
                            target_alerts = [
                                item for item in new_alerts
                                if any(
                                    str(item.get(key) or "").casefold() in scope_values
                                    for key in ("rover_username", "device_name", "device_serial")
                                )
                            ]
                        else:
                            target_devices = devices
                            target_offline = newly_offline
                            target_online = newly_online
                            target_alerts = new_alerts
                        if not (target_offline or target_online or target_alerts):
                            continue
                        text = self._format_system_alert(
                            target_offline, target_online, target_alerts, target_devices
                        )
                        try:
                            await self.app.bot.send_message(chat_id=chat_id, text=text)
                        except Exception as e:
                            logger.error(f"Failed to send alert to {chat_id}: {e}")
                            try:
                                await zalo_repository.finish_delivery(target["delivery_id"], False, str(e))
                            except Exception as log_exc:
                                logger.error("Failed to record Zalo delivery failure id=%s: %s", target["delivery_id"], log_exc)
                        else:
                            try:
                                await zalo_repository.finish_delivery(target["delivery_id"], True)
                            except Exception as log_exc:
                                logger.error("Zalo alert sent but delivery log update failed id=%s: %s", target["delivery_id"], log_exc)

            except Exception as e:
                logger.error(f"Error in Zalo error_check_loop: {e}")

            await asyncio.sleep(120)
