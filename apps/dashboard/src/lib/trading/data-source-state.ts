export type DataSourcesAvailability = 'LOADING' | 'AVAILABLE' | 'UNAVAILABLE';
export type DataSourceHealth = 'active' | 'error' | 'unknown';

export interface DataSourceStatusEntry {
  id: string;
  name: string;
  status: DataSourceHealth;
  latency: number | null;
  rateLimitRemaining: number | null;
  lastUpdate: string | null;
  error: string | null;
}

export interface DataSourcesState {
  availability: DataSourcesAvailability;
  data: DataSourceStatusEntry[] | null;
}

export interface DataSourcesSummary {
  availability: DataSourcesAvailability;
  sources: DataSourceStatusEntry[] | null;
  counts: { active: number; error: number; unknown: number } | null;
}

export type DataSourcesFetch = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

export const INITIAL_DATA_SOURCES_STATE: Readonly<DataSourcesState> = Object.freeze({
  availability: 'LOADING',
  data: null,
});

export const UNAVAILABLE_DATA_SOURCES_STATE: Readonly<DataSourcesState> = Object.freeze({
  availability: 'UNAVAILABLE',
  data: null,
});

type JsonObject = Record<string, unknown>;
const API_KEYS = [
  'id', 'name', 'status', 'latency_ms', 'rate_limit_remaining', 'last_check',
  'error_message',
] as const;
const API_STATUSES = new Set(['ok', 'error', 'unknown']);

function isObject(value: unknown): value is JsonObject {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function hasExactKeys(value: JsonObject, keys: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length
    && actual.every((key, index) => key === expected[index]);
}

function boundedString(value: unknown, maxLength = 512): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= maxLength;
}

function nullableNonNegativeNumber(value: unknown): value is number | null {
  return value === null
    || (typeof value === 'number' && Number.isFinite(value) && value >= 0);
}

function nullableNonNegativeInteger(value: unknown): value is number | null {
  return value === null
    || (Number.isSafeInteger(value) && (value as number) >= 0);
}

function nullableIsoDate(value: unknown): value is string | null {
  return value === null
    || (typeof value === 'string'
      && value.length <= 35
      && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(value)
      && Number.isFinite(Date.parse(value)));
}

function parseEntry(value: unknown): DataSourceStatusEntry | null {
  if (!(isObject(value)
    && hasExactKeys(value, API_KEYS)
    && boundedString(value.id, 128)
    && boundedString(value.name, 256)
    && typeof value.status === 'string' && API_STATUSES.has(value.status)
    && nullableNonNegativeNumber(value.latency_ms)
    && nullableNonNegativeInteger(value.rate_limit_remaining)
    && nullableIsoDate(value.last_check)
    && (value.error_message === null || boundedString(value.error_message, 2_048)))) return null;

  return {
    id: value.id,
    name: value.name,
    status: value.status === 'ok' && value.last_check !== null
      ? 'active'
      : value.status === 'error'
        ? 'error'
        : 'unknown',
    latency: value.latency_ms,
    rateLimitRemaining: value.rate_limit_remaining,
    lastUpdate: value.last_check,
    error: value.error_message,
  };
}

export function parseDataSourcesPayload(value: unknown): DataSourceStatusEntry[] | null {
  if (!Array.isArray(value) || value.length > 1_000) return null;
  const parsed = value.map(parseEntry);
  return parsed.some((entry) => entry === null)
    ? null
    : parsed as DataSourceStatusEntry[];
}

export async function loadDataSourcesState(
  fetcher: DataSourcesFetch = fetch,
  {
    timeoutMs = 5_000,
    signal,
  }: { timeoutMs?: number; signal?: AbortSignal } = {},
): Promise<Readonly<DataSourcesState>> {
  if (signal?.aborted) return UNAVAILABLE_DATA_SOURCES_STATE;
  const controller = new AbortController();
  const abortFromCaller = () => controller.abort();
  signal?.addEventListener('abort', abortFromCaller, { once: true });
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetcher('/api/trading/data-sources', {
      cache: 'no-store',
      headers: { accept: 'application/json' },
      signal: controller.signal,
    });
    if (!response.ok) return UNAVAILABLE_DATA_SOURCES_STATE;
    const data = parseDataSourcesPayload(await response.json());
    return data === null
      ? UNAVAILABLE_DATA_SOURCES_STATE
      : { availability: 'AVAILABLE', data };
  } catch {
    return UNAVAILABLE_DATA_SOURCES_STATE;
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener('abort', abortFromCaller);
  }
}

export function summarizeDataSources(
  state: Readonly<DataSourcesState>,
): DataSourcesSummary {
  if (state.availability !== 'AVAILABLE' || state.data === null) {
    return { availability: state.availability, sources: null, counts: null };
  }
  return {
    availability: 'AVAILABLE',
    sources: state.data,
    counts: {
      active: state.data.filter((source) => source.status === 'active').length,
      error: state.data.filter((source) => source.status === 'error').length,
      unknown: state.data.filter((source) => source.status === 'unknown').length,
    },
  };
}
