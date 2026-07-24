export async function readAuthenticatedResponse(response: Response): Promise<boolean> {
  if (!response.ok) return false;

  try {
    const status: unknown = await response.json();
    return Boolean(
      status
      && typeof status === 'object'
      && !Array.isArray(status)
      && (status as { authenticated?: unknown }).authenticated === true,
    );
  } catch {
    return false;
  }
}
