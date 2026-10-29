import { describe, expect, it } from 'vitest';

import { linkMachineIds } from './machine-ids';

describe('linkMachineIds', () => {
  it('links bare ids in text', () => {
    expect(linkMachineIds('<p>M017 feeds M018.</p>')).toBe(
      '<p><a href="/machines/M017">M017</a> feeds <a href="/machines/M018">M018</a>.</p>',
    );
  });

  it('leaves existing links and code alone', () => {
    const html = '<a href="/x">M017</a> <code>M018</code> <pre><code>M019</code></pre>';
    expect(linkMachineIds(html)).toBe(html);
  });

  it('does not touch attribute values or longer tokens', () => {
    const html = '<span title="M017">XM0170 M17 M0170</span>';
    expect(linkMachineIds(html)).toBe(html);
  });

  it('links ids inside table cells and list items', () => {
    expect(linkMachineIds('<td>M003</td>')).toContain('<td><a href="/machines/M003">M003</a></td>');
  });
});
