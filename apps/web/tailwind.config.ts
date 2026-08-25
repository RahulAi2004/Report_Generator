import type { Config } from 'tailwindcss';

/**
 * Design tokens read off the UI reference: navy rail, single blue accent,
 * hairline borders, dense rows. Deliberately narrow -- an enterprise BI tool
 * should look like one system, not a component gallery.
 */
const config: Config = {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        rail: {
          DEFAULT: '#0F2137',
          hover: '#1A2E48',
          active: '#1B5FDB',
          muted: '#8A9BB0',
        },
        accent: {
          DEFAULT: '#1B5FDB',
          hover: '#1750BC',
          soft: '#EEF4FF',
          border: '#C3D8FF',
        },
        ink: {
          DEFAULT: '#12202F',
          muted: '#5A6B7F',
          faint: '#8595A6',
        },
        line: {
          DEFAULT: '#E3E8EF',
          strong: '#CFD8E3',
        },
        canvas: '#F5F7FA',
        danger: { DEFAULT: '#C0392B', soft: '#FDEDEA', border: '#F5C6C0' },
        warn: { DEFAULT: '#B7791F', soft: '#FEF7E7', border: '#F3DFAE' },
        good: { DEFAULT: '#1E7A4C', soft: '#E9F7F0', border: '#B6E2CC' },
      },
      fontSize: {
        '2xs': ['10px', '14px'],
        xs: ['11px', '16px'],
        sm: ['12px', '18px'],
        base: ['13px', '20px'],
        md: ['14px', '21px'],
      },
      borderRadius: { DEFAULT: '6px', md: '6px', lg: '8px' },
      boxShadow: {
        panel: '0 1px 2px rgba(18, 32, 47, 0.04)',
        pop: '0 8px 24px rgba(18, 32, 47, 0.12)',
      },
      spacing: { row: '34px' },
    },
  },
  plugins: [],
};

export default config;
