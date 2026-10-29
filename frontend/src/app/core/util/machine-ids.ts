export const MACHINE_ID_RE = /\bM\d{3}\b/g;

const ANCHOR = /<a\b[^>]*>[\s\S]*?<\/a>/gi;
const CODE = /<(code|pre)\b[^>]*>[\s\S]*?<\/\1>/gi;

/**
 * Turns bare machine ids (M017) in rendered HTML into links to the machine page.
 * Text already inside a link or code span is left alone.
 */
export function linkMachineIds(html: string): string {
  const protectedParts: string[] = [];
  const masked = html.replace(new RegExp(`${ANCHOR.source}|${CODE.source}`, 'gi'), (m) => {
    protectedParts.push(m);
    return `\u0000${protectedParts.length - 1}\u0000`;
  });
  const linked = masked
    .split(/(<[^>]+>)/)
    .map((part) =>
      part.startsWith('<') ? part : part.replace(MACHINE_ID_RE, (id) => `<a href="/machines/${id}">${id}</a>`),
    )
    .join('');
  return linked.replace(/\u0000(\d+)\u0000/g, (_, i) => protectedParts[Number(i)]);
}
