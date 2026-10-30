#pragma once
#include <Arduino.h>

// Offline configuration page. Keep source readable; stored in flash via PROGMEM.
static const char WIFI_CONFIG_PAGE[] PROGMEM = R"PORTAL(
<!doctype html>
<html lang="vi">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width,initial-scale=1" />
    <meta name="config-token" content="%TOKEN%" />
    <title>Cài Wi-Fi · Aitogy</title>
    <style>
      :root {
        color-scheme: dark;
        --background: #080f1d;
        --surface: #111c2e;
        --surface-raised: #17243a;
        --text: #edf4ff;
        --muted: #a5b5cd;
        --accent: #78d9f7;
        --border: #2a3c56;
        --danger: #ffb1b1;
      }
      * {
        box-sizing: border-box;
      }
      body {
        margin: 0;
        min-height: 100vh;
        background:
          radial-gradient(ellipse at 90% 0, #16304c 0, transparent 48%),
          var(--background);
        color: var(--text);
        font:
          16px/1.55 system-ui,
          -apple-system,
          BlinkMacSystemFont,
          "Segoe UI",
          sans-serif;
      }
      main {
        max-width: 680px;
        margin: auto;
        padding: 36px 20px 40px;
      }
      header {
        margin-bottom: 26px;
      }
      .brand {
        display: flex;
        align-items: center;
        gap: 10px;
        font-size: 12px;
        font-weight: 750;
        letter-spacing: 0.16em;
        color: var(--accent);
      }
      .logo {
        width: 36px;
        height: 36px;
        display: grid;
        place-items: center;
        border: 1px solid #34546d;
        border-radius: 11px;
        background: #132e43;
      }
      .logo svg {
        width: 24px;
        height: 24px;
      }
      h1 {
        font-size: clamp(30px, 7vw, 40px);
        line-height: 1.15;
        letter-spacing: -0.035em;
        margin: 22px 0 12px;
      }
      h2 {
        font-size: 18px;
        line-height: 1.4;
        margin: 0;
        letter-spacing: -0.015em;
      }
      p {
        margin: 8px 0;
      }
      .intro {
        max-width: 48ch;
      }
      .muted,
      small {
        color: var(--muted);
      }
      small {
        display: block;
        font-size: 13px;
        margin-top: 7px;
      }
      .card {
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 18px;
        padding: 22px;
        margin: 16px 0;
        box-shadow: 0 10px 32px #0000001a;
      }
      .row {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        flex-wrap: wrap;
      }
      .section-title {
        display: flex;
        align-items: center;
        gap: 10px;
      }
      .step {
        width: 26px;
        height: 26px;
        display: grid;
        place-items: center;
        border-radius: 8px;
        background: #20344b;
        color: var(--accent);
        font-size: 12px;
        font-weight: 750;
      }
      .status {
        display: flex;
        align-items: flex-start;
        gap: 10px;
        background: #102c37;
        border: 1px solid #28525e;
        border-radius: 12px;
        padding: 13px 15px;
        margin-top: 22px;
        font-size: 14px;
      }
      .status::before {
        content: "";
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: var(--accent);
        margin-top: 7px;
        flex: none;
      }
      .status[data-state="connected"] {
        border-color: #366355;
        background: #142f29;
      }
      .status[data-state="connected"]::before {
        background: #83e0b5;
      }
      label {
        display: block;
        font-size: 14px;
        font-weight: 650;
        margin: 20px 0 8px;
      }
      input[type="text"],
      input[type="password"] {
        width: 100%;
        min-height: 50px;
        padding: 12px 14px;
        border: 1px solid #49617e;
        border-radius: 10px;
        font: inherit;
        background: #0a1525;
        color: var(--text);
      }
      input:disabled {
        opacity: 0.5;
      }
      button {
        min-height: 44px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 8px;
        border: 1px solid #425a76;
        border-radius: 10px;
        padding: 10px 14px;
        background: var(--surface-raised);
        color: var(--text);
        font: inherit;
        font-size: 14px;
        font-weight: 650;
        cursor: pointer;
        transition:
          background 0.16s,
          border-color 0.16s,
          transform 0.16s;
      }
      button:hover {
        background: #22354e;
        border-color: #6e8dad;
      }
      button:active {
        transform: translateY(1px);
      }
      button:disabled {
        opacity: 0.6;
        cursor: wait;
        transform: none;
      }
      button.primary {
        background: var(--accent);
        color: #071c2b;
        border-color: var(--accent);
        width: 100%;
        margin-top: 20px;
        min-height: 50px;
        font-size: 16px;
      }
      button.primary:hover {
        background: #a0e6fb;
      }
      button svg {
        width: 17px;
        height: 17px;
      }
      button.danger {
        color: var(--danger);
      }
      input:focus-visible,
      button:focus-visible,
      summary:focus-visible {
        outline: 3px solid var(--accent);
        outline-offset: 3px;
      }
      .check {
        display: flex;
        gap: 10px;
        align-items: center;
        min-height: 44px;
        margin: 4px 0;
        font-size: 14px;
        font-weight: 400;
        cursor: pointer;
      }
      .check input {
        width: 19px;
        height: 19px;
        flex: none;
        accent-color: var(--accent);
      }
      .checks {
        margin-top: 10px;
      }
      .networks {
        list-style: none;
        padding: 0;
        margin: 14px 0 0;
      }
      .networks li {
        border-top: 1px solid var(--border);
        padding: 14px 0;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
      }
      .networks .name {
        overflow-wrap: anywhere;
        min-width: 0;
        flex: 1;
        font-size: 15px;
      }
      .networks .actions {
        display: flex;
        gap: 8px;
        flex: none;
      }
      .networks li:last-child {
        padding-bottom: 0;
      }
      #nearby-list li {
        border: 0;
        padding: 4px 0;
      }
      .select-network {
        display: flex;
        align-items: center;
        justify-content: space-between;
        width: 100%;
        text-align: left;
        padding: 12px;
        gap: 12px;
      }
      .select-network .network-label {
        min-width: 0;
        overflow-wrap: anywhere;
      }
      .select-network small {
        margin: 3px 0 0;
        font-weight: 400;
        font-size: 12px;
      }
      .chevron {
        color: var(--accent);
        font-size: 20px;
        flex: none;
      }
      .count {
        display: inline-flex;
        border: 1px solid var(--border);
        border-radius: 7px;
        padding: 3px 9px;
        font-size: 12px;
        color: var(--muted);
      }
      .message {
        padding: 12px 14px;
        border: 1px solid #386452;
        border-radius: 10px;
        margin: 14px 0 0;
        background: #17382b;
        color: #bcf0d0;
        overflow-wrap: anywhere;
        font-size: 14px;
      }
      .message.error {
        background: #3b1e29;
        border-color: #7f3f53;
        color: #ffc5cf;
      }
      footer {
        font-size: 13px;
        color: var(--muted);
        padding: 10px 4px;
      }
      summary {
        cursor: pointer;
        min-height: 44px;
        display: flex;
        align-items: center;
        gap: 8px;
        color: #d4e7ff;
        font-size: 14px;
        font-weight: 600;
      }
      summary::before {
        content: "+";
        color: var(--accent);
        font-size: 20px;
      }
      details[open] summary::before {
        content: "−";
      }
      footer p {
        margin: 0 0 12px;
      }
      [hidden] {
        display: none !important;
      }
      @media (max-width: 400px) {
        main {
          padding: 24px 14px;
        }
        .card {
          padding: 18px;
        }
        .network-actions button {
          padding: 10px;
        }
      }
      @media (max-width: 350px) {
        .networks li {
          flex-wrap: wrap;
        }
        .network-actions {
          margin-left: auto;
        }
      }
      @media (prefers-reduced-motion: reduce) {
        button {
          transition: none;
        }
      }
    </style>
  </head>
  <body>
    <main>
      <header>
        <div class="brand">
          <span class="logo" aria-hidden="true">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
            >
              <path
                d="M3 8a15 15 0 0 1 18 0M6 12a10 10 0 0 1 12 0M9 16a5 5 0 0 1 6 0"
              />
              <circle cx="12" cy="20" r="1" fill="currentColor" stroke="none" />
            </svg>
          </span>
          AITOGY BASE
        </div>
        <h1>Wi-Fi của bạn.<br />Kết nối với ESP.</h1>
        <p class="muted intro">
          Lưu Wi-Fi hoặc hotspot điện thoại cho thiết bị. Không cần Internet để
          cài đặt.
        </p>
        <div id="connection" class="status" role="status">
          Đang đọc kết nối…
        </div>
      </header>

      <section class="card" aria-labelledby="nearby">
        <div class="row">
          <div class="section-title">
            <span class="step" aria-hidden="true">01</span>
            <h2 id="nearby">Mạng xung quanh</h2>
          </div>
          <button id="scan" type="button">
            <svg
              aria-hidden="true"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
            >
              <path d="M20 7v5h-5M4 17v-5h5" />
              <path d="M6 7a7 7 0 0 1 11-1l3 3M4 15l3 3a7 7 0 0 0 11-1" />
            </svg>
            Quét mạng
          </button>
        </div>
        <p id="scan-status" class="muted" role="status">
          Quét để chọn mạng, hoặc nhập tên bên dưới.
        </p>
        <ul id="nearby-list" class="networks"></ul>
      </section>

      <section class="card" aria-labelledby="add">
        <div class="section-title">
          <span class="step" aria-hidden="true">02</span>
          <h2 id="add">Thông tin kết nối</h2>
        </div>
        <form id="wifi-form">
          <label for="ssid">Tên Wi-Fi / hotspot</label>
          <input
            id="ssid"
            name="ssid"
            type="text"
            maxlength="32"
            required
            autocomplete="off"
            autocapitalize="none"
            spellcheck="false"
          />
          <label for="password">Mật khẩu Wi-Fi</label>
          <input
            id="password"
            name="password"
            type="password"
            maxlength="63"
            autocomplete="new-password"
            aria-describedby="password-help"
          />
          <small id="password-help"
            >8–63 byte. Sửa mạng đã lưu: để trống để giữ mật khẩu.</small
          >
          <div class="checks">
            <label class="check"
              ><input id="show-password" type="checkbox" />Hiện mật khẩu đang
              nhập</label
            >
            <label class="check"
              ><input id="open" type="checkbox" />Mạng không có mật khẩu</label
            >
          </div>
          <button id="save" class="primary" type="submit">
            Lưu & ưu tiên mạng này
          </button>
          <div
            id="message"
            class="message"
            role="status"
            aria-live="polite"
            hidden
          ></div>
        </form>
      </section>

      <section class="card" aria-labelledby="saved">
        <div class="row">
          <div class="section-title">
            <span class="step" aria-hidden="true">03</span>
            <h2 id="saved">Mạng đã lưu</h2>
          </div>
          <span id="count" class="count"></span>
        </div>
        <p class="muted">Mạng vừa lưu được thử trước. Tối đa 8 mạng.</p>
        <ul id="saved-list" class="networks"></ul>
      </section>

      <footer>
        <details>
          <summary>Dùng hotspot của chính điện thoại này?</summary>
          <p>
            Lưu tên và mật khẩu hotspot, sau đó bật lại hotspot để ESP kết nối.
            Bạn không cần nạp lại firmware.
          </p>
        </details>
        <p>
          Wi-Fi cấu hình của ESP luôn bật. Chế độ ưu tiên SIM hoặc Wi-Fi đang
          dùng được giữ nguyên.
        </p>
      </footer>
    </main>
    <script nonce="%TOKEN%">
      "use strict";

      const element = (id) => document.getElementById(id);
      const configToken = document.querySelector(
        'meta[name="config-token"]',
      ).content;
      let renderedNetworks = "";

      async function requestApi(path, body) {
        const response = await fetch("/api/" + path, {
          method: body ? "POST" : "GET",
          headers: {
            "X-Config-Token": configToken,
            ...(body ? { "Content-Type": "application/json" } : {}),
          },
          body: body ? JSON.stringify(body) : undefined,
          cache: "no-store",
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(
            data.error || "Không thực hiện được. Vui lòng thử lại.",
          );
        }
        return data;
      }

      function showMessage(text, isError = false) {
        const message = element("message");
        message.textContent = text;
        message.classList.toggle("error", isError);
        message.hidden = false;
      }

      function createElement(tag, text, className) {
        const result = document.createElement(tag);
        result.textContent = text;
        if (className) result.className = className;
        return result;
      }

      function selectNetwork(ssid, isOpen) {
        element("ssid").value = ssid;
        element("password").value = "";
        element("open").checked = isOpen;
        element("password").disabled = isOpen;
        element("ssid").focus();
      }

      function renderSavedNetworks(networks) {
        const signature = JSON.stringify(networks);
        if (signature === renderedNetworks) return;
        renderedNetworks = signature;
        const list = element("saved-list");
        list.replaceChildren();
        if (!networks.length) {
          list.append(createElement("li", "Chưa lưu mạng nào.", "muted"));
        }
        for (const network of networks) {
          const row = createElement("li", "");
          const name = createElement("span", network.ssid, "name");
          const actions = createElement("div", "", "actions network-actions");
          const editButton = createElement("button", "Sửa");
          editButton.type = "button";
          editButton.setAttribute("aria-label", "Sửa mạng " + network.ssid);
          editButton.onclick = () =>
            selectNetwork(network.ssid, !network.secured);

          const forgetButton = createElement("button", "Quên", "danger");
          forgetButton.type = "button";
          forgetButton.setAttribute("aria-label", "Quên mạng " + network.ssid);
          forgetButton.onclick = async () => {
            if (!confirm("Quên mạng " + network.ssid + "?")) return;
            forgetButton.disabled = true;
            try {
              await requestApi("forget", { ssid: network.ssid });
              await refreshState();
              showMessage("Đã quên mạng.");
            } catch (error) {
              showMessage(error.message, true);
            } finally {
              forgetButton.disabled = false;
            }
          };
          actions.append(editButton, forgetButton);
          row.append(name, actions);
          list.append(row);
        }
      }

      async function refreshState() {
        const state = await requestApi("state");
        const connection = element("connection");
        connection.dataset.state = state.wifi_connected
          ? "connected"
          : "waiting";
        connection.textContent = state.wifi_connected
          ? "Wi-Fi đang kết nối: " + state.wifi_ssid
          : "Cấu hình cục bộ · " + state.ap_ssid;
        element("count").textContent = state.networks.length + "/8";
        renderSavedNetworks(state.networks);
      }

      function renderNearbyNetworks(networks) {
        const list = element("nearby-list");
        list.replaceChildren();
        for (const network of networks) {
          const row = createElement("li", "");
          const button = createElement("button", "", "select-network");
          button.type = "button";
          button.setAttribute("aria-label", "Chọn mạng " + network.ssid);
          const label = createElement("span", "", "network-label");
          label.append(createElement("span", network.ssid));
          label.append(
            createElement(
              "small",
              network.rssi +
                " dBm · " +
                (network.open ? "Không mật khẩu" : "Có mật khẩu"),
            ),
          );
          const arrow = createElement("span", "›", "chevron");
          arrow.setAttribute("aria-hidden", "true");
          button.append(label, arrow);
          button.onclick = () => selectNetwork(network.ssid, network.open);
          row.append(button);
          list.append(row);
        }
      }

      const wait = (milliseconds) =>
        new Promise((resolve) => setTimeout(resolve, milliseconds));

      async function scanNetworks() {
        const scanButton = element("scan");
        const scanStatus = element("scan-status");
        scanButton.disabled = true;
        scanButton.setAttribute("aria-busy", "true");
        scanStatus.textContent = "Đang quét mạng, vui lòng chờ…";
        element("nearby-list").replaceChildren();
        try {
          let result = await requestApi("scan?start=1");
          for (let attempt = 0; result.pending && attempt < 20; attempt++) {
            await wait(800);
            result = await requestApi("scan");
          }
          if (result.pending) {
            throw new Error(
              "Quét mạng mất quá lâu. Bạn có thể nhập tên mạng thủ công.",
            );
          }
          renderNearbyNetworks(result.networks);
          scanStatus.textContent = result.networks.length
            ? "Chọn một mạng để điền thông tin."
            : "Không tìm thấy mạng. Bạn có thể nhập tên thủ công.";
        } catch (error) {
          scanStatus.textContent = error.message;
        } finally {
          scanButton.disabled = false;
          scanButton.removeAttribute("aria-busy");
        }
      }

      async function saveNetwork(event) {
        event.preventDefault();
        const saveButton = element("save");
        saveButton.disabled = true;
        saveButton.textContent = "Đang lưu…";
        saveButton.setAttribute("aria-busy", "true");
        try {
          await requestApi("save", {
            ssid: element("ssid").value,
            password: element("password").value,
            open: element("open").checked,
          });
          element("password").value = "";
          showMessage("Đã lưu. ESP sẽ thử kết nối; mạng cấu hình này vẫn bật.");
          await refreshState();
        } catch (error) {
          showMessage(error.message, true);
        } finally {
          saveButton.disabled = false;
          saveButton.textContent = "Lưu & ưu tiên mạng này";
          saveButton.removeAttribute("aria-busy");
        }
      }

      // SSIDs are always rendered with textContent, never interpreted as HTML.
      element("show-password").onchange = () => {
        element("password").type = element("show-password").checked
          ? "text"
          : "password";
      };
      element("open").onchange = () => {
        element("password").disabled = element("open").checked;
      };
      element("wifi-form").onsubmit = saveNetwork;
      element("scan").onclick = scanNetworks;
      refreshState().catch((error) => showMessage(error.message, true));
      setInterval(() => refreshState().catch(() => {}), 5000);
    </script>
  </body>
</html>
)PORTAL";
