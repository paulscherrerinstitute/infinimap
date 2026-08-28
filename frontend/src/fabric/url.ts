// Which fabric is being viewed, in the query string.
//
// Separate from time/url.ts, and safe to have two writers: each reads
// `window.location.search` fresh and rewrites only the parameter it owns, so
// order between them never matters.

const FABRIC = "fabric";

/** The fabric named in the URL, or null. Blank is treated as absent. */
export function readFabric(search: string): string | null {
  const name = new URLSearchParams(search).get(FABRIC);
  return name ? name : null;
}

export function withFabric(search: string, name: string | null): string {
  const q = new URLSearchParams(search);
  if (name) q.set(FABRIC, name);
  else q.delete(FABRIC);
  const s = q.toString();
  return s ? `?${s}` : "";
}
