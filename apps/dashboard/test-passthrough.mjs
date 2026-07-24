import { chromium } from 'playwright';

async function test(url) {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const errors = [];
  const apiCalls = [];
  page.on('pageerror', err => errors.push(err.message));
  page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text().substring(0,200)); });
  page.on('response', res => {
    if (res.url().includes('/api/trading/')) apiCalls.push(1);
  });

  try {
    await page.goto(url, { waitUntil: 'load', timeout: 30000 });
    await page.waitForTimeout(5000);
    const body = await page.textContent('body');
    const loading = body.includes('Loading') ? 'LOADING' : 'ok';
    const buttons = await page.locator('button').count();
    const rscLen = await page.evaluate(() => self.__next_f?.length ?? 0);
    console.log(`${url.split('/').pop().padEnd(22)} ${buttons}B ${rscLen}RSC api=${apiCalls.length} ${loading} errors=${errors.length}`);
    if (errors.length) console.log(`  ERR: ${errors.slice(0,3).join(' | ')}`);
  } catch (e) {
    console.log(`${url.split('/').pop()}: FAILED - ${e.message}`);
  } finally {
    await browser.close();
  }
}

async function main() {
  console.log('=== Passthrough :3098 (no stripping, raw pipe) ===');
  for (const p of ['/dashboard/history','/dashboard','/dashboard/signals','/dashboard/risk','/dashboard/plan']) {
    await test(`http://localhost:3098${p}`);
  }
}
main().catch(console.error);
