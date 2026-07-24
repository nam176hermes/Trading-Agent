import { chromium } from 'playwright';

async function main() {
  const browser = await chromium.launch({ headless: true });

  for (const port of [3002, 3098]) {
    const page = await browser.newPage();
    page.on('pageerror', err => console.log(`:${port} PAGE_ERR: ${err.message}`));

    try {
      await page.goto(`http://localhost:${port}/dashboard/history`, { waitUntil: 'load', timeout: 30000 });
      await page.waitForTimeout(3000);

      // Capture the innerHTML before/after
      const html = await page.evaluate(() => document.body.innerHTML);

      // Find what's different
      const hasDecisionHistory = html.includes('Decision History');
      const hasStructuredDecisions = html.includes('Structured Decisions');
      const hasSignalTimeline = html.includes('Signal Timeline');
      console.log(`:${port} bodyHTML=${html.length} hasDecisionHistory=${hasDecisionHistory} hasStructured=${hasStructuredDecisions} hasTimeline=${hasSignalTimeline}`);

      // Dump the end of the body HTML
      console.log(`  Last 500 chars: ${html.substring(Math.max(0, html.length - 500)).replace(/\n/g, ' ')}`);

    } catch (e) {
      console.log(`:${port} FAILED: ${e.message}`);
    }
  }
  await browser.close();
}
main().catch(console.error);
