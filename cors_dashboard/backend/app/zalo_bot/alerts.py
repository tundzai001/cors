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

    def _status_transitions(self, devices, current_statuses):
        newly_offline = []
        newly_online = []
        if not self._status_snapshot_ready:
            return newly_offline, newly_online
        for serial, status in current_statuses.items():
            previous_status = self.prev_statuses.get(serial)
            if not previous_status or previous_status == status:
                continue
            device = next((item for item in devices if item.get("serial") == serial), {})
            if status == "offline":
                newly_offline.append(device)
            elif previous_status == "offline" and status != "offline":
                newly_online.append(device)
        return newly_offline, newly_online

    def _new_alerts(self, alerts):
        current_alerts = {alert.get("id"): alert for alert in alerts}
        current_ids = set(current_alerts)
        if not self._alert_snapshot_ready:
            return current_ids, []
        new_alerts = [current_alerts[alert_id] for alert_id in current_ids - self.prev_alerts]
        return current_ids, new_alerts

    async def _collect_alert_changes(self):
        if not self._get_devices_cb or not self._get_alerts_cb:
            return None
        devices = await self._get_devices_cb()
        alerts = await self._get_alerts_cb()
        current_statuses = {device.get("serial"): device.get("status", "unknown") for device in devices}
        newly_offline, newly_online = self._status_transitions(devices, current_statuses)
        current_alert_ids, new_alerts = self._new_alerts(alerts)
        self.prev_statuses = current_statuses
        self.prev_alerts = current_alert_ids
        self._status_snapshot_ready = True
        self._alert_snapshot_ready = True
        return devices, newly_offline, newly_online, new_alerts

    @staticmethod
    def _notification_details(newly_offline, newly_online, new_alerts):
        severities = {str(alert.get("severity") or "warning").lower() for alert in new_alerts}
        critical_alert = bool(severities.intersection({"critical", "fatal", "error", "high", "severe"}))
        severity = "critical" if newly_offline or critical_alert else "warning" if new_alerts else "info"
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
        return severity, subjects, event_key

    @staticmethod
    def _device_in_scope(device, scope_values):
        return (
            str(device.get("serial") or "").casefold() in scope_values
            or str(device.get("name") or "").casefold() in scope_values
        )

    @staticmethod
    def _alert_in_scope(alert, scope_values):
        keys = ("rover_username", "device_name", "device_serial")
        return any(str(alert.get(key) or "").casefold() in scope_values for key in keys)

    def _filter_target_changes(self, target, changes):
        devices, newly_offline, newly_online, new_alerts = changes
        if target.get("scope_mode") != "selected":
            return devices, newly_offline, newly_online, new_alerts
        scope_values = {
            str(value).strip().casefold() for value in target.get("scope_values") or []
        }
        target_devices = [device for device in devices if self._device_in_scope(device, scope_values)]
        target_offline = [device for device in newly_offline if device in target_devices]
        target_online = [device for device in newly_online if device in target_devices]
        target_alerts = [alert for alert in new_alerts if self._alert_in_scope(alert, scope_values)]
        return target_devices, target_offline, target_online, target_alerts

    async def _send_notification(self, target, text):
        chat_id = target["chat_id"]
        try:
            await self.app.bot.send_message(chat_id=chat_id, text=text)
        except Exception as error:
            logger.exception(f"Failed to send alert to {chat_id}: {error}")
            try:
                await zalo_repository.finish_delivery(target["delivery_id"], False, str(error))
            except Exception as log_error:
                logger.exception(
                    "Failed to record Zalo delivery failure id=%s: %s",
                    target["delivery_id"], log_error,
                )
        else:
            try:
                await zalo_repository.finish_delivery(target["delivery_id"], True)
            except Exception as log_error:
                logger.exception(
                    "Zalo alert sent but delivery log update failed id=%s: %s",
                    target["delivery_id"], log_error,
                )

    async def _dispatch_notifications(self, changes):
        devices, newly_offline, newly_online, new_alerts = changes
        severity, subjects, event_key = self._notification_details(
            newly_offline, newly_online, new_alerts
        )
        binding_scopes = (
            await self._get_binding_scopes_cb() if self._get_binding_scopes_cb else None
        )
        targets = await zalo_repository.reserve_notification_targets(
            event_key, severity, subjects, binding_scopes=binding_scopes,
            bot_key=self.bot_key,
        )
        for target in targets:
            target_devices, target_offline, target_online, target_alerts = self._filter_target_changes(
                target, changes
            )
            if not (target_offline or target_online or target_alerts):
                continue
            text = self._format_system_alert(
                target_offline, target_online, target_alerts, target_devices
            )
            await self._send_notification(target, text)

    async def _run_error_check_cycle(self):
        changes = await self._collect_alert_changes()
        if changes and any(changes[1:]):
            await self._dispatch_notifications(changes)

    async def _error_check_loop(self):
        while True:
            try:
                await self._run_error_check_cycle()
            except Exception as error:
                logger.exception(f"Error in Zalo error_check_loop: {error}")
            await asyncio.sleep(120)
