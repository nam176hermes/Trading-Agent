import('node:http').then((http) => {

const TARGET_HOST = 'localhost';
const TARGET_PORT = 3002;
const PROXY_PORT = 3099;

http.createServer((req, res) => {
  // Block HMR — noisy WebSocket keepalives (webpack + turbopack)
  if (
    req.url.includes('__turbopack') ||
    req.url.includes('_next/hmr') ||
    req.url.includes('_next/webpack-hmr')
  ) {
    res.writeHead(404);
    res.end();
    return;
  }

  const options = {
    hostname: TARGET_HOST,
    port: TARGET_PORT,
    path: req.url,
    method: req.method,
    headers: { ...req.headers, 'accept-encoding': 'identity' },
  };

  const proxy = http.request(options, (targetRes) => {
    const chunks = [];
    targetRes.on('data', chunk => chunks.push(chunk));
    targetRes.on('end', () => {
      let body = Buffer.concat(chunks).toString();
      const ct = targetRes.headers['content-type'] || '';
      
      if (ct.includes('text/html')) {
        // Strip HMR scripts only — keep all other JS (RSC, devtools) for interactivity
        body = body.replace(/<script[^>]*hmr-client[^>]*><\/script>/gi, '');
        body = body.replace(/<script[^>]*turbopack-hmr[^>]*><\/script>/gi, '');
      }
      
      delete targetRes.headers['content-length'];
      delete targetRes.headers['transfer-encoding'];
      
      res.writeHead(targetRes.statusCode, targetRes.headers);
      res.end(body);
    });
  });

  proxy.on('error', (err) => {
    console.error('Proxy error:', err.message);
    res.writeHead(502);
    res.end('Proxy error: ' + err.message);
  });

  req.on('data', chunk => proxy.write(chunk));
  req.on('end', () => proxy.end());
}).listen(PROXY_PORT, () => {
  console.log(`Proxy: :${PROXY_PORT} → localhost:${TARGET_PORT} (trading-agent standalone)`);
});
});
