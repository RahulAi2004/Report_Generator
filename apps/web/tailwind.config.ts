import type { Config } from 'tailwindcss';

/**
 * Design tokens.
 *
 * Brighter and higher-contrast than the reference: more white, a more saturated
 * accent, and darker muted text. The original grey-on-grey read as washed out on
 * ordinary office monitors, and a reporting tool is looked at all day.
 *
 * Body text is 13.5px rather than 13px, and every muted tone was darkened until
 * it cleared WCAG AA against its background.
 */
const config: Config = {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        rail: {
          DEFAULT: '#12263F',
          hover: '#1E3A5C',
          active: '#2563EB',
          muted: '#A9BDD4',
        },
        accent: {
          DEFAULT: '#2563EB',
          hover: '#1D4ED8',
          soft: '#EFF5FF',
          softer: '#F7FAFF',
          border: '#BBD3FF',
          strong: '#1E40AF',
        },
        ink: {
          DEFAULT: '#0F1B2A',
          muted: '#4A5B70',
          faint: '#6B7C92',
        },
        line: {
          DEFAULT: '#E4E9F0',
          strong: '#C6D0DE',
        },
        canvas: '#F7F9FC',
        danger: { DEFAULT: '#D02F2F', soft: '#FEF1F0', border: '#F6BEB8' },
        warn: { DEFAULT: '#B45309', soft: '#FFF8EB', border: '#F5D9A6' },
        good: { DEFAULT: '#0F7A47', soft: '#EDFBF4', border: '#A8E3C6' },
        info: { DEFAULT: '#0E7490', soft: '#ECFAFE', border: '#A5DFF0' },
        // Category accents, so a long table list is scannable by colour.
        cat: {
          sales: '#2563EB',
          customers: '#7C3AED',
          artwork: '#DB2777',
          payments: '#0F7A47',
          purchasing: '#B45309',
          fulfillment: '#0E7490',
          people: '#4F46E5',
          system: '#64748B',
        },
      },
      fontSize: {
        '2xs': ['10.5px', '15px'],
        xs: ['11.5px', '16px'],
        sm: ['12.5px', '18px'],
        base: ['13.5px', '20px'],
        md: ['15px', '22px'],
        lg: ['17px', '24px'],
      },
      borderRadius: { DEFAULT: '7px', md: '7px', lg: '10px' },
      boxShadow: {
        panel: '0 1px 2px rgba(15, 27, 42, 0.05), 0 1px 3px rgba(15, 27, 42, 0.04)',
        raised: '0 2px 4px rgba(15, 27, 42, 0.06), 0 4px 12px rgba(15, 27, 42, 0.05)',
        pop: '0 10px 32px rgba(15, 27, 42, 0.16)',
      },
      spacing: { row: '36px' },
    },
  },
  plugins: [],
};

export default config;
