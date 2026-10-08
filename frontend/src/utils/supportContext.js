/**
 * What a support ticket learns about where it was sent from, without the
 * customer typing it. Deliberately coarse: browser family + major version,
 * device class, OS family, viewport -- enough to reproduce a problem, not a
 * fingerprint. The server adds organization, plan and tenant itself.
 */
export const browserFrom = (ua = '') => {
  const tests = [
    [/Edg\/(\d+)/, 'Edge'], [/OPR\/(\d+)/, 'Opera'], [/SamsungBrowser\/(\d+)/, 'Samsung Internet'],
    [/Firefox\/(\d+)/, 'Firefox'], [/CriOS\/(\d+)/, 'Chrome'], [/Chrome\/(\d+)/, 'Chrome'],
    [/Version\/(\d+).*Safari/, 'Safari'],
  ];
  for (const [re, name] of tests) {
    const m = ua.match(re);
    if (m) return `${name} ${m[1]}`;
  }
  return 'Unknown browser';
};

export const osFrom = (ua = '') => {
  if (/Windows/.test(ua)) return 'Windows';
  if (/iPhone|iPad|iPod/.test(ua)) return 'iOS';
  if (/Android/.test(ua)) return 'Android';
  if (/Mac OS X/.test(ua)) return 'macOS';
  if (/Linux/.test(ua)) return 'Linux';
  return 'Unknown OS';
};

export const deviceFrom = (ua = '', width = 1280) => {
  if (/iPad|Tablet/.test(ua) || (/Android/.test(ua) && !/Mobile/.test(ua))) return 'Tablet';
  if (/Mobi|iPhone|Android/.test(ua) || width < 640) return 'Mobile';
  return 'Desktop';
};

export const captureContext = (from) => {
  if (typeof window === 'undefined') return {};
  const ua = window.navigator?.userAgent || '';
  const width = window.innerWidth || 0;
  // `from` is the page the customer was on when they pressed "Need help?";
  // without it, the page they are on now.
  const page = from || window.location.pathname;
  const url = from ? `${window.location.origin}${from}` : window.location.href;
  let timezone = '';
  try { timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch { /* old browser */ }
  return {
    page,
    url: url.slice(0, 500),
    browser: browserFrom(ua),
    os: osFrom(ua),
    device: deviceFrom(ua, width),
    viewport: `${width}x${window.innerHeight || 0}`,
    language: window.navigator?.language || '',
    timezone,
    client_time: new Date().toISOString(),
  };
};
