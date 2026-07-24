import { chromium } from 'playwright';

async function test(url, label) {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const errors = [];
  const apiCalls = [];
  page.on('pageerror', err => errors.push(err.message));
  const allConsole = [];
  page.on('console', msg => allConsole.push(`[${msg.type()}] ${msg.text().substring(0,200)}`));
  page.on('response', res => {
    if (res.url().includes('/api/trading/')) apiCalls.push(res.url().replace(/.*\/api/, '/api'));
  });
  page.on('requestfailed', req => {
    if (req.url().includes('/api/trading/')) errors.push(`REQUEST FAILED: ${req.url()}`);
  });

  try {
    console.log(`\n${label} ${url}`);
    await page.goto(url, { waitUntil: 'load', timeout: 30000 });
    console.log('  Page loaded');

    // Check state at intervals
    for (const wait of [2, 5, 10, 15]) {
      await page.waitForTimeout(wait * 1000);
      const buttons = await page.locator('button').count();
      const loading = await page.locator('text=Loading').count();
      const rscLen = await page.evaluate(() => self.__next_f?.length ?? 0);
      console.log(`  +${wait}s: ${buttons}B api=${apiCalls.length} RSC=${rscLen} loading=${loading}`);
    }

    if (errors.length) console.log(`  ERRORS: ${errors.slice(0,5).join(' | ')}`);
    if (allConsole.length > 0) {
      const interesting = allConsole.filter(l => l.includes('rror') || l.includes('warn') || l.includes('fail'));
      if (interesting.length) console.log(`  CONSOLE: ${interesting.slice(0,5).join('\n           ')}`);
    }
  } catch (e) {
    console.log(`  FAILED: ${e.message}`);
  } finally {
    await browser.close();
  }
}

async function main() {
  // Test the most broken page: history
  for (const port of [3002, 3098, 3099]) {
    await test(`http://localhost:${port}/dashboard/history`, `:${port}`);
  }
}
main().catch(console.error);
