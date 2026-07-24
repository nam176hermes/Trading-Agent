import { chromium } from 'playwright';

async function test(url) {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  page.on('pageerror', err => console.log(`  PAGE_ERR: ${err.message}`));
  page.on('response', res => {
    if (res.url().includes('/api/trading/')) console.log(`  API RESP: ${res.status()} ${res.url().replace(/.*\/api/, '/api')}`);
  });

  try {
    await page.goto(url, { waitUntil: 'load', timeout: 30000 });
    await page.waitForTimeout(3000);

    // Check if React is alive
    const reactAlive = await page.evaluate(() => {
      return {
        hasReact: typeof window.React !== 'undefined',
        nextVersion: document.querySelector('[data-nextjs-version]')?.getAttribute('data-nextjs-version') || 'unknown',
        hasHydrated: document.querySelector('html')?.hasAttribute('data-nextjs-hydrated') || false,
        bodyHTML_length: document.body.innerHTML.length,
      };
    });
    console.log(`  React state: ${JSON.stringify(reactAlive)}`);

    // Try manual fetch
    const fetchResult = await page.evaluate(async () => {
      try {
        const res = await fetch('/api/trading/decisions?limit=5');
        const text = await res.text();
        return `OK: ${res.status}, len=${text.length}`;
      } catch (e) {
        return `FAIL: ${e.message}`;
      }
    });
    console.log(`  Manual fetch: ${fetchResult}`);

    // Check if self.__next_f has remaining data
    const rscState = await page.evaluate(() => {
      const f = self.__next_f;
      return {
        length: f ? f.length : 'undefined',
        sample: f && f.length > 0 ? JSON.stringify(f[0]).substring(0, 100) : 'N/A',
      };
    });
    console.log(`  RSC: len=${rscState.length} sample=${rscState.sample}`);

    // Check all script errors
    const scriptSrcs = await page.evaluate(() => {
      return Array.from(document.querySelectorAll('script[src]')).map(s => s.getAttribute('src'));
    });
    console.log(`  Scripts loaded: ${scriptSrcs.length}`);

  } catch (e) {
    console.log(`  FAILED: ${e.message}`);
  } finally {
    await browser.close();
  }
}

async function main() {
  for (const port of [3002, 3099, 3098]) {
    console.log(`\n=== :${port} /dashboard/history ===`);
    await test(`http://localhost:${port}/dashboard/history`);
  }
}
main().catch(console.error);
