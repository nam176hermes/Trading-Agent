import { chromium } from 'playwright';

async function main() {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });

  for (const port of [3002, 3098]) {
    const page = await browser.newPage();

    // Capture EVERYTHING
    const consoleMessages = [];
    page.on('console', msg => consoleMessages.push(`[${msg.type()}] ${msg.text().substring(0, 300)}`));
    page.on('pageerror', err => consoleMessages.push(`PAGE_ERROR: ${err.message}`));

    try {
      await page.goto(`http://localhost:${port}/dashboard/history`, { waitUntil: 'load', timeout: 30000 });
      await page.waitForTimeout(5000);

      // Check React root fiber
      const fiberInfo = await page.evaluate(() => {
        const rootEl = document.getElementById('__next');
        if (!rootEl) return { error: 'no __next element' };

        const fiberKey = Object.keys(rootEl).find(k => k.startsWith('__reactFiber'));
        if (!fiberKey) return { error: 'no react fiber on __next' };

        // Walk the fiber tree looking for errors
        const errors = [];
        function walkFiber(fiber, depth = 0) {
          if (!fiber || depth > 30) return;
          if (fiber.flags !== undefined) {
            // Check for error/didCapture flags
            if (fiber.flags & 0x100) errors.push(`didCapture at depth ${depth}, tag=${fiber.tag}`);
          }
          if (fiber.child) walkFiber(fiber.child, depth + 1);
          if (fiber.sibling) walkFiber(fiber.sibling, depth);
        }
        try {
          walkFiber(rootEl[fiberKey]);
        } catch (e) {
          errors.push(`walk error: ${e.message}`);
        }

        return {
          fiberFound: !!fiberKey,
          fiberErrors: errors.length > 0 ? errors : 'none',
          tag: rootEl[fiberKey]?.tag,
          childCount: (function countFibers(f) { if (!f) return 0; return 1 + countFibers(f.child) + countFibers(f.sibling); })(rootEl[fiberKey]),
        };
      });
      console.log(`:${port} Fiber: ${JSON.stringify(fiberInfo)}`);

      // Check if window.__next_f was consumed
      const nextF = await page.evaluate(() => ({
        f_len: self.__next_f?.length ?? 'undef',
        f_exists: typeof self.__next_f !== 'undefined',
      }));
      console.log(`:${port} __next_f: ${JSON.stringify(nextF)}`);

      // Check specifically for the DecisionHistory component in the DOM
      const hasComponent = await page.evaluate(() => {
        return {
          buttons: document.querySelectorAll('button').length,
          tabs: Array.from(document.querySelectorAll('button')).map(b => b.textContent?.trim()).slice(0, 10),
          paragraphs: document.querySelectorAll('p').length,
          anyDecisions: document.body.innerHTML.includes('executive_summary') || document.body.innerHTML.includes('bull_synthesis'),
        };
      });
      console.log(`:${port} DOM: ${JSON.stringify(hasComponent)}`);

      // Show errors/warnings
      const errs = consoleMessages.filter(m => m.includes('[error]') || m.includes('PAGE_ERROR') || m.includes('[warning]'));
      if (errs.length > 0) {
        console.log(`:${port} ERRORS/WARNINGS:`);
        errs.forEach(e => console.log(`  ${e}`));
      }

    } catch (e) {
      console.log(`:${port} FAILED: ${e.message}`);
    }
  }
  await browser.close();
}
main().catch(console.error);
