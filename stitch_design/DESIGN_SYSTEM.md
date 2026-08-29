---
name: Telephony Logic Core
colors:
  surface: '#f8f9ff'
  surface-dim: '#cbdbf5'
  surface-bright: '#f8f9ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#eff4ff'
  surface-container: '#e5eeff'
  surface-container-high: '#dce9ff'
  surface-container-highest: '#d3e4fe'
  on-surface: '#0b1c30'
  on-surface-variant: '#45464d'
  inverse-surface: '#213145'
  inverse-on-surface: '#eaf1ff'
  outline: '#76777d'
  outline-variant: '#c6c6cd'
  surface-tint: '#565e74'
  primary: '#000000'
  on-primary: '#ffffff'
  primary-container: '#131b2e'
  on-primary-container: '#7c839b'
  inverse-primary: '#bec6e0'
  secondary: '#0051d5'
  on-secondary: '#ffffff'
  secondary-container: '#316bf3'
  on-secondary-container: '#fefcff'
  tertiary: '#000000'
  on-tertiary: '#ffffff'
  tertiary-container: '#002113'
  on-tertiary-container: '#009668'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#dae2fd'
  primary-fixed-dim: '#bec6e0'
  on-primary-fixed: '#131b2e'
  on-primary-fixed-variant: '#3f465c'
  secondary-fixed: '#dbe1ff'
  secondary-fixed-dim: '#b4c5ff'
  on-secondary-fixed: '#00174b'
  on-secondary-fixed-variant: '#003ea8'
  tertiary-fixed: '#6ffbbe'
  tertiary-fixed-dim: '#4edea3'
  on-tertiary-fixed: '#002113'
  on-tertiary-fixed-variant: '#005236'
  background: '#f8f9ff'
  on-background: '#0b1c30'
  surface-variant: '#d3e4fe'
typography:
  display-lg:
    fontFamily: Inter
    fontSize: 32px
    fontWeight: '700'
    lineHeight: 40px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.01em
  headline-sm:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 24px
  body-lg:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  body-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  body-sm:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 16px
  label-caps:
    fontFamily: Inter
    fontSize: 11px
    fontWeight: '600'
    lineHeight: 16px
    letterSpacing: 0.05em
  code-md:
    fontFamily: JetBrains Mono
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 20px
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  unit: 4px
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 32px
  gutter: 16px
  margin-page: 24px
  max-width: 1440px
---

## Brand & Style

This design system is engineered for high-stakes commercial telephony management. The brand personality is hyper-rational, authoritative, and invisible. It prioritizes cognitive efficiency over aesthetic flourish, adopting a **Modern Enterprise SaaS** style with a focus on **Minimalism** and **Logical Density**.

The UI should evoke a sense of absolute control and stability. Every pixel must serve a functional purpose. The "logical to the point of absurdity" approach manifests in a strict adherence to a modular grid, where every element is aligned to a predictable rhythm, ensuring that users can navigate complex routing logic and PBX configurations with zero friction.

- **Minimalism:** No unnecessary shadows or gradients. Whitespace is used as a functional separator.
- **Precision:** High-contrast borders and clear demarcations between control zones.
- **Utility:** All interactive elements must have a distinct, unmistakable state.

## Colors

The palette is anchored in **Deep Slate** (#0F172A) for core structural elements and navigation, providing a heavy, stable foundation. **Professional Blue** (#2563EB) serves as the primary action color, used for CTA buttons, active states, and focus indicators.

Functional colors are absolute:
- **Emerald Green (#10B981):** Reserved exclusively for "Live," "Connected," or "System Healthy" statuses.
- **Orange (#F97316):** Used for alerts, warnings, and pending actions. This is preferred over red to maintain a professional, calm environment unless a critical system failure occurs.
- **Neutral Greys:** A strict scale from #F8FAFC (Background) to #64748B (Secondary Text) to manage information density without overwhelming the eye.

## Typography

The design system utilizes **Inter** for all UI text to ensure maximum legibility at small sizes and high-density environments. For technical data, such as SIP logs, phone numbers, and extension IDs, **JetBrains Mono** is utilized to provide a clear distinction between narrative labels and system-generated data.

- **Headlines:** Keep short and descriptive. 
- **Labels:** Use `label-caps` for table headers and section titles to create a strong visual hierarchy.
- **Mobile Adaptation:** For screens smaller than 768px, `display-lg` should scale down to 24px to ensure headers do not wrap excessively.

## Layout & Spacing

The layout follows a **Fixed-Fluid Hybrid Grid**. 
- **Side Navigation:** Fixed at 260px for desktop. 
- **Main Content:** A 12-column fluid grid with 16px gutters and 24px outer margins. 
- **Spacing Rhythm:** All spacing must be a multiple of 4px. Use `md` (16px) for standard padding within cards and `lg` (24px) for spacing between major sections.

**Breakpoints:**
- **Desktop (1024px+):** Full 12-column view.
- **Tablet (768px - 1023px):** Side navigation collapses to an icon bar (64px), content expands.
- **Mobile (Below 768px):** Single column, margins reduced to 16px. Side navigation moves to a bottom bar or hamburger menu.

## Elevation & Depth

To maintain a "logical" and flat aesthetic, the design system avoids ambient shadows. Instead, it uses **Tonal Layering** and **Low-Contrast Outlines**.

- **Level 0 (Background):** #F8FAFC. The canvas.
- **Level 1 (Cards/Surface):** #FFFFFF with a 1px solid border (#E2E8F0).
- **Level 2 (Dropdowns/Popovers):** #FFFFFF with a slightly darker 1px border (#CBD5E1) and a very tight 4px blur shadow with 5% opacity to distinguish it from the card layer.
- **Active State:** Use a 2px left-border accent in Professional Blue to indicate the currently selected item in a list or navigation bar.

## Shapes

The shape language is "Soft" (0.25rem/4px radius) to maintain a modern feel without appearing overly consumer-focused. 

- **Interactive Elements:** Buttons and inputs use 4px (`rounded-sm`).
- **Containers:** Unified cards and side panels use 8px (`rounded-lg`) for a clear structural containment.
- **Status Badges:** Use 2px radius or a full pill shape depending on the context—standard badges are 4px, while "Live" indicators are full pills.

## Components

### Buttons
- **Primary:** Solid Deep Slate or Professional Blue, white text. No gradient.
- **Secondary:** White background, 1px Deep Slate border, Deep Slate text.
- **Ghost:** No border, transparent background, Professional Blue text.

### Unified Cards
All content modules must be housed in cards. Cards have a 1px #E2E8F0 border. Headers within cards should have a subtle #F8FAFC background to separate metadata from the content body.

### Data Tables
- **Header:** `label-caps` typography with a light grey background (#F1F5F9).
- **Rows:** 48px height minimum. Zebra striping is not used; use subtle 1px bottom borders.
- **Status:** Status badges are placed in the first or last column, right-aligned for numeric data, left-aligned for strings.

### Input Fields
Strict 40px height. Use 1px #CBD5E1 borders. Focus state is a 2px Professional Blue ring.

### Side Navigation
Dark themed (#0F172A). Active items are highlighted with a Professional Blue background at 10% opacity and a 2px solid blue left border. Icons must be 20px, stroke-based, and simplified.

### Status Badges
- **Active:** Emerald background (10% opacity) with Emerald text.
- **Alert:** Orange background (10% opacity) with Orange text.
- **Inactive:** Grey background (10% opacity) with Grey text.