/**
 * resume-site — Main JavaScript
 *
 * All client-side interactivity for the portfolio site. Wrapped in an
 * IIFE (Immediately Invoked Function Expression) to avoid polluting
 * the global scope.
 *
 * Modules:
 *   1. Theme Toggle          — Dark/light mode with localStorage persistence
 *   2. Navbar Scroll          — IntersectionObserver for frosted glass effect
 *   3. Hamburger Menu         — Mobile navigation slide-out panel
 *   4. Portfolio Lightbox     — Full-screen image viewer with keyboard support
 *   5. Category Filtering     — Client-side portfolio filter by category
 *   6. Skill Accordion        — Expandable skill domain sections
 *   7. Star Rating            — Interactive star input for review form
 *   8. Contact Form           — Double-submit prevention
 *   9. GSAP Animations        — Scroll-triggered reveals, stat counters
 *
 * Dependencies:
 *   - GSAP 3.12.5 (loaded from CDN in base.html)
 *   - ScrollTrigger plugin (loaded from CDN in base.html)
 */

(function () {
    'use strict';

    // ============================================================
    // THEME TOGGLE
    // ============================================================

    var themeToggle = document.getElementById('themeToggle');
    var root = document.documentElement;

    function setTheme(theme) {
        root.setAttribute('data-theme', theme);
        localStorage.setItem('theme', theme);
    }

    if (themeToggle) {
        themeToggle.addEventListener('click', function () {
            var current = root.getAttribute('data-theme');
            setTheme(current === 'dark' ? 'light' : 'dark');
        });
    }

    // ============================================================
    // NAVBAR SCROLL BEHAVIOR
    // ============================================================

    var navbar = document.getElementById('navbar');
    var hero = document.getElementById('hero');

    if (navbar && hero) {
        var observer = new IntersectionObserver(
            function (entries) {
                entries.forEach(function (entry) {
                    if (entry.isIntersecting) {
                        navbar.classList.remove('navbar--scrolled');
                    } else {
                        navbar.classList.add('navbar--scrolled');
                    }
                });
            },
            { threshold: 0, rootMargin: '-64px 0px 0px 0px' }
        );
        observer.observe(hero);
    } else if (navbar) {
        navbar.classList.add('navbar--scrolled');
    }

    // ============================================================
    // HAMBURGER MENU
    // ============================================================

    var navToggle = document.getElementById('navToggle');
    var navMenu = document.getElementById('navMenu');

    if (navToggle && navMenu) {
        navToggle.addEventListener('click', function () {
            var isOpen = navMenu.classList.toggle('navbar__menu--open');
            navToggle.classList.toggle('navbar__toggle--active');
            navToggle.setAttribute('aria-expanded', isOpen);
        });

        navMenu.querySelectorAll('a').forEach(function (link) {
            link.addEventListener('click', function () {
                navMenu.classList.remove('navbar__menu--open');
                navToggle.classList.remove('navbar__toggle--active');
                navToggle.setAttribute('aria-expanded', 'false');
            });
        });
    }

    // ============================================================
    // PORTFOLIO LIGHTBOX
    // ============================================================

    var lightbox = document.getElementById('lightbox');
    if (lightbox) {
        var lightboxImg = lightbox.querySelector('.lightbox__img');
        var lightboxCaption = lightbox.querySelector('.lightbox__caption');
        var lightboxClose = lightbox.querySelector('.lightbox__close');
        var lightboxBackdrop = lightbox.querySelector('.lightbox__backdrop');

        function openLightbox(src, caption) {
            lightboxImg.src = src;
            lightboxCaption.textContent = caption || '';
            lightbox.classList.add('lightbox--active');
            document.body.style.overflow = 'hidden';
        }

        function closeLightbox() {
            lightbox.classList.remove('lightbox--active');
            document.body.style.overflow = '';
            lightboxImg.src = '';
        }

        if (lightboxClose) lightboxClose.addEventListener('click', closeLightbox);
        if (lightboxBackdrop) lightboxBackdrop.addEventListener('click', closeLightbox);

        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && lightbox.classList.contains('lightbox--active')) {
                closeLightbox();
            }
        });

        // Delegate click on portfolio items
        document.querySelectorAll('.portfolio__grid, .portfolio__featured').forEach(function (grid) {
            grid.addEventListener('click', function (e) {
                var item = e.target.closest('.portfolio__item');
                if (!item) return;

                // If clicking a case study link, let it navigate
                if (e.target.closest('.portfolio__overlay-link')) return;

                var src = item.dataset.fullSrc || item.querySelector('.portfolio__img').src;
                var title = item.querySelector('.portfolio__overlay-title');
                openLightbox(src, title ? title.textContent : '');
            });
        });
    }

    // ============================================================
    // PORTFOLIO CATEGORY FILTERING
    // ============================================================

    var filterBtns = document.querySelectorAll('.portfolio__filter-btn');
    if (filterBtns.length) {
        var portfolioItems = document.querySelectorAll('.portfolio__item');

        filterBtns.forEach(function (btn) {
            btn.addEventListener('click', function () {
                var category = this.dataset.category;

                filterBtns.forEach(function (b) { b.classList.remove('portfolio__filter-btn--active'); });
                this.classList.add('portfolio__filter-btn--active');

                portfolioItems.forEach(function (item) {
                    if (category === 'all' || item.dataset.category === category) {
                        item.style.display = '';
                    } else {
                        item.style.display = 'none';
                    }
                });
            });
        });
    }

    // ============================================================
    // SKILL DOMAIN ACCORDION
    // ============================================================

    document.querySelectorAll('.skill-domain__header').forEach(function (header) {
        header.addEventListener('click', function () {
            var body = this.nextElementSibling;
            var isOpen = body.classList.contains('skill-domain__body--open');

            this.setAttribute('aria-expanded', !isOpen);
            body.classList.toggle('skill-domain__body--open');
        });
    });

    // ============================================================
    // STAR RATING
    // ============================================================

    var starRating = document.getElementById('starRating');
    if (starRating) {
        var ratingInput = document.getElementById('ratingInput');
        var stars = starRating.querySelectorAll('.star-rating__star');
        var selectedRating = 0;

        function highlightStars(count) {
            stars.forEach(function (star, i) {
                if (i < count) {
                    star.classList.add('star-rating__star--active');
                } else {
                    star.classList.remove('star-rating__star--active');
                }
            });
        }

        stars.forEach(function (star) {
            star.addEventListener('click', function () {
                selectedRating = parseInt(this.dataset.value);
                ratingInput.value = selectedRating;
                highlightStars(selectedRating);
            });

            star.addEventListener('mouseenter', function () {
                highlightStars(parseInt(this.dataset.value));
            });

            star.addEventListener('mouseleave', function () {
                highlightStars(selectedRating);
            });
        });
    }

    // ============================================================
    // CONTACT FORM — prevent double submit
    // ============================================================

    var contactForm = document.getElementById('contactForm');
    if (contactForm) {
        contactForm.addEventListener('submit', function () {
            var btn = document.getElementById('contactSubmit');
            if (btn) {
                btn.disabled = true;
                btn.textContent = 'Sending...';
            }
        });
    }

    // ============================================================
    // GSAP ANIMATIONS
    // ============================================================

    if (typeof gsap !== 'undefined' && typeof ScrollTrigger !== 'undefined') {
        gsap.registerPlugin(ScrollTrigger);

        // Hero content animation on load
        var heroContent = document.querySelector('.hero__content');
        var heroImage = document.querySelector('.hero__image');

        if (heroContent) {
            gsap.from(heroContent.children, {
                y: 30,
                opacity: 0,
                duration: 0.8,
                stagger: 0.12,
                ease: 'power2.out',
                delay: 0.2
            });
        }

        if (heroImage) {
            gsap.from(heroImage, {
                scale: 0.9,
                opacity: 0,
                duration: 1,
                ease: 'power2.out',
                delay: 0.5
            });
        }

        // Section heading/text reveals
        gsap.utils.toArray('.section').forEach(function (section) {
            var heading = section.querySelector('.section__heading');
            var text = section.querySelector('.section__text');
            var elements = [heading, text].filter(Boolean);

            if (elements.length) {
                gsap.from(elements, {
                    y: 40,
                    opacity: 0,
                    duration: 0.8,
                    stagger: 0.15,
                    ease: 'power2.out',
                    scrollTrigger: {
                        trigger: section,
                        start: 'top 80%',
                        once: true
                    }
                });
            }
        });

        // Card stagger reveals
        var cardSelectors = [
            '.service-card', '.testimonial-card', '.project-card',
            '.cert-card', '.portfolio__item'
        ];
        cardSelectors.forEach(function (selector) {
            var cards = gsap.utils.toArray(selector);
            if (cards.length) {
                gsap.from(cards, {
                    y: 40,
                    opacity: 0,
                    duration: 0.6,
                    stagger: 0.08,
                    ease: 'power2.out',
                    scrollTrigger: {
                        trigger: cards[0].parentElement,
                        start: 'top 85%',
                        once: true
                    }
                });
            }
        });

        // Stats counter animation
        var statValues = document.querySelectorAll('.stats-bar__value');
        if (statValues.length) {
            statValues.forEach(function (el) {
                var target = parseInt(el.dataset.target) || 0;
                var obj = { val: 0 };

                ScrollTrigger.create({
                    trigger: el,
                    start: 'top 85%',
                    once: true,
                    onEnter: function () {
                        gsap.to(obj, {
                            val: target,
                            duration: 2,
                            ease: 'power2.out',
                            snap: { val: 1 },
                            onUpdate: function () {
                                el.textContent = obj.val;
                            }
                        });
                    }
                });
            });
        }

        // Page header animations
        var pageHeader = document.querySelector('.page-header');
        if (pageHeader) {
            gsap.from(pageHeader.children[0].children, {
                y: 20,
                opacity: 0,
                duration: 0.6,
                stagger: 0.1,
                ease: 'power2.out',
                delay: 0.1
            });
        }
    }

})();
