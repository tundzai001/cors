---
colors:
  primary: "#00f2ff"
  primary-navy: "#0a192f"
  accent-gold: "#ffcc00"
  text-main: "#e6f1ff"
  text-dim: "#8892b0"
  surface-900: "#0f0f0f"
  surface-800: "#1a1a1a"
  surface-700: "#2c2c2e"
  surface-600: "#3a3a3c"
  surface-glass: "rgba(10, 25, 47, 0.7)"
  glass-border: "rgba(0, 242, 255, 0.2)"
  glow-primary: "rgba(0, 242, 255, 0.3)"
  glow-primary-strong: "rgba(0, 242, 255, 0.5)"
  success: "#198754"
  warning: "#ffc107"
  danger: "#dc3545"
  badge-gps: "#4facfe"
  badge-glo: "#f093fb"
  badge-bds: "#ff0844"
  badge-gal: "#00f2fe"
  status-online: "linear-gradient(180deg, #51cf66 0%, #37b24d 100%)"
  status-offline: "linear-gradient(180deg, #ff6b6b 0%, #fa5252 100%)"
  status-warning: "linear-gradient(180deg, #ffd43b 0%, #fab005 100%)"
  status-configuring: "linear-gradient(180deg, #74c0fc 0%, #339af0 100%)"

typography:
  body:
    font-family: "'Inter', 'Noto Sans', 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif"
    font-weight: 400
    letter-spacing: "0.2px"
    line-height: 1.6
  heading:
    font-family: "'Inter', -apple-system, sans-serif"
    font-weight: 800
    letter-spacing: "-0.04em"
  label:
    font-size: "0.75rem"
    font-weight: 700
    text-transform: uppercase
  code:
    font-family: "'Consolas', 'Monaco', monospace"
    font-size: "0.7rem"

spacing:
  xs: "0.4rem"
  sm: "0.75rem"
  md: "1rem"
  lg: "1.5rem"
  xl: "2rem"
  xxl: "2.5rem"

radii:
  tight: "8px"
  soft: "10px"
  card: "16px"
  large-card: "20px"
  pill: "50px"
  round: "50%"

shadows:
  card: "0 8px 32px rgba(0, 0, 0, 0.8)"
  card-soft: "0 2px 6px rgba(0, 0, 0, 0.2), inset 0 1px 0 rgba(255, 255, 255, 0.05)"
  hover-glow: "0 0 20px rgba(0, 242, 255, 0.3)"
  button: "0 4px 20px rgba(0, 242, 255, 0.3)"
  hero: "0 0 20px rgba(0, 242, 255, 0.3)"

motion:
  duration-fast: "400ms"
  duration-normal: "500ms"
  duration-slow: "700ms"
  ease-butter: "cubic-bezier(0.23, 1, 0.32, 1)"
  ease-silk: "cubic-bezier(0.25, 0.1, 0.25, 1)"
  pulse: "2s infinite"
  scan: "8s linear infinite"
  orbit: "40s linear infinite"
  drift: "30s linear infinite"
  hover-lift: "transform 0.3s ease, border-color 0.3s ease"

layout:
  navbar-height: "72px"
  card-padding: "1.5rem"
  section-gap: "3rem"
  compact-gap: "1rem"
  hero-padding: "140px 0 80px"
---

# CORS Dashboard Design System

Aitogy CORS is built around a polished, high-contrast enterprise palette that blends modern glassmorphism with cyber-tech motion. The interface is intentionally dark and immersive, with luminous cyan highlights, deep navy atmospheres, and soft gold accents for key interactive details.

## Visual Identity

- Dark base surfaces create a stable command center impression, with layered charcoal and midnight blue panels.
- Primary cyan is used as the system signal color for active data, interactive states, and motion highlights.
- Accent gold is reserved for premium emphasis and rare context cues, while muted off-white copy preserves readability.
- Status states are vivid and direct: green for online, red for offline/locked, amber for warnings, blue for in-progress operations, and purple for reboot/configuring flows.

## Surface & Material

- The design uses translucent glass cards with subtle blur, thin cyan borders, and deep shadow to separate content from the background.
- Major cards and panels float above the canvas with soft ambient shadows and occasional stronger glows for hero and interactive elements.
- Rounded rectangles and pill shapes balance mechanical data density with approachable, tactile surfaces.

## Typography

- The product uses a modern sans-serif system: Inter for headings and body text with Noto Sans as a secondary system font.
- Headings are bold and condensed, with tight letter spacing and high contrast to reinforce the enterprise dashboard feel.
- Labels and badges are small, uppercase, and wide-lettered, signaling status and metadata clearly at a glance.
- Code or numeric metadata uses monospaced styling for clarity in technical tables and device details.

## Core Component Language

- Glass cards: translucent navy panels with a blurred backdrop, cyan border accents, and upward hover lift.
- Device cards: gradient dark surfaces with subtle inset lighting, left edge status bars, and glowing active outlines.
- Hero callouts: large gradient text, scan beam animation, and pulsing badges establish a cinematic top section.
- Buttons: rounded pill forms with strong cyan gradients or ghost outlines, elevated by luminous shadows and mild hover scale.
- Badges: capsule shapes with bold color-coded borders for constellation and device state markers.

## Motion & Interaction

- Motion is smooth and energetic, using long linear animations for drift and orbit effects.
- Hover states are expressed through gentle lift, border color intensification, and luminous shadows.
- Pulse animations reinforce live signals and presence, especially for online status indicators and hero badges.
- Background motion is subtle but constant: scanning beams, drifting grid overlays, orbiting rings, and floating orbs.

## Look & Feel

The interface feels cinematic, technical, and precise. It is designed to communicate reliability and operational control while keeping the visual system distinctly modern:

- Night-vision blue world with glowing telemetry accents
- Glass-like data panels that feel both elegant and function-first
- A sense of depth through layered illumination, blurred surfaces, and animated atmosphere
- Clear hierarchy between core dashboard data, navigation, and live status indicators

## Design Intent

This system is intended for a professional CORS network management product. It should feel serious enough for mission-critical operations while still offering a futuristic, approachable brand expression. The goal is to make complex GNSS/RTK state visible and trustworthy without losing the modern polish of a premium dashboard.
