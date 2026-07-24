import { chromium } from 'playwright';

async function deepTest(baseUrl, path) {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();

  const errors = [];
  page.on('pageerror', err => errors.push(err.message));
  page.on('console', msg => {
    if (msg.type() === 'error') errors.push(msg.text());
  });

  const apiRequests = [];
  page.on('response', res => {
    const url = res.url();
    if (url.includes('/api/trading/')) {
      apiRequests.push(`${res.status()} ${url.replace(/^.*\/api/, '/api')}`);
    }
  });

  try {
    const url = `${baseUrl}${path}`;
    console.log(`\n=== ${url} ===`);
    await page.goto(url, { waitUntil: 'load', timeout: 30000 });
    await page.waitForTimeout(5000);

    console.log(`API calls: ${apiRequests.length > 0 ? apiRequests.join(', ') : 'none'}`);

    // Find any loading/empty states
    const bodyText = await page.textContent('body');
    const loadingMatch = bodyText.match(/Loading[^.]*/);
    const noDataMatch = bodyText.match(/No (Data|Decisions|Signal|Risk)[^.]*/);
    if (loadingMatch) console.log(`Loading state: "${loadingMatch[0]}"`);
    if (noDataMatch) console.log(`Empty state: "${noDataMatch[0]}"`);

    // Interactive elements
    const buttons = await page.locator('button').count();
    console.log(`Buttons: ${buttons}`);

    // RSC state
    const rsc = await page.evaluate(() => ({
      next_f_length: self.__next_f ? self.__next_f.length : 'undefined',
      next_r: self.__next_r || 'undefined',
    }));
    console.log(`RSC: next_f.length=${rsc.next_f_length}, next_r=${rsc.next_r?.substring(0,20)}...`);

    // Check for Next.js error overlay
    const overlay = await page.locator('[data-nextjs-dialog]').count();
    console.log(`Error overlays: ${overlay}`);

    // Take body text sample
    console.log(`Body first 300 chars: ${bodyText.substring(0, 300).replace(/\n/g, ' ')}`);

    if (errors.length) console.log(`ERRORS: ${errors.slice(0,5).join(' | ')}`);

  } catch (e) {
    console.log(`FAILED: ${e.message}`);
  } finally {
    await browser.close();
  }
}

async function main() {
  for (const path of ['/dashboard/history', '/dashboard', '/dashboard/signals', '/dashboard/risk', '/dashboard/plan']) {
    await deepTest('http://localhost:3099', path);
  }
}

main().catch(console.error);
