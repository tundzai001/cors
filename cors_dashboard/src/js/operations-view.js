// Static Operations layout. Live data is populated by operations.js.
function OperationsView() {
    return (
        <>
    <nav className="navbar navbar-expand-lg sticky-top ops-admin-nav">
        <div className="container-fluid">
            <a className="navbar-brand fw-bold text-light" href="/">
                <i className="bi bi-activity me-2"></i>Trung tâm điều khiển CORS
            </a>
            <button className="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navbarNav">
                <span className="navbar-toggler-icon"></span>
            </button>
            <div className="collapse navbar-collapse" id="navbarNav">
                <ul id="main-nav" className="navbar-nav me-auto mb-2 mb-lg-0"></ul>
            </div>
            <div className="d-flex align-items-center">
                <span id="user-info" className="navbar-text me-3 d-none d-md-inline"></span>
                <button id="logout-btn" className="btn btn-sm btn-outline-danger">
                    <i className="bi bi-box-arrow-right"></i><span className="d-none d-md-inline ms-1">Đăng xuất</span>
                </button>
            </div>
        </div>
    </nav>

    <main className="container-fluid ops-center-shell">
        <header className="ops-command-header">
            <div className="ops-command-heading">
                <span className="ops-kicker"><span className="ops-live-dot"></span> GIÁM SÁT HỆ THỐNG</span>
                <h1>Trung tâm điều hành</h1>
                <p>Theo dõi trạng thái mạng CORS và xử lý tác vụ tại một nơi.</p>
            </div>
            <div className="ops-command-actions">
                <span id="ops-last-updated" className="ops-last-updated" aria-live="polite">Đang cập nhật...</span>
                <button className="btn btn-outline-info ops-btn" id="ops-refresh-btn" type="button">
                    <i className="bi bi-arrow-clockwise me-2" aria-hidden="true"></i>Làm mới
                </button>
            </div>
        </header>

        <section className="ops-center-grid" aria-label="Tổng quan hệ thống">
            <article className="ops-card ops-center-stat ops-stat-devices">
                <div className="ops-stat-heading"><span>Trạm</span><i className="bi bi-broadcast-pin" aria-hidden="true"></i></div>
                <strong id="ops-stat-devices">--</strong>
                <small id="ops-stat-devices-meta">Đang tải</small>
            </article>
            <article className="ops-card ops-center-stat ops-stat-rovers">
                <div className="ops-stat-heading"><span>Rover</span><i className="bi bi-hdd-network" aria-hidden="true"></i></div>
                <strong id="ops-stat-rovers">--</strong>
                <small id="ops-stat-rovers-meta">Đang tải</small>
            </article>
            <article className="ops-card ops-center-stat ops-stat-alerts">
                <div className="ops-stat-heading"><span>Cảnh báo</span><i className="bi bi-exclamation-triangle" aria-hidden="true"></i></div>
                <strong id="ops-stat-alerts">--</strong>
                <small id="ops-stat-alerts-meta">Đang tải</small>
            </article>
            <article className="ops-card ops-center-stat ops-stat-traffic">
                <div className="ops-stat-heading"><span>Lưu lượng</span><i className="bi bi-activity" aria-hidden="true"></i></div>
                <strong id="ops-stat-traffic">--</strong>
                <small id="ops-stat-casters-meta">Đang tải</small>
            </article>
        </section>

        <div className="ops-area-heading">
            <div><span className="ops-area-kicker">01 / THEO DÕI</span><h2>Diễn biến hệ thống</h2></div>
            <p>Cảnh báo cần xử lý và sự kiện mới nhất</p>
        </div>

        <section className="ops-monitor-grid" aria-label="Cảnh báo và nhật ký">
            <div className="ops-card ops-monitor-card">
                    <div className="ops-section-head">
                        <div className="ops-section-title"><i className="bi bi-exclamation-triangle-fill me-2" aria-hidden="true"></i>Cảnh báo <span id="ops-alert-count" className="ops-count-pill">0</span></div>
                        <label className="visually-hidden" htmlFor="ops-alert-severity">Lọc mức độ cảnh báo</label>
                        <select id="ops-alert-severity" className="form-select form-select-sm ops-input ops-severity-filter">
                            <option value="">Tất cả mức độ</option>
                            <option value="critical">Nghiêm trọng</option>
                            <option value="warning">Cảnh báo</option>
                            <option value="info">Thông tin</option>
                        </select>
                    </div>
                    <div id="ops-alerts-list" className="ops-center-list"></div>
            </div>
            <div className="ops-card ops-monitor-card">
                    <div className="ops-section-head">
                        <div className="ops-section-title"><i className="bi bi-clock-history me-2" aria-hidden="true"></i>Nhật ký <span id="ops-event-count" className="ops-count-pill">0</span></div>
                    </div>
                    <div className="ops-event-controls">
                            <label className="visually-hidden" htmlFor="ops-event-search">Tìm sự kiện</label>
                            <input id="ops-event-search" className="form-control form-control-sm ops-input" placeholder="Tìm trong nhật ký..." />
                            <label className="visually-hidden" htmlFor="ops-event-severity">Lọc mức độ sự kiện</label>
                            <select id="ops-event-severity" className="form-select form-select-sm ops-input">
                                <option value="">Tất cả</option>
                                <option value="success">Thành công</option>
                                <option value="warning">Cảnh báo</option>
                                <option value="error">Lỗi</option>
                                <option value="info">Thông tin</option>
                            </select>
                    </div>
                    <div id="ops-events-list" className="ops-event-list"></div>
            </div>
        </section>

        <div className="ops-area-heading">
            <div><span className="ops-area-kicker">02 / TÁC VỤ</span><h2>Công cụ điều hành</h2></div>
            <p>Thao tác hàng loạt và xuất báo cáo</p>
        </div>

        <section className="ops-card ops-export-card" aria-labelledby="ops-export-title">
            <div className="ops-export-intro">
                <span className="ops-export-icon"><i className="bi bi-file-earmark-arrow-down" aria-hidden="true"></i></span>
                <div><div className="ops-section-title" id="ops-export-title">Xuất dữ liệu</div><p>Chọn một trong 11 bộ dữ liệu, tải file CSV mở bằng Excel.</p></div>
            </div>
            <div className="ops-export-controls">
                <label className="visually-hidden" htmlFor="ops-export-kind">Chọn dữ liệu cần tải</label>
                <select id="ops-export-kind" className="form-select ops-input">
                    <optgroup label="Trạm và Rover">
                        <option value="devices">Toàn bộ thông tin trạm</option>
                        <option value="rovers">Danh sách Rover trong DB</option>
                        <option value="rover-history">Lịch sử phiên Rover</option>
                        <option value="caster-users">Rover trên các Caster</option>
                        <option value="blocklist">Danh sách Rover bị chặn</option>
                    </optgroup>
                    <optgroup label="Vận hành">
                        <option value="alerts">Cảnh báo hiện tại</option>
                        <option value="resolved-alerts">Cảnh báo đã xử lý còn tồn tại</option>
                        <option value="events">200 sự kiện gần nhất</option>
                        <option value="casters">Tình trạng máy chủ Caster</option>
                    </optgroup>
                    <optgroup label="Quản trị">
                        <option value="users">Tài khoản hệ thống</option>
                        <option value="templates">Mẫu cấu hình trạm</option>
                    </optgroup>
                </select>
                <button className="btn btn-outline-info" id="ops-export-btn" type="button"><i className="bi bi-download me-1" aria-hidden="true"></i>Tải CSV</button>
            </div>
        </section>

        <section className="ops-tools-grid" aria-label="Công cụ Rover và trạm">
            <article className="ops-card ops-tool-card">
                <div className="ops-section-head">
                    <div><div className="ops-section-title"><i className="bi bi-hdd-network-fill" aria-hidden="true"></i> Rover</div><p className="ops-card-caption">Gia hạn tài khoản và kiểm tra quyền truy cập</p></div>
                </div>
                <div className="ops-tool-body">
                    <label className="form-label" htmlFor="ops-rover-usernames">Tài khoản Rover</label>
                    <textarea id="ops-rover-usernames" className="form-control ops-input" rows="2" placeholder="Nhập tên tài khoản, phân cách bằng dấu phẩy"></textarea>
                    <div className="ops-tool-action-row">
                        <div><label className="form-label" htmlFor="ops-rover-days">Thời hạn mới</label><div className="ops-input-suffix"><input id="ops-rover-days" className="form-control ops-input" type="number" min="1" max="3650" defaultValue="365" /><span>ngày</span></div></div>
                        <button id="ops-rover-renew-btn" className="btn btn-primary" type="button"><i className="bi bi-calendar-plus me-1" aria-hidden="true"></i>Gia hạn Rover</button>
                    </div>
                    <details className="ops-tool-disclosure">
                        <summary><i className="bi bi-shield-check" aria-hidden="true"></i> Kiểm tra tài khoản Rover <i className="bi bi-chevron-down ops-summary-chevron" aria-hidden="true"></i></summary>
                        <div className="ops-disclosure-body">
                            <label className="form-label" htmlFor="ops-rover-validate-username">Tên tài khoản</label>
                            <input id="ops-rover-validate-username" className="form-control ops-input" placeholder="Ví dụ: rover01" />
                            <label className="form-label mt-2" htmlFor="ops-rover-validate-mps">Mountpoint (tùy chọn)</label>
                            <input id="ops-rover-validate-mps" className="form-control ops-input" placeholder="Ví dụ: HN.TP" />
                            <button id="ops-rover-validate-btn" className="btn btn-outline-info mt-3" type="button">Kiểm tra</button>
                            <pre id="ops-rover-result" className="ops-json-output mt-3 mb-0" hidden></pre>
                        </div>
                    </details>
                </div>
            </article>
            <article className="ops-card ops-tool-card">
                <div className="ops-section-head">
                    <div><div className="ops-section-title"><i className="bi bi-broadcast-pin" aria-hidden="true"></i> Trạm Base</div><p className="ops-card-caption">Thao tác hàng loạt và áp dụng mẫu cấu hình</p></div>
                </div>
                <div className="ops-tool-body">
                    <label className="form-label" htmlFor="ops-device-serials">Số sê-ri trạm</label>
                    <textarea id="ops-device-serials" className="form-control ops-input" rows="2" placeholder="Nhập số sê-ri, phân cách bằng dấu phẩy"></textarea>
                    <div className="ops-tool-action-row">
                        <div><label className="form-label" htmlFor="ops-device-action">Hành động</label><select id="ops-device-action" className="form-select ops-input"><option value="lock">Khóa trạm</option><option value="unlock">Mở khóa trạm</option><option value="reset">Đặt lại trạm</option></select></div>
                        <button id="ops-device-action-btn" className="btn btn-outline-warning" type="button"><i className="bi bi-lightning-charge me-1" aria-hidden="true"></i>Thực hiện</button>
                    </div>
                    <details className="ops-tool-disclosure">
                        <summary><i className="bi bi-sliders" aria-hidden="true"></i> Mẫu cấu hình <i className="bi bi-chevron-down ops-summary-chevron" aria-hidden="true"></i></summary>
                        <div className="ops-disclosure-body">
                            <label className="form-label" htmlFor="ops-template-name">Tên mẫu mới</label>
                            <input id="ops-template-name" className="form-control ops-input" placeholder="Tên mẫu cấu hình" />
                            <label className="form-label mt-2" htmlFor="ops-template-service">Cấu hình dịch vụ (JSON)</label>
                            <textarea id="ops-template-service" className="form-control ops-input" rows="3" placeholder='{"server1_enabled":true}'></textarea>
                            <button id="ops-template-create-btn" className="btn btn-outline-info mt-2" type="button">Tạo mẫu</button>
                            <div className="ops-template-apply">
                                <label className="form-label" htmlFor="ops-template-select">Mẫu đã lưu</label>
                                <div className="ops-template-apply-row"><select id="ops-template-select" className="form-select ops-input"></select><button id="ops-template-apply-btn" className="btn btn-outline-info" type="button">Áp dụng</button></div>
                            </div>
                            <pre id="ops-device-result" className="ops-json-output mt-3 mb-0" hidden></pre>
                        </div>
                    </details>
                </div>
            </article>
        </section>
    </main>

    <div id="toast-container" className="toast-container position-fixed top-0 end-0 p-3"></div>
        </>
    );
}

window.OperationsView = OperationsView;
