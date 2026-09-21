/**
 * T2-7 render harness — mounts SettingsAttributionPanel.tsx and reports the
 * painted DOM as JSON on stdout.
 *
 * Driven by tests/unit/test_t2_7_attribution_render.py. Renders with a REAL
 * react-dom mount against the REAL payload emitted by
 * src/ui/data_attribution_catalog.py, so useEffect runs and the panel reaches
 * its loaded state.
 *
 * Why a Node harness exists: `vite build` cannot run under the current file
 * sandbox (esbuild's service subprocess hits `spawn EPERM`), so the "the credit
 * is actually rendered, not merely present in source" claim is proven by
 * painting the component rather than asserted in prose. Uses the in-process
 * `typescript` transpiler and an in-process DOM shim on purpose — no
 * subprocesses, so it works under the sandbox.
 *
 * Usage:
 *   node render_attribution_panel.cjs <payload.json> <frontend-dir>
 */
const fs = require('node:fs');
const path = require('node:path');

// argv[3] (frontend dir) lets the caller run the harness from anywhere; fall
// back to cwd for manual invocation.
const frontend = process.argv[3] ? path.resolve(process.argv[3]) : process.cwd();
const ts = require(path.join(frontend, 'node_modules', 'typescript'));

const srcPath = path.join(
  frontend,
  'src',
  'components',
  'settings',
  'SettingsAttributionPanel.tsx',
);
const code = fs.readFileSync(srcPath, 'utf8');

// TSX -> CommonJS, entirely in-process.
const transpiled = ts.transpileModule(code, {
  compilerOptions: {
    jsx: ts.JsxEmit.React,
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2020,
    esModuleInterop: true,
  },
  fileName: 'SettingsAttributionPanel.tsx',
});

const payload = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));

const staging = path.join(frontend, 'node_modules', '.t27-staging');
fs.mkdirSync(staging, { recursive: true });

const iconStub = `
const React = require('react');
const mk = () => (props) => React.createElement('span', { 'data-icon': 'stub' });
module.exports = new Proxy({}, { get: (_t, key) => (key === '__esModule' ? false : mk()) });
`;
fs.writeFileSync(path.join(staging, 'lucide-react.js'), iconStub);

const bridgeStub = `
const payload = ${JSON.stringify(payload)};
class DesktopBridge {
  static async getDataAttributions() { return payload; }
}
module.exports = { DesktopBridge };
`;
fs.writeFileSync(path.join(staging, 'bridge.js'), bridgeStub);

const panelModule = path.join(staging, 'panel.cjs');
fs.writeFileSync(
  panelModule,
  transpiled.outputText
    .replace(/require\("lucide-react"\)/g, `require(${JSON.stringify(path.join(staging, 'lucide-react.js'))})`)
    .replace(/require\("\.\.\/\.\.\/services\/bridge"\)/g, `require(${JSON.stringify(path.join(staging, 'bridge.js'))})`),
);

const Panel = require(panelModule).SettingsAttributionPanel;
const React = require(path.join(frontend, 'node_modules', 'react'));
const ReactDOMClient = require(path.join(frontend, 'node_modules', 'react-dom', 'client'));

/**
 * Minimal DOM good enough for react-dom/client + this component: elements with
 * text, attributes, children, classList, style, and event listeners. React only
 * needs createElement/appendChild/insertBefore/removeChild/removeAttribute/
 * setAttribute/addEventListener, which is all we implement.
 */
class El {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.nodeType = 1;
    this.childNodes = [];
    this.parentNode = null;
    this.attributes = {};
    this.style = {};
    this._listeners = {};
    this.ownerDocument = null;
    this.namespaceURI = 'http://www.w3.org/1999/xhtml';
  }
  setAttribute(k, v) {
    this.attributes[k] = String(v);
    if (k === 'style') this.style.cssText = String(v);
  }
  getAttribute(k) {
    return Object.prototype.hasOwnProperty.call(this.attributes, k)
      ? this.attributes[k]
      : null;
  }
  removeAttribute(k) {
    delete this.attributes[k];
  }
  hasAttribute(k) {
    return Object.prototype.hasOwnProperty.call(this.attributes, k);
  }
  appendChild(child) {
    return this.insertBefore(child, null);
  }
  insertBefore(child, ref) {
    if (child.parentNode) child.parentNode.removeChild(child);
    if (ref == null) {
      this.childNodes.push(child);
    } else {
      const i = this.childNodes.indexOf(ref);
      this.childNodes.splice(i < 0 ? this.childNodes.length : i, 0, child);
    }
    child.parentNode = this;
    return child;
  }
  removeChild(child) {
    const i = this.childNodes.indexOf(child);
    if (i >= 0) this.childNodes.splice(i, 1);
    child.parentNode = null;
    return child;
  }
  addEventListener(type, fn) {
    (this._listeners[type] = this._listeners[type] || []).push(fn);
  }
  removeEventListener(type, fn) {
    const l = this._listeners[type] || [];
    const i = l.indexOf(fn);
    if (i >= 0) l.splice(i, 1);
  }
  get firstChild() {
    return this.childNodes[0] || null;
  }
  contains(node) {
    if (node === this) return true;
    return this.childNodes.some((c) => c.contains && c.contains(node));
  }
  focus() {}
  blur() {}
  get ownerDocument() {
    return this._ownerDocument || doc;
  }
  set ownerDocument(v) {
    this._ownerDocument = v;
  }
  get textContent() {
    if (this.nodeType === 3) return this.nodeValue;
    return this.childNodes.map((c) => c.textContent).join('');
  }
  set textContent(v) {
    this.childNodes = [];
    if (v !== '' && v != null) {
      const t = new Text(v);
      t.parentNode = this;
      this.childNodes.push(t);
    }
  }
  get nodeValue() {
    return this._nodeValue || '';
  }
  set nodeValue(v) {
    this._nodeValue = v == null ? '' : String(v);
  }
  get innerHTML() {
    return serialize(this);
  }
  get outerHTML() {
    return serialize(this);
  }
  get classList() {
    const self = this;
    const get = () => (self.attributes.class || '').split(/\s+/).filter(Boolean);
    return {
      add: (c) => self.setAttribute('class', [...new Set([...get(), c])].join(' ')),
      remove: (c) => self.setAttribute('class', get().filter((x) => x !== c).join(' ')),
      contains: (c) => get().includes(c),
    };
  }
}

class Text {
  constructor(v) {
    this.nodeType = 3;
    this.nodeValue = String(v);
    this.parentNode = null;
    this.childNodes = [];
  }
  get textContent() {
    return this.nodeValue;
  }
}

function serialize(node) {
  if (!node) return '';
  if (node.nodeType === 3) return escapeHtml(node.nodeValue);
  const attrs = Object.entries(node.attributes || {})
    .map(([k, v]) => ` ${k}="${escapeHtml(v)}"`)
    .join('');
  const kids = (node.childNodes || []).map(serialize).join('');
  return `<${node.tagName.toLowerCase()}${attrs}>${kids}</${node.tagName.toLowerCase()}>`;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

const doc = {
  createElement: (tag) => {
    const e = new El(tag);
    e.ownerDocument = doc;
    return e;
  },
  createElementNS: (_ns, tag) => doc.createElement(tag),
  createTextNode: (v) => new Text(v),
  createComment: () => new Text(''),
  addEventListener() {},
  removeEventListener() {},
  documentElement: null,
  head: null,
  body: null,
};
doc.documentElement = doc.createElement('html');
doc.head = doc.createElement('head');
doc.body = doc.createElement('body');
doc.documentElement.appendChild(doc.head);
doc.documentElement.appendChild(doc.body);

global.window = global;
global.document = doc;
global.Node = El;
global.HTMLElement = El;
global.Element = El;
global.Text = Text;
global.Document = El;
global.Event = class Event {
  constructor(type, init) {
    this.type = type;
    Object.assign(this, init || {});
  }
};
global.MouseEvent = global.Event;
global.HTMLIFrameElement = class HTMLIFrameElement extends El {};
global.HTMLInputElement = class HTMLInputElement extends El {};
global.HTMLTextAreaElement = class HTMLTextAreaElement extends El {};
global.HTMLSelectElement = class HTMLSelectElement extends El {};
global.HTMLButtonElement = class HTMLButtonElement extends El {};
global.HTMLAnchorElement = class HTMLAnchorElement extends El {};
global.getComputedStyle = () => ({ getPropertyValue: () => '' });
global.requestAnimationFrame = (cb) => setTimeout(cb, 0);
global.cancelAnimationFrame = (id) => clearTimeout(id);
global.navigator = { userAgent: 'node' };
global.IS_REACT_ACT_ENVIRONMENT = false;

// React's commit phase probes the document for the active element and walks
// ancestors for iframe ownership; give it inert answers.
doc.activeElement = doc.body;
doc.defaultView = global;
doc.documentElement.contains = () => true;
doc.body.contains = (n) => n != null;

const container = doc.createElement('div');
doc.body.appendChild(container);

(async () => {
  const root = ReactDOMClient.createRoot(container);
  root.render(React.createElement(Panel, {}));

  // Let the DesktopBridge promise chain + effects settle.
  await new Promise((r) => setTimeout(r, 250));

  const html = serialize(container);

  const report = {
    panel_mounted: html.includes('data-testid="settings-attribution-panel"'),
    sitrak_credit_rendered_verbatim:
      html.includes('Источник: МегаДата / megadata.pro — CC BY 4.0') &&
      html.includes('https://creativecommons.org/licenses/by/4.0/'),
    verbatim_block_count: (
      html.match(/data-testid="attribution-verbatim-text"/g) || []
    ).length,
    source_card_count: (html.match(/data-testid="attribution-source"/g) || [])
      .length,
    required_badge_count: (html.match(/ATIF ZORUNLU/g) || []).length,
    megadata_occurrences: (html.match(/МегаДата/g) || []).length,
    sitrak_commit_rendered: html.includes(
      'fdb0c0d9daf0643975b0ff62e0ff69ef9c07f742',
    ),
    licenses_rendered:
      html.includes('CC-BY-4.0') &&
      html.includes('Apache-2.0') &&
      html.includes('MIT') &&
      html.includes('CC0-1.0'),
    pinned_commit_cells: (
      html.match(/data-testid="attribution-pinned-commit"/g) || []
    ).length,
    source_url_cells: (html.match(/data-testid="attribution-source-url"/g) || [])
      .length,
    collapsed_licence_bodies: (
      html.match(/data-testid="attribution-source-text"/g) || []
    ).length,
    sitrak_in_collapsed_body: false,
    offline_badge: html.includes('Çevrimdışı'),
    html_bytes: html.length,
  };
  report.sitrak_in_collapsed_body = report.collapsed_licence_bodies > 0;
  report.credit_needs_no_click =
    report.sitrak_credit_rendered_verbatim &&
    report.collapsed_licence_bodies === 0;

  // The painted DOM goes to stderr (diagnostic); stdout carries ONLY the JSON
  // report so the caller can parse it directly.
  process.stderr.write(html);
  process.stdout.write(JSON.stringify(report, null, 2) + '\n');
})();
