/**
 * resume-site — Main JavaScript
 * Handles: theme toggle, navbar scroll, hamburger menu, GSAP animations
 */

(function () {
    'use strict';

    // ============================================================
    // THEME TOGGLE
    // ============================================================

    const themeToggle = document.getElementById('themeToggle');
    const root = document.documentElement;

    function setTheme(theme) {
        root.setAttribute('data-theme', theme);
        localStorage.setItem('theme', theme);
    }

    if (themeToggle) {
        themeToggle.addEventListener('click', function () {
            const current = root.getAttribute('data-theme');
            setTheme(current === 'dark' ? 'light' : 'dark');
        });
    }

    // ============================================================
    // NAVBAR SCROLL BEHAVIOR
    // ============================================================

    const navbar = document.getElementById('navbar');
    const hero = document.getElementById('hero');

    if (navbar && hero) {
        const observer = new IntersectionObserver(
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
        // No hero section (e.g., admin pages) — always show scrolled navbar
        navbar.classList.add('navbar--scrolled');
    }

    // ============================================================
    // HAMBURGER MENU
    // ============================================================

    const navToggle = document.getElementById('navToggle');
    const navMenu = document.getElementById('navMenu');

    if (navToggle && navMenu) {
        navToggle.addEventListener('click', function () {
            const isOpen = navMenu.classList.toggle('navbar__menu--open');
            navToggle.classList.toggle('navbar__toggle--active');
            navToggle.setAttribute('aria-expanded', isOpen);
        });

        // Close menu when clicking a link
        navMenu.querySelectorAll('a').forEach(function (link) {
            link.addEventListener('click', function () {
                navMenu.classList.remove('navbar__menu--open');
                navToggle.classList.remove('navbar__toggle--active');
                navToggle.setAttribute('aria-expanded', 'false');
            });
        });
    }

    // ============================================================
    // GSAP SCROLL ANIMATIONS
    // ============================================================

    if (typeof gsap !== 'undefined' && typeof ScrollTrigger !== 'undefined') {
        gsap.registerPlugin(ScrollTrigger);

        // Reveal animations for sections
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
    }

})();
