import { chromium } from 'playwright';

async function test(url, label) {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  const errors = [];
  const apiCalls = [];
  page.on('pageerror', err => errors.push(err.message));
  page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()); });
  page.on('response', res => {
    if (res.url().includes('/api/trading/')) apiCalls.push(`${res.status()} ${res.url().replace(/.*\/api/, '/api')}`);
  });

  try {
    await page.goto(url, { waitUntil: 'load', timeout: 30000 });
    await page.waitForTimeout(5000);

    const body = await page.textContent('body');
    const hasLoading = body.includes('Loading');
    const buttons = await page.locator('button').count();
    const rscLen = await page.evaluate(() => self.__next_f?.length ?? 0);

    console.log(`${label}: ${buttons}B ${rscLen}RSC api=[${apiCalls.join(',')}] loading=${hasLoading}`);
    if (errors.length) console.log(`  ERRORS: ${errors.slice(0,3).join(' | ')}`);
  } catch (e) {
    console.log(`${label}: FAILED - ${e.message}`);
  } finally {
    await browser.close();
  }
}

async function main() {
  for (const path of ['/dashboard/history', '/dashboard', '/dashboard/signals', '/dashboard/risk', '/dashboard/plan']) {
    await test(`http://localhost:3002${path}`, `DIRECT  ${path}`);
    await test(`http://localhost:3099${path}`, `PROXIED ${path}`);
  }
}
main().catch(console.error);
