/**
 * drawer.js — Off-Canvas Sidebar Drawer (mobile)
 * A11y: aria-expanded, focus-trap, ESC, backdrop click, return focus
 */
(function () {
  'use strict';

  /** Focusable element selectors for focus-trap */
  const FOCUSABLE = [
    'a[href]',
    'button:not([disabled])',
    'input:not([disabled])',
    'select:not([disabled])',
    'textarea:not([disabled])',
    '[tabindex]:not([tabindex="-1"])',
  ].join(',');

  let sidebar = null;
  let overlay = null;
  let toggle = null;
  let isOpen = false;

  function getFocusableElements() {
    return Array.from(sidebar.querySelectorAll(FOCUSABLE)).filter(
      (el) => !el.closest('[hidden]') && (el.offsetParent !== null || getComputedStyle(el).position === 'fixed')
    );
  }

  function openDrawer() {
    isOpen = true;
    sidebar.classList.add('open');
    overlay.classList.add('open');
    overlay.removeAttribute('hidden');
    toggle.setAttribute('aria-expanded', 'true');
    toggle.setAttribute('aria-label', 'Menü schließen');
    document.body.style.overflow = 'hidden';

    // Move focus to first focusable element in sidebar
    const focusable = getFocusableElements();
    if (focusable.length > 0) {
      focusable[0].focus();
    }
  }

  function closeDrawer() {
    isOpen = false;
    sidebar.classList.remove('open');
    overlay.classList.remove('open');
    overlay.setAttribute('hidden', '');
    toggle.setAttribute('aria-expanded', 'false');
    toggle.setAttribute('aria-label', 'Menü öffnen');
    document.body.style.overflow = '';

    // Return focus to the toggle button
    toggle.focus();
  }

  function trapFocus(e) {
    if (!isOpen) return;
    const focusable = getFocusableElements();
    if (focusable.length === 0) return;

    const first = focusable[0];
    const last = focusable[focusable.length - 1];

    if (e.key === 'Tab') {
      if (e.shiftKey) {
        // Shift+Tab: if focus is on first element, wrap to last
        if (document.activeElement === first) {
          e.preventDefault();
          last.focus();
        }
      } else {
        // Tab: if focus is on last element, wrap to first
        if (document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }
  }

  function onKeyDown(e) {
    if (e.key === 'Escape' && isOpen) {
      closeDrawer();
    }
    trapFocus(e);
  }

  function isMobile() {
    return window.innerWidth <= 768;
  }

  function init() {
    sidebar = document.querySelector('.sidebar');
    overlay = document.querySelector('.sidebar-overlay');
    toggle = document.getElementById('drawer-toggle');

    if (!sidebar || !overlay || !toggle) return;

    // Initial hidden state for overlay (accessible)
    overlay.setAttribute('hidden', '');

    // Toggle button click
    toggle.addEventListener('click', function () {
      if (isOpen) {
        closeDrawer();
      } else {
        openDrawer();
      }
    });

    // Backdrop click closes drawer
    overlay.addEventListener('click', closeDrawer);

    // ESC key + focus trap
    document.addEventListener('keydown', onKeyDown);

    // Close drawer when a nav link is clicked on mobile
    sidebar.querySelectorAll('.nav-link').forEach(function (link) {
      link.addEventListener('click', function () {
        if (isMobile() && isOpen) {
          closeDrawer();
        }
      });
    });

    // On resize to desktop: reset state
    window.addEventListener('resize', function () {
      if (!isMobile() && isOpen) {
        // Reset without returning focus (user resized window)
        isOpen = false;
        sidebar.classList.remove('open');
        overlay.classList.remove('open');
        overlay.setAttribute('hidden', '');
        toggle.setAttribute('aria-expanded', 'false');
        toggle.setAttribute('aria-label', 'Menü öffnen');
        document.body.style.overflow = '';
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
