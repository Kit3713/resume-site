# resume-site Roadmap

## Project Vision

A self-hosted, containerized personal resume and portfolio website built with Flask. Designed as a "you are the product" sales-style site with an Apple-inspired aesthetic. All personal data lives outside the repository — the public repo is the engine, a private fork holds your configuration and content.

---

## Architecture Overview

- **Backend:** Flask + Gunicorn
- **Database:** SQLite (reviews, analytics, content, settings)
- **Frontend:** Jinja2 templates, CSS (custom properties for theming), GSAP (scroll animations)
- **Container:** Podman / OCI-compliant image with Containerfile
- **Reverse Proxy:** Caddy (external, not bundled)
- **Admin Access:** Local/Tailscale network only — admin routes are IP-restricted, then protected by single-user login
- **Config:** YAML for infrastructure settings (SMTP, secret key), admin panel for all content and display settings

---

## Design Specifications

### Visual Identity

- **Style:** Apple-inspired — clean, bold, generous whitespace, smooth scroll animations
- **Color Scheme:** Dark theme with accent color, dark/light mode toggle for visitors
- **Typography:** Mix — bold display font for headings (e.g., Clash Display, Cabinet Grotesk), clean sans-serif body (Inter)
- **Logo/Branding:** Configurable via admin — upload a logo, use styled initials, or just a site title
- **Favicon:** Configurable via admin upload

### Navigation

- Fixed top navbar, transparent over hero section, gains subtle background on scroll
- Hamburger collapse on mobile
- Hybrid layout: landing page with scroll sections + detail pages for portfolio, case studies, projects

### Animations (GSAP)

- Scroll-triggered section reveals (fade + slide)
- Parallax depth effects on hero and section backgrounds
- Animated stat counters on scroll
- Page transition animations between routes
- Hover effects on portfolio grid items

### Responsive Design

- Mobile and desktop treated as equal priority
- Touch-friendly interactions for portfolio gallery
- Responsive grid layouts throughout

---

## Page Structure

### Landing Page (scroll sections)

1. **Hero** — Split layout: text (name, title, one-liner value prop) + image/headshot. Subtle pattern or animation in background. Availability status badge (admin-toggled: available / not available / open to opportunities).
2. **About** — Short professional narrative
3. **Stats Bar** — Animated counters (deployments, years experience, etc.). Admin-configurable: add/remove/edit stats, or toggle entire section off.
4. **Services Overview** — Cards summarizing what you do, linking to detail page
5. **Featured Portfolio** — Top 2-3 blown-up portfolio items from the grid
6. **Featured Testimonials** — Top-tier reviews displayed large
7. **Contact CTA** — Call to action linking to contact page
8. **Footer** — Social icons (GitHub, LinkedIn, etc., configurable), copyright, minimal links

### Detail Pages

- **/portfolio** — Full photo gallery (masonry grid). Three-tier photo interaction:
  - **No metadata:** Click to enlarge only
  - **Has caption/description:** Hover to expand with overlay info (title, description, tech used)
  - **Has case study:** Hover shows overview, click goes to dedicated case study page
  - Top/featured photos blown up at the top of the page
  - Category filtering (admin-defined categories: racks, cable runs, panels, etc.)
- **/portfolio/\<slug\>** — Case study detail page (problem / solution / result format). Page only exists if case studies exist; toggleable on/off from admin.
- **/services** — Expanded service descriptions with expandable skill cards
- **/skills** — Interactive expandable cards grouped by domain. Click to reveal experience details, tools, context.
- **/projects** — Technical projects (Ironclad, homelab, etc.). Each project is either a GitHub link with description, or a dedicated subpage with screenshots and writeup. Configurable per project from admin.
- **/projects/\<slug\>** — Dedicated project page (when enabled for that project)
- **/testimonials** — All visible reviews. Display toggle per review:
  - **Featured:** Large, top of page
  - **Standard:** Normal card display
  - **Hidden:** Not shown
  - Reviews tagged as "professional recommendation" or "client review" — admin chooses display mode: separate sections, mixed with label, or all together
- **/certifications** — Cert badges with images (CompTIA, etc.), descriptions, dates. Admin-managed.
- **/contact** — Contact form (name, email, message) with honeypot spam protection. Configurable contact info display: email, phone, LinkedIn, GitHub — each individually toggleable visible/hidden from admin.
- **/resume** — PDF resume download. Admin-toggled: public download, private link only, or disabled entirely.

---

## Admin Panel (local/Tailscale access only)

### Access Model

- Admin routes only respond to private IP ranges (10.x, 192.168.x, 100.64.x for Tailscale)
- Single admin account with hashed password
- Session-based auth via Flask-Login

### Admin Features

- **Dashboard** — Simple analytics: page views, visitor counts, popular pages, recent contact form submissions
- **Content Editor** — Edit section text via rich text editor (Quill.js — lightweight, no dependencies). Page structure stays fixed in templates; text content stored in database.
- **Photo Manager** — Upload photos, assign to categories, set metadata (title, description, tech used), attach case study content, set display tier (featured / grid / hidden). Photos auto-processed on upload: original stored, optimized + thumbnail generated via Pillow.
- **Review Manager** — View all submitted reviews, set display tier (featured / standard / hidden), set type (recommendation / client review), approve or reject pending submissions
- **Token Generator** — Create invite tokens for review submission, tag as recommendation or client review type
- **Settings Panel:**
  - Site title, tagline, about text
  - Logo upload or initials config
  - Favicon upload
  - Contact visibility toggles (email, phone, social links — each on/off)
  - Contact form on/off
  - Resume PDF upload and visibility toggle (public / private link / off)
  - Availability status toggle
  - Stats section toggle and stat editor
  - Case study page toggle
  - Dark/light mode default (visitors can still toggle)
  - Testimonial display mode (separate sections / mixed with tags)
  - SMTP test button
- **Account** — Change admin password

---

## Review / Testimonial System

- Admin generates a unique token, tagged as "recommendation" or "client review"
- Token encodes a URL: `yoursite.com/review/<token>`
- Visitor sees a simple form: name, title/role, relationship, message, optional star rating
- Submission stored as pending in SQLite
- Admin approves/rejects from the review manager
- Approved reviews appear on the testimonials page according to their display tier and type

---

## Contact Form

- Fields: name, email, message
- Honeypot hidden field for bot detection
- Rate limiting (Flask-Limiter or simple in-app counter)
- On submit: sends email to admin's personal address via SMTP relay
- SMTP config in YAML (host, port, user, password, recipient)
- Submissions also logged to SQLite for admin dashboard view

---

## Analytics (built-in)

- Lightweight page view counter — middleware logs each request to SQLite
- Stores: page path, timestamp, referrer, user agent
- Admin dashboard shows: total views, views per page, views over time (simple chart), recent visitors
- No cookies, no tracking scripts, no third-party services
- Minimal storage footprint — optional auto-purge of records older than N days

---

## SEO

- Meta tags (title, description) per page, configurable from admin
- Open Graph tags for rich link previews (LinkedIn, Discord, etc.)
- Semantic HTML throughout
- Sitemap.xml auto-generated from active pages
- robots.txt

---

## Configuration Layers

### YAML config file (`config.yaml`) — infrastructure only

```yaml
secret_key: "generate-a-random-key"
database_path: "/app/data/site.db"
photo_storage: "/app/photos"
smtp:
  host: "smtp.gmail.com"
  port: 587
  user: "you@gmail.com"
  password: "app-password"
  recipient: "you@gmail.com"
admin:
  username: "admin"
  password_hash: "pbkdf2:sha256:..."
  allowed_networks:
    - "10.0.0.0/8"
    - "192.168.0.0/16"
    - "100.64.0.0/10"
```

### Admin panel — everything else

All content, display settings, toggles, and media managed through the browser-based admin interface. Changes take effect immediately without container restart.

---

## Container Deployment

### Containerfile

- Base image: `python:3.12-slim`
- Install: Flask, Gunicorn, Pillow, PyYAML, Flask-Login
- Copy application code
- Expose port 8080
- Entrypoint: Gunicorn

### Volumes (bind mounts)

| Volume | Container Path | Purpose |
|--------|---------------|---------|
| `config.yaml` | `/app/config.yaml` | Infrastructure config (read-only) |
| `photos/` | `/app/photos` | Original + optimized images |
| `data/` | `/app/data` | SQLite database |

### Caddy integration

```
portfolio.yourdomain.com {
    reverse_proxy localhost:8080
}
```

---


## Build Phases

### Phase 1 — Foundation *(complete)*

- [x] Flask app skeleton with Gunicorn
- [x] YAML config loading
- [x] SQLite database schema and initialization
- [x] Base template with nav, footer, dark/light mode toggle
- [x] CSS custom properties for theming (dark + light)
- [x] Landing page hero section (split layout)
- [x] Containerfile + basic Podman deployment
- [x] Admin IP restriction middleware
- [x] Admin login (Flask-Login, single user)

### Phase 2 — Public Pages *(complete)*

- [x] About section with configurable content
- [x] Services page with expandable skill cards
- [x] Stats bar with GSAP animated counters
- [x] Portfolio gallery (masonry grid, three-tier interaction)
- [x] Testimonials page (featured + standard display)
- [x] Contact page with form + honeypot
- [x] SMTP relay for contact form
- [x] Certifications display with badge images
- [x] Projects page (GitHub links + optional detail pages)
- [x] Resume PDF download with visibility toggle
- [x] GSAP scroll animations throughout
- [x] Page transition animations
- [x] Responsive / mobile layout pass

### Phase 3 — Admin Panel *(next up)*

- [ ] Admin dashboard with analytics overview
- [ ] Content editor (Quill.js rich text for section content)
- [ ] Photo upload with Pillow processing (originals + optimized)
- [ ] Photo manager (categories, metadata, tiers)
- [ ] Review manager (approve/reject, set tiers and types)
- [ ] Token generator for review invites
- [ ] Settings panel (all toggles)

### Phase 4 — Polish

- [x] Case study detail pages
- [x] Project detail pages
- [ ] SEO meta tags, Open Graph, sitemap.xml
- [ ] Analytics auto-purge
- [ ] Final mobile/responsive QA
- [ ] README + documentation polish
- [ ] Container final build and test

---

## Future Considerations (not in scope)

- Multiple admin / viewer accounts
- Public-facing login
- i18n / multilingual support
- Blog / articles section
- API endpoints for headless usage
- Automated backups
