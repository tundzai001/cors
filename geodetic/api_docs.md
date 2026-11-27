# CORS Backend API Documentation

### `GET` /api/bases/{serial}/rovers
- **File**: devices.py
- **Function**: `get_rovers_for_base`
- **Auth**: Requires Authentication

### `POST` /api/bases/{serial}/rovers
- **File**: devices.py
- **Function**: `create_rover_for_base`
- **Auth**: Requires Authentication

### `GET` /api/devices
- **File**: devices.py
- **Function**: `get_initial_devices`
- **Auth**: Requires Authentication

### `POST` /api/devices/{serial}/auto-deploy-token
- **File**: devices.py
- **Function**: `auto_deploy_device_token`
- **Auth**: Requires Authentication

### `POST` /api/devices/{serial}/command
- **File**: devices.py
- **Function**: `send_generic_command`
- **Auth**: Requires Authentication

### `POST` /api/devices/{serial}/configure-chip
- **File**: devices.py
- **Function**: `configure_chip_endpoint`
- **Auth**: Requires Authentication

### `POST` /api/devices/{serial}/license-bypass
- **File**: devices.py
- **Function**: `set_device_license_bypass`
- **Auth**: Requires Authentication

### `PUT` /api/rovers/{rover_id}
- **File**: devices.py
- **Function**: `update_rover`
- **Auth**: Requires Authentication

### `DELETE` /api/rovers/{rover_id}
- **File**: devices.py
- **Function**: `delete_rover`
- **Auth**: Requires Authentication

### `GET` /api/activity/feed
- **File**: main.py
- **Function**: `get_activity_feed`
- **Auth**: Requires Authentication

### `GET` /api/activity/heatmap
- **File**: main.py
- **Function**: `get_heatmap_data`
- **Auth**: Requires Authentication

### `GET` /api/admin/caster-sources
- **File**: main.py
- **Function**: `get_admin_caster_sources`
- **Auth**: Requires Authentication
- **Description**: ✅ ADMIN ONLY: Fetch all available mountpoints/sources from NTRIP Caster(s).

### `POST` /api/admin/caster-sources
- **File**: main.py
- **Function**: `create_admin_caster_source`
- **Auth**: Requires Authentication

### `DELETE` /api/admin/caster-sources
- **File**: main.py
- **Function**: `delete_admin_caster_source`
- **Auth**: Requires Authentication

### `POST` /api/admin/security/block-ip
- **File**: main.py
- **Function**: `block_ip_manually`
- **Auth**: Requires Authentication
- **Description**: Admin block thủ công một IP (lưu vào database).

### `GET` /api/admin/security/stats
- **File**: main.py
- **Function**: `get_security_stats`
- **Auth**: Requires Authentication
- **Description**: Enhanced security stats với error handling

### `POST` /api/admin/security/unblock-ip
- **File**: main.py
- **Function**: `unblock_ip`
- **Auth**: Requires Authentication
- **Description**: Admin unblock một IP (thao tác trên database).

### `GET` /api/admin/server-logs
- **File**: main.py
- **Function**: `get_server_logs`
- **Auth**: Requires Authentication
- **Description**: Đọc N dòng cuối cùng từ file app.log để hiển thị lên UI.

### `GET` /api/admin/settings
- **File**: main.py
- **Function**: `get_system_settings`
- **Auth**: Requires Authentication
- **Description**: Lấy trạng thái hiện tại của các cài đặt bảo mật.

### `POST` /api/admin/settings
- **File**: main.py
- **Function**: `update_system_settings`
- **Auth**: Requires Authentication
- **Description**: Cập nhật cài đặt bảo mật.

### `POST` /api/auth/login
- **File**: main.py
- **Function**: `login`

### `GET` /api/auth/me
- **File**: main.py
- **Function**: `get_current_user_info`
- **Auth**: Requires Authentication

### `POST` /api/caster/block-user
- **File**: main.py
- **Function**: `block_caster_user`
- **Auth**: Requires Authentication
- **Description**: Chặn rover trên hệ thống:

### `GET` /api/caster/blocklist
- **File**: main.py
- **Function**: `get_caster_blocklist`
- **Auth**: Requires Authentication
- **Description**: Fetch all active block rules.

### `GET` /api/caster/clienttable
- **File**: main.py
- **Function**: `get_caster_clienttable`
- **Auth**: Requires Authentication
- **Description**: Aggregate clienttable from all scanned casters to show on map.

### `GET` /api/caster/config
- **File**: main.py
- **Function**: `get_caster_config`
- **Auth**: Requires Authentication
- **Description**: Return the list of configured caster hosts and system version.

### `POST` /api/caster/unblock-user
- **File**: main.py
- **Function**: `unblock_caster_user`
- **Auth**: Requires Authentication
- **Description**: Mở khóa rover:

### `GET` /api/caster/users
- **File**: main.py
- **Function**: `get_caster_users`
- **Auth**: Requires Authentication
- **Description**: Aggregate total users from all scanned casters with enrichment.

### `DELETE` /api/caster/users/{username}
- **File**: main.py
- **Function**: `delete_caster_user_endpoint`
- **Auth**: Requires Authentication

### `GET` /api/device-config-templates
- **File**: main.py
- **Function**: `get_device_config_templates`
- **Auth**: Requires Authentication

### `POST` /api/device-config-templates
- **File**: main.py
- **Function**: `create_device_config_template`
- **Auth**: Requires Authentication

### `GET` /api/devices/alerts
- **File**: main.py
- **Function**: `get_device_alerts`
- **Auth**: Requires Authentication

### `POST` /api/devices/apply-template
- **File**: main.py
- **Function**: `apply_device_config_template`
- **Auth**: Requires Authentication

### `POST` /api/devices/bulk-action
- **File**: main.py
- **Function**: `bulk_device_action`
- **Auth**: Requires Authentication

### `GET` /api/devices/export/csv
- **File**: main.py
- **Function**: `export_devices_to_csv`
- **Auth**: Requires Authentication

### `DELETE` /api/devices/{serial}
- **File**: main.py
- **Function**: `delete_device_from_list`
- **Auth**: Requires Authentication

### `POST` /api/devices/{serial}/configure-chip
- **File**: main.py
- **Function**: `configure_chip_endpoint`
- **Auth**: Requires Authentication

### `POST` /api/devices/{serial}/configure-service
- **File**: main.py
- **Function**: `configure_service_endpoint`
- **Auth**: Requires Authentication

### `GET` /api/devices/{serial}/history
- **File**: main.py
- **Function**: `get_device_history`
- **Auth**: Requires Authentication

### `POST` /api/devices/{serial}/lock
- **File**: main.py
- **Function**: `lock_device`
- **Auth**: Requires Authentication

### `POST` /api/devices/{serial}/reset
- **File**: main.py
- **Function**: `reset_pi_device`
- **Auth**: Requires Authentication

### `POST` /api/devices/{serial}/unlock
- **File**: main.py
- **Function**: `unlock_device`
- **Auth**: Requires Authentication

### `GET` /api/docs
- **File**: main.py
- **Function**: `serve_spa`

### `GET` /api/health
- **File**: main.py
- **Function**: `simple_health_check`
- **Description**: Simple health check cho load balancer

### `GET` /api/health/detailed
- **File**: main.py
- **Function**: `detailed_health_check`
- **Auth**: Requires Authentication
- **Description**: Enhanced health check với system metrics cho monitoring dashboard

### `GET` /api/health/errors
- **File**: main.py
- **Function**: `recent_errors`
- **Auth**: Requires Authentication
- **Description**: Lấy danh sách lỗi gần đây

### `POST` /api/license/pi
- **File**: main.py
- **Function**: `generate_pi_license`
- **Auth**: Requires Authentication

### `GET` /api/metrics/buffer
- **File**: main.py
- **Function**: `buffer_metrics_endpoint`
- **Auth**: Requires Authentication
- **Description**: Buffer and flush performance metrics for realtime monitoring

### `GET` /api/ntrip/auth-check
- **File**: main.py
- **Function**: `ntrip_auth_check`
- **Description**: Endpoint cho NTRIP Caster gọi để kiểm tra xem user có bị chặn không.

### `GET` /api/ntrip/wake-listen
- **File**: main.py
- **Function**: `ntrip_wake_listen_get`
- **Description**: Endpoint cho NTRIP gửi lệnh bật trạm qua HTTP GET.

### `GET` /api/ntrip/wake-station
- **File**: main.py
- **Function**: `ntrip_wake_listen_get`
- **Description**: Endpoint cho NTRIP gửi lệnh bật trạm qua HTTP GET.

### `POST` /api/ntrip/wake-station
- **File**: main.py
- **Function**: `ntrip_wake_station`
- **Description**: Endpoint cho NTRIP gửi lệnh bật trạm qua HTTP POST.

### `GET` /api/ops/alerts
- **File**: main.py
- **Function**: `get_ops_alerts`
- **Auth**: Requires Authentication

### `POST` /api/ops/alerts/{alert_id}/ack
- **File**: main.py
- **Function**: `acknowledge_ops_alert`
- **Auth**: Requires Authentication

### `POST` /api/ops/alerts/{alert_id}/resolve
- **File**: main.py
- **Function**: `resolve_ops_alert`
- **Auth**: Requires Authentication

### `GET` /api/ops/events
- **File**: main.py
- **Function**: `get_ops_events`
- **Auth**: Requires Authentication

### `GET` /api/ops/summary
- **File**: main.py
- **Function**: `get_ops_summary`
- **Auth**: Requires Authentication

### `GET` /api/public/map-stations
- **File**: main.py
- **Function**: `get_public_map_stations`

### `GET` /api/public/network-summary
- **File**: main.py
- **Function**: `get_public_network_summary`

### `GET` /api/rovers/all
- **File**: main.py
- **Function**: `get_all_rovers_global`
- **Auth**: Requires Authentication

### `POST` /api/rovers/bulk-renew
- **File**: main.py
- **Function**: `bulk_renew_rovers`
- **Auth**: Requires Authentication

### `POST` /api/rovers/create-multi
- **File**: main.py
- **Function**: `create_multi_base_rover`
- **Auth**: Requires Authentication

### `GET` /api/rovers/export/csv
- **File**: main.py
- **Function**: `export_rovers_csv`
- **Auth**: Requires Authentication

### `POST` /api/rovers/sync-status
- **File**: main.py
- **Function**: `sync_rover_status`
- **Auth**: Requires Authentication

### `PUT` /api/rovers/sync/by-username/{username}
- **File**: main.py
- **Function**: `sync_rover_by_username`
- **Auth**: Requires Authentication
- **Description**: Thông minh: Đồng bộ rover bằng username. 

### `GET` /api/rovers/validate
- **File**: main.py
- **Function**: `validate_rover_username`
- **Auth**: Requires Authentication

### `GET` /api/rovers/{username}/history
- **File**: main.py
- **Function**: `get_rover_history`
- **Auth**: Requires Authentication

### `POST` /api/rtcm/ntrip/start
- **File**: main.py
- **Function**: `ntrip_start_forward`
- **Auth**: Requires Authentication

### `POST` /api/rtcm/ntrip/stop
- **File**: main.py
- **Function**: `ntrip_stop_forward`
- **Auth**: Requires Authentication

### `DELETE` /api/rtcm/sessions/{session_id}
- **File**: main.py
- **Function**: `release_rtcm_session`
- **Auth**: Requires Authentication

### `POST` /api/rtcm/sessions/{session_id}/heartbeat
- **File**: main.py
- **Function**: `heartbeat_rtcm_session`
- **Auth**: Requires Authentication

### `GET` /api/rtcm/stations/active
- **File**: main.py
- **Function**: `get_active_rtcm_stations`
- **Auth**: Requires Authentication

### `POST` /api/rtcm/stations/{serial}/activate
- **File**: main.py
- **Function**: `activate_rtcm_station`
- **Auth**: Requires Authentication

### `GET` /blog
- **File**: main.py
- **Function**: `get_public_pages`
- **Description**: Catch-all for other public SEO pages to serve index.html.

### `GET` /blog
- **File**: main.py
- **Function**: `serve_spa`

### `GET` /blog/{rest_of_path:path}
- **File**: main.py
- **Function**: `serve_spa`

### `GET` /blog/{slug}
- **File**: main.py
- **Function**: `get_blog_post_seo`
- **Description**: Renders index.html with server-side SEO for individual blog posts.

### `GET` /coverage
- **File**: main.py
- **Function**: `get_public_pages`
- **Description**: Catch-all for other public SEO pages to serve index.html.

### `GET` /coverage
- **File**: main.py
- **Function**: `serve_spa`

### `GET` /health
- **File**: main.py
- **Function**: `simple_health_check`
- **Description**: Simple health check cho load balancer

### `GET` /login
- **File**: main.py
- **Function**: `serve_login`

### `GET` /monitoring
- **File**: main.py
- **Function**: `serve_monitoring`

### `GET` /network-status
- **File**: main.py
- **Function**: `get_public_pages`
- **Description**: Catch-all for other public SEO pages to serve index.html.

### `GET` /network-status
- **File**: main.py
- **Function**: `serve_spa`

### `GET` /operations
- **File**: main.py
- **Function**: `serve_operations`

### `GET` /rovers-global
- **File**: main.py
- **Function**: `serve_rovers_global`

### `GET` /security
- **File**: main.py
- **Function**: `serve_security`

### `GET` /sitemap.xml
- **File**: main.py
- **Function**: `get_dynamic_sitemap`
- **Description**: Generates a dynamic XML sitemap listing the homepage and all active CORS stations.

### `GET` /station/{rest_of_path:path}
- **File**: main.py
- **Function**: `serve_spa`

### `GET` /station/{serial}
- **File**: main.py
- **Function**: `get_station_seo_page`
- **Description**: ENTERPRISE SEO ENGINE: Renders a high-performance, crawler-optimized page

### `GET` /users
- **File**: main.py
- **Function**: `serve_users`

### `WEBSOCKET` /ws/pi/{serial}
- **File**: main.py
- **Function**: `pi_websocket_endpoint`

### `WEBSOCKET` /ws/track-visitors
- **File**: main.py
- **Function**: `track_visitors_endpoint`

### `WEBSOCKET` /ws/updates
- **File**: main.py
- **Function**: `ui_websocket_endpoint`

### `PUT` /api/devices/{serial}/name
- **File**: users.py
- **Function**: `rename_device`
- **Auth**: Requires Authentication

### `PUT` /api/devices/{serial}/serial
- **File**: users.py
- **Function**: `rename_device_serial`
- **Auth**: Requires Authentication

### `GET` /api/users
- **File**: users.py
- **Function**: `get_users`
- **Auth**: Requires Authentication

### `POST` /api/users
- **File**: users.py
- **Function**: `create_user`
- **Auth**: Requires Authentication

### `GET` /api/users/{user_id}
- **File**: users.py
- **Function**: `get_user_details`
- **Auth**: Requires Authentication

### `PUT` /api/users/{user_id}
- **File**: users.py
- **Function**: `update_user`
- **Auth**: Requires Authentication

### `DELETE` /api/users/{user_id}
- **File**: users.py
- **Function**: `delete_user`
- **Auth**: Requires Authentication

