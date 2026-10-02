import React from 'react';
import ReactDOM from 'react-dom/client';
import { AppRoot } from './components/shell/AppRoot';
import { applyTheme } from './components/shell/theme';
import { lang } from './components/mechanic/text';
import './index.css';

const rootElement = document.getElementById('root');
if (!rootElement) throw new Error('index.html is missing the #root element');

// Apply the remembered theme and language before the first paint.
applyTheme();
document.documentElement.lang = lang();

ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
    <AppRoot />
  </React.StrictMode>
);
