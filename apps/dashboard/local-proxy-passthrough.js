import('node:http').then((http) => {

const TARGET_HOST = 'localhost';
const TARGET_PORT = 3002;
const PASSTHROUGH_PORT = 3098;

http.createServer((req, res) => {
  const options = {
    hostname: TARGET_HOST,
    port: TARGET_PORT,
    path: req.url,
    method: req.method,
    headers: { ...req.headers },
  };

  const proxy = http.request(options, (targetRes) => {
    res.writeHead(targetRes.statusCode, targetRes.headers);
    targetRes.pipe(res);
  });

  proxy.on('error', (err) => {
    console.error('Proxy error:', err.message);
    res.writeHead(502);
    res.end('Proxy error: ' + err.message);
  });

  req.pipe(proxy);
}).listen(PASSTHROUGH_PORT, () => {
  console.log(`Passthrough proxy: :${PASSTHROUGH_PORT} → localhost:${TARGET_PORT}`);
});
});
