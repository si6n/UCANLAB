import React from 'react';
import ReactDOM from 'react-dom/client';
import { App } from './App';
import { SignInGate } from './components/mechanic/SignInGate';
import './index.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <SignInGate>
      <App />
    </SignInGate>
  </React.StrictMode>
);
