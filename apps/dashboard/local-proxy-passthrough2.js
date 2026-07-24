import('node:http').then((http) => {

const TARGET_HOST = 'localhost';
const TARGET_PORT = 3002;
const FIXED_PROXY_PORT = 3097;

http.createServer((req, res) => {
  const options = {
    hostname: TARGET_HOST,
    port: TARGET_PORT,
    path: req.url,
    method: req.method,
    // Forward the correct host header to Next.js
    headers: { ...req.headers, host: `${TARGET_HOST}:${TARGET_PORT}` },
  };

  const proxy = http.request(options, (targetRes) => {
    // Forward response headers as-is
    res.writeHead(targetRes.statusCode, targetRes.headers);
    targetRes.pipe(res);
  });

  proxy.on('error', (err) => {
    console.error('Proxy error:', err.message);
    res.writeHead(502);
    res.end('Proxy error: ' + err.message);
  });

  req.pipe(proxy);
}).listen(FIXED_PROXY_PORT, () => {
  console.log(`Fixed proxy: :${FIXED_PROXY_PORT} → localhost:${TARGET_PORT} (host: localhost:3002)`);
});
});
