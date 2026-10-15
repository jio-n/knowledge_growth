// Standalone file preview verification. No app server, database, or AI connection.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const preview = path.resolve(process.argv[2] || 'reading-output/demo/2601.00001v2/english_preview.html');
  const output = path.resolve(process.argv[3] || 'reading-output/browser');
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium', args: ['--no-sandbox'] });
  const context = await browser.newContext({ offline: true });
  // Serve local bytes through Playwright while Chromium networking stays offline.
  // Managed Chromium disallows file://; no HTTP server or outbound fetch is needed.
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'http://127.0.0.1:8765') return route.abort();
    const local = path.resolve(path.dirname(preview), '.' + decodeURIComponent(url.pathname));
    if (!local.startsWith(path.dirname(preview) + path.sep)) return route.abort();
    await route.fulfill({path: local});
  });
  const page = await context.newPage();
  const errors = [], remote = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('request', req => { if (/^https?:/.test(req.url()) && !req.url().startsWith('http://127.0.0.1:8765/')) remote.push(req.url()); });
  page.on('dialog', dialog => { errors.push('Unexpected dialog'); dialog.dismiss(); });
  const checks = [];
  try {
    for (const viewport of [{width: 1280, height: 1000}, {width: 390, height: 844}]) {
      await page.setViewportSize(viewport);
      await page.goto('http://127.0.0.1:8765/english_preview.html');
      await page.locator('main img').evaluateAll(images => Promise.all(images.map(i => i.decode())));
      const check = await page.evaluate(() => ({
        width: innerWidth,
        images: [...document.querySelectorAll('main img')].map(i => ({loaded: i.complete && i.naturalWidth > 0, width: i.naturalWidth})),
        mergedCells: document.querySelectorAll('main [rowspan="2"], main [colspan="2"]').length,
        math: document.querySelectorAll('main math').length,
        internalLinks: [...document.querySelectorAll('main a[href^="#"]')].every(a => document.getElementById(a.hash.slice(1))),
        overflow: document.documentElement.scrollWidth > innerWidth,
        pwned: Boolean(window.PWNED),
        headings: [...document.querySelectorAll('main h1,main h2')].map(h => h.textContent)
      }));
      assert(check.images.length > 0 && check.images.every(i => i.loaded));
      assert(check.mergedCells >= 2 && check.math >= 1 && check.internalLinks);
      assert(!check.overflow && !check.pwned);
      assert(check.headings.some(h => h.includes('Abstract')) && check.headings.some(h => h.includes('Appendix')) && check.headings.includes('References'));
      checks.push(check);
      await page.screenshot({path: path.join(output, `english-${viewport.width}.png`), fullPage: true});
    }
    assert.deepEqual(remote, []);
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'browser-report.json'), JSON.stringify({fixture_only: true, offline: true, checks, remoteRequests: remote, errors}, null, 2) + '\n');
    console.log(JSON.stringify({offline: true, viewports: checks.length, errors}));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
