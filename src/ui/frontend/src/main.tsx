import React from 'react';
import ReactDOM from 'react-dom/client';
import { App } from './App';
import { MechanicFlow } from './components/mechanic/MechanicFlow';
import { SignInGate } from './components/mechanic/SignInGate';
import './index.css';

const rootElement = document.getElementById('root');
if (!rootElement) throw new Error('index.html is missing the #root element');

ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
    <SignInGate>
      <MechanicFlow>
        <App />
      </MechanicFlow>
    </SignInGate>
  </React.StrictMode>
);
