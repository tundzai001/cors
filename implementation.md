# Landing Page Upgrade Implementation Plan

## Goal

Nang cap landing page thanh mot trai nghiem premium, cinematic va co chieu sau. Trang phai tao cam giac "vippro master" ngay khi dung yen o viewport dau tien: it chu, visual manh, motion cham va tinh te, khong bi mau me re tien.

Landing page nen giu tinh than cua `DESIGN.md`: dark command-center, cyan signal, gold accent, glass surface, telemetry motion va cam giac enterprise GNSS/RTK dang tin cay.

## Creative Direction

### Visual Tone

- Premium technical command center.
- Nen toi sau, co nhieu lop anh sang va chieu sau.
- Cyan dung nhu tin hieu live/precision.
- Gold dung rat tiet che cho diem nhan cao cap.
- Typography lon, chac, it chu, hierarchy ro.
- UI khong nen thanh mot mang card day dac; hero can co cam giac full-bleed, cinematic.

### Static First Impression

Neu animation bi pause, top page van phai trong dat tien:

- Hero object/scene la tam diem.
- Headline ro va co luc.
- CTA noi bat nhung khong loe loet.
- Background co depth bang lighting, grid, horizon, orbital/telemetry layers.
- Trust signal nho, vi du uptime, stations, RTK precision, network status.

## Recommended Tech

### If Current Frontend Is React

- `three` for WebGL hero scene.
- `@react-three/fiber` for React integration.
- `@react-three/drei` for camera helpers, environment, shader utilities.
- `framer-motion` for UI entrance and micro interactions.
- `gsap` only if scroll choreography becomes complex.

### If Current Frontend Is Plain HTML/CSS/JS

- `three` directly for hero canvas.
- CSS keyframes for lightweight ambient motion.
- Intersection Observer for reveal-on-scroll.

## Page Structure

### 1. Hero

Purpose: tao an tuong premium ngay lap tuc.

Content:

- H1: brand/product/offer chinh.
- Subcopy: 1-2 dong ve gia tri cot loi.
- CTA primary: vao dashboard / request demo / explore network.
- CTA secondary: xem coverage / xem stations / learn more.
- Trust strip nho: live stations, uptime, precision, coverage.

Visual:

- Full-bleed Three.js canvas hoac cinematic background.
- Foreground text nam tren canvas nhung khong nam trong card.
- Mot panel nho co live status duoc phep dung glass surface.

Motion:

- Camera drift rat nhe.
- Signal rings cham.
- Scan beam 8-12s moi vong.
- Particle/data points cham, it, co muc dich.
- CTA glow nhe, khong pulse lien tuc qua manh.

### 2. Precision / Value Section

Purpose: giai thich tai sao san pham dang tin.

Layout:

- 3 cot hoac 2 cot tuy viewport.
- Moi block co metric lon, label nho, mo ta ngan.

Motion:

- Reveal stagger khi scroll.
- Metric count-up ngan, chi chay mot lan.
- Border/light sweep rat nhe khi block vao viewport.

### 3. Network Showcase

Purpose: cho thay quy mo va kha nang van hanh.

Options:

- Map-like visualization.
- Station nodes voi status online/warning/offline.
- Connection lines hoac RTK signal paths.

Motion:

- Lines draw in khi section vao viewport.
- Nodes glow theo status.
- Hover node hien metadata ngan.

### 4. Feature Showcase

Purpose: noi ve tinh nang nhung van giu premium.

Possible features:

- Real-time station monitoring.
- GNSS constellation visibility.
- CORS device health.
- RTK/NTRIP service reliability.
- Alerts and maintenance workflows.

Layout:

- Sticky visual ben phai, text/features ben trai.
- Khi scroll qua tung feature, visual thay doi state.

### 5. Social Proof / Reliability

Purpose: lam trang co do tin cay, khong chi dep.

Content:

- Uptime number.
- Deployment scale.
- Partner/client logos neu co.
- Short testimonial neu co.
- Compliance/security/reliability claims neu co bang chung.

### 6. Final CTA

Purpose: ket thuc ro rang.

Style:

- Nen toi, mot accent gold/cyan rat gon.
- H2 ngan.
- CTA primary.
- Mot dong reassurance: support, deployment, consultation, demo.

## Three.js Hero Concept

### Scene Idea

Tao mot "GNSS precision orbit scene":

- Mot globe/terrain plane hoac abstract receiver core o trung tam.
- Orbit rings dai dien cho satellite signal.
- Station nodes tren mat phang/map.
- Beam lines cyan noi satellites, receiver va stations.
- Subtle gold highlight tai core hoac CTA direction.

### Visual Layers

1. Background deep navy/black gradient.
2. Fine grid hoac terrain mesh.
3. Particle field rat mong.
4. Main object: globe/core/station network.
5. Orbit/signal rings.
6. Scan beam/light sweep.
7. HTML UI overlay.

### Motion Rules

- Camera movement: slow, 20-40s loop.
- Rings: slow rotation, different speeds.
- Particles: subtle drift, opacity low.
- Lines: faint pulse with long interval.
- No fast spinning object.
- No excessive bloom.

### Performance Rules

- Target 60fps desktop, acceptable 30fps mobile.
- Limit geometry count.
- Use instancing for particles/nodes.
- Pause or reduce animation when tab hidden.
- Respect `prefers-reduced-motion`.
- On mobile, reduce particle count and disable expensive post-processing.

## UI Motion Spec

### Hero Load

Sequence:

1. Background/canvas visible immediately.
2. Main object fades in within 400-700ms.
3. H1 fades/slides up slightly.
4. Subcopy appears after H1.
5. CTA and trust metrics appear last.

### Idle Motion

- Ambient scene stays alive.
- Text stays stable.
- CTA glow is subtle.
- No layout shift.
- No animation should distract from reading.

### Scroll Motion

- Sections reveal with opacity + small translateY.
- Stagger between children: 80-120ms.
- Scroll animation should be restrained.
- Avoid every element moving independently.

## Component Breakdown

Recommended components:

- `LandingPage`
- `HeroSection`
- `HeroCanvas` or `PrecisionScene`
- `HeroCopy`
- `TrustMetrics`
- `ValueSection`
- `NetworkShowcase`
- `FeatureScroller`
- `ReliabilitySection`
- `FinalCTA`

Three.js-specific pieces:

- `SceneCamera`
- `SignalRings`
- `StationNodes`
- `ParticleField`
- `ScanBeam`
- `CoreObject`

## Styling Requirements

Use tokens from `DESIGN.md`:

- Primary cyan: `#00f2ff`
- Deep navy: `#0a192f`
- Gold accent: `#ffcc00`
- Text main: `#e6f1ff`
- Text dim: `#8892b0`
- Glass surface: `rgba(10, 25, 47, 0.7)`

Guidelines:

- Keep border radius restrained, usually 8-16px.
- Cards only for contained information, not every section.
- Avoid overusing cyan glow.
- Gold should appear in rare high-value moments.
- Keep letter spacing normal for body and UI text.
- Ensure mobile text never overlaps canvas or controls.

## Accessibility

- Maintain readable contrast.
- Hero canvas must be decorative unless interactive.
- Provide semantic H1, sections, buttons, links.
- Add reduced-motion mode:
  - stop camera drift;
  - disable scan beam;
  - keep static premium composition;
  - preserve all content.
- CTA focus states must be visible.

## Implementation Steps

### Phase 1: Audit

- Identify current frontend stack and route for landing page.
- Find existing design tokens/styles.
- Check whether dependencies already include Three.js, Framer Motion, GSAP, or icon libraries.
- Capture current landing page screenshot before changes.

### Phase 2: Hero Foundation

- Build full-bleed hero layout.
- Add headline, subcopy, CTA and trust metrics.
- Implement responsive spacing.
- Ensure static screenshot already looks premium before animation.

### Phase 3: Three.js Scene

- Add canvas behind hero content.
- Create core object, signal rings, station nodes and particle field.
- Add slow idle animation.
- Add mobile quality settings.
- Add reduced-motion fallback.

### Phase 4: Page Sections

- Add value metrics section.
- Add network showcase section.
- Add feature scroller/showcase.
- Add reliability proof.
- Add final CTA.

### Phase 5: Motion Polish

- Add entrance sequence.
- Add scroll reveals.
- Tune easing and durations to match `DESIGN.md`.
- Remove any motion that feels noisy or cheap.

### Phase 6: Verification

- Test desktop and mobile viewport.
- Check no text overlap.
- Check canvas is not blank.
- Check animation performance.
- Check reduced-motion behavior.
- Run project tests/build/lint if available.

## Acceptance Criteria

- First viewport looks premium in a still screenshot.
- Hero has tasteful idle motion without distracting from content.
- Three.js scene renders correctly on desktop and mobile.
- Page remains usable when WebGL is unavailable or reduced motion is enabled.
- CTA is visible and clear above the fold.
- Landing page follows the existing dark/cyan/gold design system.
- No layout shift from animated elements.
- Build/lint/tests pass where available.

## Notes

- Three.js should serve the brand and product story, not become a tech demo.
- The strongest version is likely a cinematic GNSS signal scene with restrained telemetry motion.
- The page should feel like a premium operational platform, not a generic SaaS landing template.
