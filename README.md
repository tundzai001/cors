<p align="center">
  <a href="https://cors.aitogy.com/">
    <img src="docs/assets/cors-satellite-banner.png" width="100%" alt="Vệ tinh GNSS truyền tín hiệu tới mạng lưới trạm CORS trên Trái Đất" />
  </a>
</p>

<h1 align="center">🛰️ Aitogy CORS Control</h1>
<p align="center"><strong>Mạng lưới trạm tham chiếu GNSS · Định vị RTK chính xác · Vận hành trên một nền tảng</strong></p>

<p align="center">
  <img src="https://readme-typing-svg.demolab.com?font=Fira+Code&amp;weight=600&amp;size=19&amp;duration=2800&amp;pause=900&amp;color=06B6D4&amp;center=true&amp;vCenter=true&amp;width=850&amp;lines=Kh%C3%A1m+ph%C3%A1+m%E1%BA%A1ng+l%C6%B0%E1%BB%9Bi+tr%E1%BA%A1m+CORS;Theo+d%C3%B5i+tr%E1%BA%A1ng+th%C3%A1i+GNSS%2FRTK;K%E1%BA%BFt+n%E1%BB%91i+v%E1%BB%87+tinh%2C+tr%E1%BA%A1m+v%C3%A0+rover" alt="Khám phá Aitogy CORS" />
</p>

<p align="center">
  <a href="https://cors.aitogy.com/"><img src="https://img.shields.io/badge/WEBSITE-cors.aitogy.com-0EA5E9?style=for-the-badge&amp;logo=googlechrome&amp;logoColor=white" alt="Website Aitogy CORS" /></a>
  <a href="https://cors.aitogy.com/network-status"><img src="https://img.shields.io/badge/M%E1%BA%A0NG_L%C6%AF%E1%BB%9AI-tr%E1%BA%A1ng_th%C3%A1i-7C3AED?style=for-the-badge&amp;logo=signal&amp;logoColor=white" alt="Trạng thái mạng lưới" /></a>
  <a href="https://cors.aitogy.com/coverage"><img src="https://img.shields.io/badge/B%E1%BA%A2N_%C4%90%E1%BB%92-v%C3%B9ng_ph%E1%BB%A7-06B6D4?style=for-the-badge&amp;logo=googlemaps&amp;logoColor=white" alt="Bản đồ vùng phủ" /></a>
  <a href="https://cors.aitogy.com/api/docs"><img src="https://img.shields.io/badge/API-t%C3%A0i_li%E1%BB%87u-22C55E?style=for-the-badge&amp;logo=fastapi&amp;logoColor=white" alt="Tài liệu API" /></a>
</p>

<p align="center">
  <img src="https://img.shields.io/website?url=https%3A%2F%2Fcors.aitogy.com%2F&amp;style=flat-square&amp;label=LIVE%20WEBSITE" alt="Trạng thái website" />
  <img src="https://img.shields.io/github/last-commit/tundzai001/cors?style=flat-square&amp;color=F59E0B&amp;label=updated" alt="Lần cập nhật gần nhất" />
  <img src="https://img.shields.io/github/stars/tundzai001/cors?style=flat-square&amp;color=FBBF24&amp;logo=github" alt="GitHub stars" />
</p>

## 🌌 Nền tảng CORS cho định vị chính xác

**Aitogy CORS Control** là website quản lý và theo dõi mạng lưới trạm GNSS/CORS của Aitogy. Từ bản đồ trạm và vùng phủ công khai đến dashboard vận hành thiết bị, rover và kết nối NTRIP/RTCM — thông tin mạng lưới được tập trung trong một giao diện web.

## ✨ Khám phá nền tảng

| 🗺️ Bản đồ & vùng phủ | 📡 Tình trạng mạng lưới |
|---|---|
| Khám phá vị trí, trang thông tin trạm và phân bố mạng lưới CORS. | Theo dõi thông tin hoạt động của mạng lưới và các trạm. |

| 🛰️ Vận hành CORS & rover | 🔌 Dành cho nhà phát triển |
|---|---|
| Quản lý thiết bị, rover và phiên kết nối RTK/NTRIP trong dashboard. | Tra cứu tài liệu API để tích hợp với hệ thống. |

## 🧭 Từ vệ tinh đến rover RTK

```mermaid
flowchart LR
    SAT["🛰️ Vệ tinh GNSS"] --> BASE["📡 Trạm tham chiếu CORS"]
    BASE -->|Hiệu chỉnh RTCM / NTRIP| ROVER["🧭 Rover RTK"]
    BASE -.->|Trạng thái, cấu hình| HUB["🌐 Aitogy CORS Control"]
    HUB -->|Bản đồ, giám sát, vận hành| OPS["🛠️ Đội vận hành"]

    classDef space fill:#0B1220,stroke:#38BDF8,color:#F8FAFC,stroke-width:2px
    classDef signal fill:#102A43,stroke:#F59E0B,color:#F8FAFC,stroke-width:2px
    class SAT,BASE,ROVER space
    class HUB,OPS signal
```

## 🧰 Công nghệ

<p>
  <img src="https://img.shields.io/badge/Python-3776AB?style=flat-square&amp;logo=python&amp;logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/FastAPI-009688?style=flat-square&amp;logo=fastapi&amp;logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/JavaScript-F7DF1E?style=flat-square&amp;logo=javascript&amp;logoColor=222222" alt="JavaScript" />
  <img src="https://img.shields.io/badge/Leaflet-199900?style=flat-square&amp;logo=leaflet&amp;logoColor=white" alt="Leaflet" />
  <img src="https://img.shields.io/badge/PostgreSQL-4169E1?style=flat-square&amp;logo=postgresql&amp;logoColor=white" alt="PostgreSQL" />
  <img src="https://img.shields.io/badge/MQTT-660066?style=flat-square&amp;logo=eclipsemosquitto&amp;logoColor=white" alt="MQTT" />
</p>

## 🗂️ Cấu trúc repository

```text
.
├── cors_dashboard/       # Website và API: backend, frontend, source, tests
├── geodetic/             # Agent tại trạm, công cụ thiết bị và tests
├── docs/
│   ├── assets/           # Ảnh dùng trong tài liệu
│   └── specs/            # Đặc tả và thiết kế hệ thống
├── reports/
│   ├── geodetic/         # Log vận hành đã lưu
│   ├── pdf/              # Báo cáo tổng hợp
│   └── security-scan/    # Kết quả rà soát bảo mật
├── scripts/              # Build assets và self-check
└── tools/diagnostics/    # Công cụ phân tích RTCM/MQTT độc lập
```

Tài liệu chính: [đặc tả agent](docs/specs/ESP_AGENT_REPLACEMENT_SPEC.md) · [báo cáo security scan](reports/security-scan/report.md).

<p align="center">
  <a href="https://cors.aitogy.com/">🌐 Mở website</a> &nbsp;·&nbsp;
  <a href="https://cors.aitogy.com/network-status">📡 Xem trạng thái</a> &nbsp;·&nbsp;
  <a href="https://cors.aitogy.com/coverage">🗺️ Khám phá vùng phủ</a> &nbsp;·&nbsp;
  <a href="https://cors.aitogy.com/api/docs">🔌 Đọc API</a>
</p>

<p align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&amp;color=0:06B6D4,50:2563EB,100:7C3AED&amp;height=120&amp;section=footer&amp;animation=twinkling" width="100%" alt="" />
</p>
