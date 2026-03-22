import { h } from 'preact';

export function FilamentIQLogo({ height = 28, showWordmark = true }) {
  const iconSize = height;
  const hubFill = '#111113'; // match card dark bg (--bg-0)

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: '8px',
      lineHeight: 1,
    }}>
      {/* Icon mark */}
      <svg
        viewBox="0 0 72 72"
        width={iconSize}
        height={iconSize}
        style={{ flexShrink: 0 }}
        aria-hidden="true"
      >
        {/* Outer rim */}
        <circle cx="36" cy="36" r="32" fill="none" stroke="#5B8AF0" strokeWidth="3.5"/>
        {/* Filament fill — wound annular area */}
        <path d="M36 6 A30 30 0 1 1 35.99 6Z" fill="none" stroke="#F97316" strokeWidth="11" opacity="0.9"/>
        {/* Hub */}
        <circle cx="36" cy="36" r="11" fill={hubFill} stroke="#5B8AF0" strokeWidth="2.5"/>
        {/* Spokes */}
        <line x1="36" y1="4" x2="36" y2="25" stroke="#5B8AF0" strokeWidth="2.5" strokeLinecap="round"/>
        <line x1="63.7" y1="52" x2="47.6" y2="42.5" stroke="#5B8AF0" strokeWidth="2.5" strokeLinecap="round"/>
        <line x1="8.3" y1="52" x2="24.4" y2="42.5" stroke="#5B8AF0" strokeWidth="2.5" strokeLinecap="round"/>
        {/* Center axle */}
        <circle cx="36" cy="36" r="4.5" fill="#5B8AF0"/>
        {/* Filament tail */}
        <path d="M65 19 Q71 11 69 5" fill="none" stroke="#F97316" strokeWidth="2.5" strokeLinecap="round" opacity="0.75"/>
        <circle cx="69" cy="5" r="3" fill="#F97316" opacity="0.75"/>
      </svg>

      {/* Wordmark — only shown when showWordmark=true */}
      {showWordmark && (
        <span style={{
          display: 'flex',
          flexDirection: 'column',
          gap: '1px',
          lineHeight: 1,
        }}>
          <span style={{
            fontSize: '13px',
            fontWeight: 700,
            color: '#ffffff',
            letterSpacing: '-0.2px',
            lineHeight: 1,
          }}>
            Filament
          </span>
          <span style={{
            fontSize: '11px',
            fontWeight: 300,
            color: '#5B8AF0',
            letterSpacing: '3px',
            lineHeight: 1,
          }}>
            IQ
          </span>
        </span>
      )}
    </div>
  );
}
