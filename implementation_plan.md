# Nâng cấp Landing Page CORS - Cinematic GSAP + SVG Enhancement

Nâng cấp toàn diện landing page với hiệu ứng GSAP mượt mà như video, tận dụng tối đa 2 file SVG (`vietnam-map.svg` và `Flag_of_Vietnam.svg`), bổ sung thêm thông tin và visual effects premium.

## Proposed Changes

### 1. Inline SVG Vietnam Map + GSAP Draw-On Animation

Thay vì dùng `<img>` tag cho `vietnam-map.svg`, ta sẽ **inline SVG trực tiếp** vào component để có thể:
- **GSAP DrawSVG effect**: Vẽ từng nét bản đồ Việt Nam từ Bắc vào Nam khi scroll
- **Animated station nodes**: Các điểm trạm CORS pulse lên khi bản đồ được vẽ xong  
- **Path highlighting**: Highlight golden path (đường Hồ Chí Minh) với hiệu ứng glow
- **Interactive glow regions**: Bắc/Trung/Nam glow khác nhau khi scroll qua

#### [MODIFY] [homepage-enterprise.v2.js](file:///c:/Users/len/Desktop/other_projects/cors/cors_dashboard/frontend/js/homepage-enterprise.v2.js)

**VietnamSVG Component - Major Upgrade:**
- Chuyển từ `<img>` sang inline `<svg>` với tất cả path từ file `vietnam-map.svg`
- Thêm GSAP `drawSVG` animation: vẽ outline Việt Nam từ trên xuống dưới
- Thêm animated data connection lines giữa 3 thành phố chính (HN-ĐN-HCM)
- Thêm ripple effects cho các station nodes
- Thêm animated radar sweep overlay trên bản đồ

**Flag SVG - Cinematic Parallax Usage:**
- Inline flag SVG để có thể animate các phần riêng (nền đỏ, ngôi sao vàng)
- Thêm GSAP rotation/scale animation cho ngôi sao
- Sử dụng flag như "loading reveal" element khi page đầu tiên load
- Thêm flag SVG decorative elements ở nhiều vị trí: hero badge, section kickers, footer

---

### 2. New Sections & Enhanced Information

**Thêm section "Tại sao chọn Aitogy?" (Why Choose Us):**
- 3 columns với animated counters và icon illustrations
- Số liệu: "10+ năm kinh nghiệm", "50+ đối tác", "24/7 hỗ trợ kỹ thuật"
- GSAP stagger reveal từ trái/phải

**Thêm section "Đối tác & Khách hàng" (Partners):**
- Logo marquee animation
- Testimonial cards với glassmorphism

**Upgrade Hero section:**
- Thêm animated typing effect cho subtitle
- Thêm floating tech badges xung quanh map (GPS, BeiDou, etc.)
- Thêm animated connection lines từ satellites đến map

---

### 3. Advanced GSAP Scroll Animations

#### [MODIFY] [homepage-enterprise.v2.js](file:///c:/Users/len/Desktop/other_projects/cors/cors_dashboard/frontend/js/homepage-enterprise.v2.js)

**`useCinematicScroll` hook - Major Enhancement:**

- **Hero Parallax Depth**: Multi-layer parallax với 3+ tốc độ khác nhau
- **SVG Draw-On**: GSAP `drawSVG` cho map outline (sử dụng stroke-dasharray/dashoffset)
- **Section Pin + Scrub**: Pin "How It Works" section và scrub qua 4 steps
- **Text Split Animation**: Chữ title từng section tách thành từng ký tự và fly in
- **Smooth Counter**: Animated counter trigger khi scroll vào viewport
- **3D Card Tilt**: Feature cards tilt nhẹ theo mouse position
- **Magnetic Buttons**: CTA buttons có magnetic hover effect
- **ScrollTrigger Batch**: Batch animation cho station directory cards
- **Progress Bar**: Scroll progress indicator dọc bên phải màn hình

---

### 4. CSS Cinematic Enhancements

#### [MODIFY] [enterprise-theme.css](file:///c:/Users/len/Desktop/other_projects/cors/cors_dashboard/frontend/css/enterprise-theme.css)

- Thêm CSS cho SVG draw animation (stroke-dasharray, dashoffset)
- Thêm glassmorphism card styles cho "Why Choose Us"
- Thêm animated gradient border effects
- Thêm neon glow text effects cho section titles
- Thêm CSS cho scroll progress bar
- Thêm shimmer/shine effect cho buttons
- Thêm animated grid overlay cho hero background
- Thêm responsive styles cho new sections

#### [MODIFY] [cinematic-cards.css](file:///c:/Users/len/Desktop/other_projects/cors/cors_dashboard/frontend/css/cinematic-cards.css)

- Enhance hover micro-animations
- Thêm animated border gradient effect

---

### 5. GSAP ScrollSmoother + Advanced Plugins

#### [MODIFY] [index.html](file:///c:/Users/len/Desktop/other_projects/cors/cors_dashboard/frontend/index.html)

- Thêm GSAP `TextPlugin` cho typing effect
- Update cache-busting version strings

---

## Tóm tắt Visual Effects Mới

| Effect | Mô tả | Kỹ thuật |
|--------|--------|----------|
| SVG Draw-On Map | Bản đồ VN vẽ từng nét khi scroll | GSAP + stroke-dasharray |
| Flag Star Spin | Ngôi sao cờ VN xoay nhẹ | GSAP rotation |
| Data Flow Lines | Đường kết nối HN↔ĐN↔HCM | SVG animated paths |
| Text Reveal | Chữ từng section fly in | GSAP SplitText style |
| 3D Card Parallax | Cards nghiêng theo mouse | GSAP + mousemove |
| Scroll Progress | Thanh progress dọc bên phải | GSAP ScrollTrigger |
| Radar Sweep | Vòng quét radar trên bản đồ | CSS animation + GSAP |
| Counter Glow | Số liệu glow khi counting | GSAP + CSS filter |
| Magnetic Hover | Buttons follow cursor | GSAP quickTo |
| Section Wipe | Transition giữa sections | GSAP ScrollTrigger |

## Verification Plan

### Automated Tests
- Mở browser, kiểm tra landing page load đầy đủ
- Scroll từ trên xuống dưới, verify tất cả animations trigger đúng
- Kiểm tra responsive trên viewport nhỏ (768px)

### Manual Verification
- Record browser session để demo scroll animation flow
- Kiểm tra performance (không lag khi scroll)
- Verify SVG render đúng trên Chrome/Edge
