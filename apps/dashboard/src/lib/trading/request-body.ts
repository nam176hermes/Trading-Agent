import 'server-only';

export type BoundedBodyResult =
  | { ok: true; text: string }
  | { ok: false; reason: 'invalid' | 'too_large' };

export type BoundedJsonResult =
  | { ok: true; value: unknown }
  | { ok: false; reason: 'invalid' | 'too_large' };

type BodyMessage = Pick<Request, 'body' | 'headers'>;

async function cancelBody(message: BodyMessage): Promise<void> {
  try { await message.body?.cancel(); } catch { /* Cancellation is best-effort for untrusted streams. */ }
}

function declaredLength(message: BodyMessage, maxBytes: number): 'invalid' | 'too_large' | null {
  const declared = message.headers.get('content-length');
  if (declared === null) return null;
  if (!/^\d+$/.test(declared)) return 'invalid';
  return Number(declared) > maxBytes ? 'too_large' : null;
}

/** Reads an untrusted UTF-8 body with both declared and streamed byte limits. */
export async function readBoundedUtf8Body(
  message: BodyMessage,
  maxBytes: number,
): Promise<BoundedBodyResult> {
  const declared = declaredLength(message, maxBytes);
  if (declared) {
    await cancelBody(message);
    return { ok: false, reason: declared };
  }
  if (!message.body) return { ok: true, text: '' };

  const reader = message.body.getReader();
  const decoder = new TextDecoder('utf-8', { fatal: true });
  let bytes = 0;
  const chunks: string[] = [];
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > maxBytes) {
        try { await reader.cancel(); } catch { /* Cancellation is best-effort for untrusted streams. */ }
        return { ok: false, reason: 'too_large' };
      }
      chunks.push(decoder.decode(value, { stream: true }));
    }
    chunks.push(decoder.decode());
    return { ok: true, text: chunks.join('') };
  } catch {
    try { await reader.cancel(); } catch { /* Cancellation is best-effort for untrusted streams. */ }
    return { ok: false, reason: 'invalid' };
  } finally {
    reader.releaseLock();
  }
}

/** Parses a bounded UTF-8 JSON request body without trusting Request.json(). */
export async function readBoundedJsonBody(
  request: Request,
  maxBytes: number,
): Promise<BoundedJsonResult> {
  const body = await readBoundedUtf8Body(request, maxBytes);
  if (!body.ok) return body;
  try {
    return { ok: true, value: JSON.parse(body.text) };
  } catch {
    await cancelBody(request);
    return { ok: false, reason: 'invalid' };
  }
}
