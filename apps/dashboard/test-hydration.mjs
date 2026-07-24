import { chromium } from 'playwright';

const PAGES = ['/dashboard', '/dashboard/signals', '/dashboard/risk', '/dashboard/history', '/dashboard/plan'];

async function testPage(baseUrl, path) {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();

  const errors = [];
  const warnings = [];
  const logs = [];

  page.on('console', msg => {
    if (msg.type() === 'error') errors.push(msg.text());
    else if (msg.type() === 'warning') warnings.push(msg.text());
    else logs.push(`[${msg.type()}] ${msg.text()}`);
  });

  page.on('pageerror', err => errors.push(`PAGE_ERROR: ${err.message}`));

  try {
    const url = `${baseUrl}${path}`;
    console.log(`\n=== Testing: ${url} ===`);
    const response = await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 });
    console.log(`Status: ${response.status()}`);
    console.log(`Content-Type: ${response.headers()['content-type']}`);

    // Wait a bit for hydration
    await page.waitForTimeout(3000);

    // Check for loading indicators
    const loadingText = await page.textContent('body');
    const hasLoading = loadingText.includes('Loading...') || loadingText.includes('Loading');

    // Check for common interactive elements
    const buttons = await page.locator('button').count();
    const links = await page.locator('a').count();
    const headings = await page.locator('h1, h2').count();
    const selects = await page.locator('select').count();

    console.log(`Buttons: ${buttons}, Links: ${links}, Headings: ${headings}, Selects: ${selects}`);
    console.log(`Has loading text: ${hasLoading}`);

    if (errors.length > 0) {
      console.log(`ERRORS (${errors.length}):`);
      errors.slice(0, 10).forEach(e => console.log(`  - ${e}`));
    } else {
      console.log('No errors');
    }

    if (warnings.length > 0) {
      console.log(`WARNINGS (${warnings.length}):`);
      warnings.slice(0, 5).forEach(w => console.log(`  - ${w}`));
    }

    return { errors, warnings, buttons, headings, hasLoading, status: response.status() };
  } catch (e) {
    console.log(`FAILED: ${e.message}`);
    return { errors: [e.message], status: 0 };
  } finally {
    await browser.close();
  }
}

async function main() {
  console.log('========================================');
  console.log('TESTING DIRECT (:3002)');
  console.log('========================================');
  for (const page of PAGES) {
    await testPage('http://localhost:3002', page);
  }

  console.log('\n\n========================================');
  console.log('TESTING PROXIED (:3099)');
  console.log('========================================');
  for (const page of PAGES) {
    await testPage('http://localhost:3099', page);
  }
}

main().catch(console.error);
