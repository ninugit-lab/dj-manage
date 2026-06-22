/**
 * DJ Wishlist Tailwind config — Design-Tokens (Evolution der bestehenden Marke).
 */
module.exports = {
    content: [
        '../templates/**/*.html',
        '../../templates/**/*.html',
        '../../**/templates/**/*.html',
        '../../**/*.py',
    ],
    theme: {
        extend: {
            colors: {
                bg: '#06060e',
                surface: 'rgba(12,12,24,.85)',
                card: 'rgba(16,16,32,.7)',
                'card-solid': '#10102a',
                border: { DEFAULT: 'rgba(40,40,80,.45)', hover: 'rgba(70,70,140,.5)' },
                accent: { DEFAULT: '#3b82f6', 400: '#60a5fa', 600: '#2563eb', glow: 'rgba(59,130,246,.25)' },
                accent2: { DEFAULT: '#a855f7', glow: 'rgba(168,85,247,.2)' },
                success: '#22c55e',
                warn: '#eab308',
                danger: '#ef4444',
                text: { DEFAULT: '#e4e4ef', bright: '#f4f4ff' },
                // A11y-Fix: >=4.5:1 Kontrast auf bg
                muted: 'rgba(176,176,214,.92)',
                subtle: 'rgba(148,148,190,.7)',
            },
            fontFamily: {
                display: ["'Bebas Neue'", 'sans-serif'],
                sans: ["'DM Sans'", 'sans-serif'],
                mono: ["'Space Mono'", 'monospace'],
            },
            borderRadius: { DEFAULT: '10px', sm: '6px' },
            boxShadow: {
                'glass-sm': '0 2px 12px rgba(0,0,0,.2)',
                'glass-lg': '0 12px 40px rgba(0,0,0,.45)',
            },
            zIndex: { dropdown: '10', sticky: '20', modal: '100', drawer: '150', toast: '200' },
        },
    },
    plugins: [
        require('@tailwindcss/forms'),
        require('@tailwindcss/typography'),
        require('@tailwindcss/aspect-ratio'),
    ],
}
