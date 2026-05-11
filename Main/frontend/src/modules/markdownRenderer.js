// Markdown + math renderer.
//
// Single `$...$` is intentionally NOT a math delimiter: financial prose
// contains currency ("$1.00", "$13.63 billion") that would otherwise pair
// across sentences and turn text into math. Recognized math forms are
// `\(...\)`, `\[...\]`, and `$$...$$`.

import markdownIt from 'markdown-it';
import texmath from 'markdown-it-texmath';

// Normalize math strings so KaTeX does not choke on uncommon unicode spacing/hyphen characters
function normalizeMathInput(mathText) {
  if (!mathText) {
    return mathText;
  }
  return mathText
    .replace(/\u202f/g, ' ') // narrow no-break space → regular space
    .replace(/\u2011/g, '-'); // non-breaking hyphen → regular hyphen
}

const KATEX_RENDER_OPTIONS = {
  delimiters: [
    { left: '$$', right: '$$', display: true },
    { left: '\\[', right: '\\]', display: true },
    { left: '\\(', right: '\\)', display: false },
  ],
  throwOnError: false,
  errorColor: '#cc0000',
  strict: false,
  trust: true,
  macros: {
    '\\Δ': '\\Delta',
    '\\σ': '\\sigma',
    '\\ν': '\\nu',
    '\\ρ': '\\rho',
    '\\Γ': '\\Gamma',
    '\\Θ': '\\theta',
  },
  preProcess: normalizeMathInput,
};

// Custom delimiter preset for `$$...$$` only. Used together with the
// upstream `brackets` preset (which provides `\(...\)` and `\[...\]`).
// We deliberately omit any single-`$` inline rule — see file header.
function ensureFinsearchDelimiters() {
  if (texmath.rules.finsearch) return;
  texmath.rules.finsearch = {
    inline: [
      {
        name: 'fs_math_inline_double',
        rex: /\${2}([^$]*?[^\\])\${2}/gy,
        tmpl: '<eqn>$1</eqn>',
        tag: '$$',
        displayMode: true,
        pre: texmath.$_pre,
        post: texmath.$_post,
      },
    ],
    block: [
      {
        name: 'fs_math_block_dollars_eqno',
        rex: /\${2}([^$]*?[^\\])\${2}\s*?\(([^)\s]+?)\)/gmy,
        tmpl: '<section class="eqno"><eqn>$1</eqn><span>($2)</span></section>',
        tag: '$$',
      },
      {
        name: 'fs_math_block_dollars',
        rex: /\${2}([^$]*?[^\\])\${2}/gmy,
        tmpl: '<section><eqn>$1</eqn></section>',
        tag: '$$',
      },
    ],
  };
}

// Defense in depth against LLMs that disregard the "no single-$" prompt.
// One alternation pass classifies each match as code (passthrough), math
// region (escape internal `$` so KaTeX sees `\$` not bare `$`), or bare
// currency `$<digit>` (escape so markdown emits literal text).
const CURRENCY_ESCAPE_RE = /```[\s\S]*?```|`[^`\n]+`|\$\$[\s\S]*?\$\$|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]|(?<![\\$])\$(?=\d)/g;
const UNESCAPED_DOLLAR_RE = /(?<!\\)\$/g;

function escapeDollarsInsideMath(mathRegion) {
  // `$$...$$`: keep boundary `$$` intact, escape only internal bare `$`
  // (otherwise KaTeX errors when math content mentions currency like
  // `\(Assets ($106.62B) = ...\)`).
  if (mathRegion.startsWith('$$')) {
    return '$$' + mathRegion.slice(2, -2).replace(UNESCAPED_DOLLAR_RE, '\\$') + '$$';
  }
  // `\(...\)` and `\[...\]` have backslash-bracket boundaries, no `$` to preserve.
  return mathRegion.replace(UNESCAPED_DOLLAR_RE, '\\$');
}

export function escapeCurrencyDollars(text) {
  if (!text) return text;
  return text.replace(CURRENCY_ESCAPE_RE, (m) => {
    if (m === '$') return '\\$';        // bare currency outside any region
    if (m[0] === '`') return m;         // fenced or inline code: passthrough
    return escapeDollarsInsideMath(m);  // math region: escape internal `$`
  });
}

// Create and configure the markdown-it instance
function createMarkdownRenderer() {
  ensureFinsearchDelimiters();

  const md = markdownIt({
    html: true,
    linkify: true,
    typographer: false,
    breaks: false,
  });

  // texmath claims math regions before markdown emphasis parsing, so `*`
  // and `_` inside formulas survive. We hand off the raw TeX wrapped in
  // bracket delimiters for KaTeX auto-render to pick up downstream.
  md.use(texmath, {
    engine: {
      renderToString: (tex, options) =>
        options.displayMode ? `\\[${tex}\\]` : `\\(${tex}\\)`,
    },
    delimiters: ['brackets', 'finsearch'],
  });

  return md;
}

let markdownRenderer = null;

function getMarkdownRenderer() {
  if (!markdownRenderer) {
    markdownRenderer = createMarkdownRenderer();
  }
  return markdownRenderer;
}

function sanitizeHtml(html) {
  const template = document.createElement('template');
  template.innerHTML = html;

  const forbiddenSelectors = 'script,style,iframe,object,embed,link,meta';
  template.content.querySelectorAll(forbiddenSelectors).forEach((node) => node.remove());

  const walker = document.createTreeWalker(template.content, NodeFilter.SHOW_ELEMENT, null, false);
  while (walker.nextNode()) {
    const element = walker.currentNode;
    Array.from(element.attributes).forEach((attr) => {
      if (attr.name.startsWith('on')) {
        element.removeAttribute(attr.name);
      }
    });
  }

  const commentWalker = document.createTreeWalker(template.content, NodeFilter.SHOW_COMMENT, null, false);
  const comments = [];
  while (commentWalker.nextNode()) {
    comments.push(commentWalker.currentNode);
  }
  comments.forEach((node) => node.remove());

  return template.innerHTML;
}

function applyLinkAttributes(element) {
  const links = element.querySelectorAll('a');
  links.forEach((link) => {
    link.setAttribute('target', '_blank');
    link.setAttribute('rel', 'noopener noreferrer');
  });
}

function renderMath(element) {
  if (typeof globalThis.renderMathInElement !== 'function') {
    console.warn('KaTeX auto-render is not available.');
    return;
  }

  globalThis.renderMathInElement(element, KATEX_RENDER_OPTIONS);
}

function applyPrefix(html, prefixLabel) {
  if (!prefixLabel) {
    return html;
  }
  return `<strong>${prefixLabel}:</strong> ${html}`;
}

function render(markdown, options = {}) {
  const { stabilizeStreaming = false } = options;

  let processedMarkdown = escapeCurrencyDollars(markdown || '');

  if (stabilizeStreaming) {
    // Close unmatched math delimiters mid-stream so a half-streamed formula
    // doesn't leak its `*` and `_` into emphasis parsing while the user waits.
    const openParen = (processedMarkdown.match(/\\\(/g) || []).length;
    const closeParen = (processedMarkdown.match(/\\\)/g) || []).length;
    if (openParen > closeParen) {
      processedMarkdown += '\\)'.repeat(openParen - closeParen);
    }

    const openBrack = (processedMarkdown.match(/\\\[/g) || []).length;
    const closeBrack = (processedMarkdown.match(/\\\]/g) || []).length;
    if (openBrack > closeBrack) {
      processedMarkdown += '\\]'.repeat(openBrack - closeBrack);
    }

    const doubleDollarCount = (processedMarkdown.match(/\$\$/g) || []).length;
    if (doubleDollarCount % 2 !== 0) {
      processedMarkdown += '$$';
    }

    const fenceCount = (processedMarkdown.match(/```/g) || []).length;
    if (fenceCount % 2 !== 0) {
      processedMarkdown += '\n```';
    }
  }

  const md = getMarkdownRenderer();
  return sanitizeHtml(md.render(processedMarkdown));
}

export function renderMarkdownContent(targetElement, rawText, options = {}) {
  if (!targetElement) {
    return;
  }

  const { prefixLabel = 'FinSearch' } = options;

  try {
    const html = render(rawText);
    targetElement.innerHTML = applyPrefix(html, prefixLabel);
    targetElement.dataset.renderState = 'final';

    applyLinkAttributes(targetElement);
    renderMath(targetElement);
  } catch (error) {
    console.error('Error rendering markdown:', error);
    targetElement.textContent = `${prefixLabel ? `${prefixLabel}: ` : ''}${rawText || ''}`;
  }
}

export function renderStreamingPreview(targetElement, rawText, options = {}) {
  if (!targetElement) {
    return;
  }

  const { prefixLabel = 'FinSearch' } = options;

  try {
    const html = render(rawText, { stabilizeStreaming: true });
    targetElement.innerHTML = applyPrefix(html, prefixLabel);
    targetElement.dataset.renderState = 'streaming';

    applyLinkAttributes(targetElement);
    renderMath(targetElement);
  } catch (error) {
    console.error('Error rendering streaming markdown:', error);
    targetElement.textContent = `${prefixLabel ? `${prefixLabel}: ` : ''}${rawText || ''}`;
  }
}
