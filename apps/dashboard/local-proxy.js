import('node:http').then((http) => {

const TARGET_HOST = 'localhost';
const TARGET_PORT = 3002;
const PORT = 3099;

http.createServer((req, res) => {
  // Block HMR and dev WebSocket endpoints
  if (req.url.includes('webpack-hmr') || req.url.includes('_next/webpack')) {
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
        body = body.replace(/<script[^>]*hmr-client[^>]*><\/script>/gi, '');
        body = body.replace(/<script[^>]*next-devtools[^>]*><\/script>/gi, '');
        body = body.replace(/<script[^>]*react-server-dom-turbopack[^>]*><\/script>/gi, '');
      }
      
      // Remove problematic headers
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

  req.pipe(proxy);
}).listen(PORT, () => {
  console.log(`Proxy on ${PORT} -> ${TARGET_HOST}:${TARGET_PORT}`);
});
});
